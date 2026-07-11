"""The local review server.

A small ``http.server`` handler over a ``ReviewSession``: no framework, no
third-party dependency, consistent with keeping everything around the matcher on
the standard library. The server binds the loopback interface and, under a policy
pack that requires local targets, refuses any non-loopback bind, fail-closed, so
the review surface cannot become an egress path for client PII.

Binding to loopback alone is not enough: any page a reviewer has open in the
same browser can still point a request at ``http://127.0.0.1:PORT/...``
(cross-site request forgery), and DNS rebinding lets a hostile page get a
browser to send a request whose ``Host`` header the attacker controls while
the connection still lands on loopback. Three checks close that gap, all
fail-closed: the ``Host`` header must name exactly the bound host and port
(FIX-01), a POST's ``Origin`` header, if present, must match the server's own
origin, and every POST must carry the session's per-run token, which never
leaves the rendered page.

Request handling is split from socket binding on purpose. ``handle_get`` and
``handle_post`` return a ``_Response`` from the session and a ``RequestContext``
alone, so they are tested without a socket; ``build_server`` binds a real
loopback socket for the end-to-end path and the CLI.
"""

from __future__ import annotations

import hmac
import webbrowser
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.review import render
from constituent_reconciler.review.session import APPROVED, REJECTED, ReviewSession

# Interfaces that keep traffic on the machine. A non-loopback bind under the DV
# pack is refused, mirroring the connector local-target gate.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_VERDICT_FORM = {"approve": APPROVED, "reject": REJECTED}


class HeaderSource(Protocol):
    """The subset of a request's headers the pure handlers need.

    ``http.server`` hands the real handler an ``email.message.Message``, whose
    ``get`` is already case-insensitive; a plain ``dict`` satisfies this
    protocol too, which is what the unit tests pass.
    """

    def get(self, name: str, default: str | None = None, /) -> str | None: ...


@dataclass(frozen=True)
class RequestContext:
    """Per-request facts needed to resist forgery, independent of the socket.

    ``authority`` is the ``host:port`` the server is bound to (what a correct
    ``Host`` header must equal); ``headers`` is the incoming request's header
    map.
    """

    authority: str
    headers: HeaderSource


class _Response:
    __slots__ = ("status", "body", "location")

    def __init__(self, status: int, body: str = "", location: str | None = None) -> None:
        self.status = status
        self.body = body
        self.location = location


def _apply_command(session: ReviewSession) -> str:
    return f"reconcile apply --config <recipe.toml> --decisions {session.decisions_path}"


def _host_is_valid(context: RequestContext) -> bool:
    """The Host header must name exactly the address this server is bound to.

    A mismatch means either a stale/forged Host header or a DNS-rebinding
    attempt: a hostile DNS name that first resolves to a public IP (passing a
    browser's same-origin checks) and then re-resolves to 127.0.0.1, so a page
    loaded from that name can address the loopback server. Pinning the exact
    authority closes that gap without needing to enumerate attacker domains.
    """

    return context.headers.get("Host", "") == context.authority


def _origin_is_valid(context: RequestContext) -> bool:
    """No Origin header, or one that names this server's own origin.

    Browsers attach Origin to cross-origin requests (and, in most current
    browsers, to same-origin POSTs too); a request with no Origin header is a
    same-origin form submission from a browser that omits it, or a non-browser
    client, and is not the cross-site forgery this check exists to block.
    """

    origin = context.headers.get("Origin")
    if origin is None:
        return True
    return origin in (f"http://{context.authority}", f"https://{context.authority}")


def handle_get(session: ReviewSession, path: str, context: RequestContext) -> _Response:
    """Route a GET path to a rendered page, with no socket involved."""

    if not _host_is_valid(context):
        return _Response(HTTPStatus.FORBIDDEN, "Host header does not match this server")

    parsed = urlparse(path)
    route = parsed.path
    if route in ("/", "/index.html"):
        return _Response(
            HTTPStatus.OK,
            render.render_overview(session, apply_command=_apply_command(session)),
        )
    if route.startswith("/pair/"):
        index = _parse_index(route)
        if index is None:
            return _Response(HTTPStatus.NOT_FOUND, "Not found")
        view = session.view(index)
        if view is None:
            return _Response(HTTPStatus.NOT_FOUND, "No such pair")
        return _Response(
            HTTPStatus.OK,
            render.render_pair(session, view, apply_command=_apply_command(session)),
        )
    return _Response(HTTPStatus.NOT_FOUND, "Not found")


def handle_post(
    session: ReviewSession, path: str, form: dict[str, list[str]], context: RequestContext
) -> _Response:
    """Record a verdict and redirect to the next undecided pair (or the queue)."""

    if not _host_is_valid(context):
        return _Response(HTTPStatus.FORBIDDEN, "Host header does not match this server")
    if not _origin_is_valid(context):
        return _Response(HTTPStatus.FORBIDDEN, "Origin does not match this server")

    submitted_token = form.get("token", [""])[0]
    if not hmac.compare_digest(submitted_token, session.token):
        return _Response(HTTPStatus.FORBIDDEN, "Missing or invalid session token")

    parsed = urlparse(path)
    if not parsed.path.startswith("/pair/"):
        return _Response(HTTPStatus.NOT_FOUND, "Not found")
    index = _parse_index(parsed.path)
    if index is None or session.view(index) is None:
        return _Response(HTTPStatus.NOT_FOUND, "No such pair")

    raw = form.get("verdict", [""])[0]
    verdict = _VERDICT_FORM.get(raw)
    if verdict is None:
        return _Response(HTTPStatus.BAD_REQUEST, "Unknown verdict")
    session.record(index, verdict)

    nxt = session.next_undecided(after=index)
    location = f"/pair/{nxt}" if nxt is not None else "/"
    return _Response(HTTPStatus.SEE_OTHER, location=location)


def _parse_index(route: str) -> int | None:
    tail = route[len("/pair/") :].strip("/")
    if tail.isdigit():
        return int(tail)
    return None


class ReviewRequestHandler(BaseHTTPRequestHandler):
    """Thin adapter: parse the request, delegate to the pure handlers, respond."""

    server_version = "constituent-reconciler-review"

    def __init__(self, session: ReviewSession, *args: object, **kwargs: object) -> None:
        self.session = session
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def log_message(self, format: str, *args: object) -> None:
        # Stay quiet: request paths can carry pair ids, and the review step keeps
        # no record of client data beyond the decisions file.
        return

    def _send(self, response: _Response) -> None:
        self.send_response(response.status)
        if response.location is not None:
            self.send_header("Location", response.location)
            self.end_headers()
            return
        encoded = response.body.encode("utf-8")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _context(self) -> RequestContext:
        authority: str = self.server.authority  # type: ignore[attr-defined]
        return RequestContext(authority=authority, headers=self.headers)  # type: ignore[arg-type]

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._send(handle_get(self.session, self.path, self._context()))

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(raw)
        self._send(handle_post(self.session, self.path, form, self._context()))


def build_server(session: ReviewSession, host: str, port: int) -> ThreadingHTTPServer:
    """Bind a loopback review server. Refuses a non-loopback host under DV.

    Binding is separate from serving so a test can bind to port 0 and drive the
    server in a thread. The policy check happens here, before the socket is
    bound, so a non-local bind is refused fail-closed the way a non-local write
    target is.
    """

    if session.privacy_mode and host not in LOOPBACK_HOSTS:
        raise PolicyViolation(
            f"the active policy pack requires a local-only review server; host "
            f"{host!r} is not loopback. Use one of: {', '.join(sorted(LOOPBACK_HOSTS))}."
        )
    handler = partial(ReviewRequestHandler, session)
    server = ThreadingHTTPServer((host, port), handler)
    sockname = server.socket.getsockname()
    # Stamped on the server, not computed per-request, so it reflects the
    # actual bound address (relevant when ``port`` is 0 and the OS assigns one)
    # and every handler checks the Host header against the same value.
    server.authority = f"{sockname[0]}:{sockname[1]}"  # type: ignore[attr-defined]
    return server


def serve(
    session: ReviewSession,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Run the review server until interrupted. Blocks the caller.

    Prints the local URL and how to apply decisions, optionally opens a browser,
    then serves until Ctrl-C. Every decision is written through to the decisions
    file as it is made, so an interrupt loses nothing.
    """

    server = build_server(session, host, port)
    sockname = server.socket.getsockname()
    url = f"http://{sockname[0]}:{sockname[1]}/"
    print(f"Review server running at {url}")
    print(f"  {session.total} pair(s) to review; decisions save to {session.decisions_path}")
    print("  This server is local only and sends no data over the network.")
    print("  Press Ctrl-C to stop.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception as error:  # pragma: no cover - environment without a browser
            print(f"  (could not open a browser automatically: {error})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        print("\nStopping review server.")
    finally:
        server.shutdown()
        server.server_close()
