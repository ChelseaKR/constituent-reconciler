"""The local review server.

A small ``http.server`` handler over a ``ReviewSession``: no framework, no
third-party dependency, consistent with keeping everything around the matcher on
the standard library. The server binds the loopback interface and, under a policy
pack that requires local targets, refuses any non-loopback bind, fail-closed, so
the review surface cannot become an egress path for client PII.

The loopback bind alone does not keep other software on the machine's browser
out, so the web boundary is checked on every request, fail-closed:

* every request's Host header must name this server (loopback host, bound
  port), which defeats DNS rebinding against the pair pages;
* every POST must carry the per-session token embedded in each rendered form,
  and a POST with a foreign Origin header or a non-form content type is
  refused, which defeats cross-site verdict forging.

Request handling is split from socket binding on purpose. ``handle_get`` and
``handle_post`` return ``(status, body)`` from the session alone, so they are
tested without a socket; ``build_server`` binds a real loopback socket for the
end-to-end path and the CLI. The boundary checks are pure functions
(``host_allowed``, ``origin_allowed``) for the same reason.
"""

from __future__ import annotations

import secrets
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import parse_qs, urlparse, urlsplit

from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.review import render
from constituent_reconciler.review.session import APPROVED, REJECTED, ReviewSession

# Interfaces that keep traffic on the machine. A non-loopback bind under the DV
# pack is refused, mirroring the connector local-target gate.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_VERDICT_FORM = {"approve": APPROVED, "reject": REJECTED}

_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


class _Response:
    __slots__ = ("status", "body", "location", "content_type")

    def __init__(
        self,
        status: int,
        body: str = "",
        location: str | None = None,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.status = status
        self.body = body
        self.location = location
        self.content_type = content_type


def _refused(status: int, body: str) -> _Response:
    return _Response(status, body, content_type="text/plain; charset=utf-8")


def _apply_command(session: ReviewSession) -> str:
    return f"reconcile apply --config <recipe.toml> --decisions {session.decisions_path}"


def host_allowed(host_header: str | None, *, host: str, port: int) -> bool:
    """True when the Host header names this server: a local host, the bound port.

    A missing, malformed, or foreign Host header is refused. This is the DNS
    rebinding defence: a hostile page that resolves its own name to 127.0.0.1
    still sends its own name in Host, and is turned away before any pair data
    is rendered.
    """

    if not host_header:
        return False
    try:
        parts = urlsplit("//" + host_header.strip())
        hostname, host_port = parts.hostname, parts.port
    except ValueError:
        return False
    if hostname is None or hostname not in (LOOPBACK_HOSTS | {host.lower()}):
        return False
    return (host_port if host_port is not None else 80) == port


def origin_allowed(origin_header: str | None, *, host: str, port: int) -> bool:
    """True when a POST's Origin header is this server's own origin, or absent.

    Browsers send Origin on cross-site form posts; a value naming any other
    origin (including "null") is refused. An absent header is allowed because
    non-browser clients omit it, and the session token still gates the POST.
    """

    if origin_header is None:
        return True
    try:
        parts = urlsplit(origin_header.strip())
    except ValueError:
        return False
    if parts.scheme != "http":
        return False
    hostname, origin_port = parts.hostname, parts.port
    if hostname is None or hostname not in (LOOPBACK_HOSTS | {host.lower()}):
        return False
    return (origin_port if origin_port is not None else 80) == port


def handle_get(session: ReviewSession, path: str, *, csrf_token: str) -> _Response:
    """Route a GET path to a rendered page, with no socket involved."""

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
            return _refused(HTTPStatus.NOT_FOUND, "Not found")
        view = session.view(index)
        if view is None:
            return _refused(HTTPStatus.NOT_FOUND, "No such pair")
        return _Response(
            HTTPStatus.OK,
            render.render_pair(
                session, view, apply_command=_apply_command(session), csrf_token=csrf_token
            ),
        )
    return _refused(HTTPStatus.NOT_FOUND, "Not found")


def handle_post(
    session: ReviewSession, path: str, form: dict[str, list[str]], *, csrf_token: str
) -> _Response:
    """Record a verdict and redirect to the next undecided pair (or the queue).

    The form must carry the per-session token the pair page embedded. A POST
    without it (or with a stale or empty one) is refused before any state
    changes: a cross-site form cannot read the page to learn the token, so it
    cannot forge a verdict.
    """

    supplied = form.get("token", [""])[0]
    if not csrf_token or not secrets.compare_digest(supplied, csrf_token):
        return _refused(HTTPStatus.FORBIDDEN, "Missing or invalid session token")

    parsed = urlparse(path)
    if not parsed.path.startswith("/pair/"):
        return _refused(HTTPStatus.NOT_FOUND, "Not found")
    index = _parse_index(parsed.path)
    if index is None or session.view(index) is None:
        return _refused(HTTPStatus.NOT_FOUND, "No such pair")

    raw = form.get("verdict", [""])[0]
    verdict = _VERDICT_FORM.get(raw)
    if verdict is None:
        return _refused(HTTPStatus.BAD_REQUEST, "Unknown verdict")
    session.record(index, verdict)

    nxt = session.next_undecided(after=index)
    location = f"/pair/{nxt}" if nxt is not None else "/"
    return _Response(HTTPStatus.SEE_OTHER, location=location)


def _parse_index(route: str) -> int | None:
    tail = route[len("/pair/") :].strip("/")
    if tail.isdigit():
        return int(tail)
    return None


class ReviewServer(ThreadingHTTPServer):
    """The bound server, carrying the session and the per-session token.

    The token is minted once per server, at bind time, from the OS CSPRNG. It
    reaches the browser only inside rendered pair pages, which the same-origin
    policy keeps unreadable to other sites, so possession of it demonstrates
    the POST came from this server's own pages.
    """

    def __init__(self, address: tuple[str, int], session: ReviewSession) -> None:
        self.session = session
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(address, ReviewRequestHandler)


class ReviewRequestHandler(BaseHTTPRequestHandler):
    """Thin adapter: check the web boundary, delegate to the pure handlers."""

    server_version = "constituent-reconciler-review"

    def log_message(self, format: str, *args: object) -> None:
        # Stay quiet: request paths can carry pair ids, and the review step keeps
        # no record of client data beyond the decisions file.
        return

    @property
    def _review_server(self) -> ReviewServer:
        return cast(ReviewServer, self.server)

    def _send(self, response: _Response) -> None:
        self.send_response(response.status)
        # Uniform response discipline: never let a client sniff a type.
        self.send_header("X-Content-Type-Options", "nosniff")
        if response.location is not None:
            self.send_header("Location", response.location)
            self.end_headers()
            return
        encoded = response.body.encode("utf-8")
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _bound_address(self) -> tuple[str, int]:
        host, port = self._review_server.socket.getsockname()[:2]
        return str(host), int(port)

    def _host_ok(self) -> bool:
        host, port = self._bound_address()
        return host_allowed(self.headers.get("Host"), host=host, port=port)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        if not self._host_ok():
            self._send(_refused(HTTPStatus.FORBIDDEN, "Host header does not name this server"))
            return
        self._send(
            handle_get(
                self._review_server.session, self.path, csrf_token=self._review_server.csrf_token
            )
        )

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        if not self._host_ok():
            self._send(_refused(HTTPStatus.FORBIDDEN, "Host header does not name this server"))
            return
        host, port = self._bound_address()
        if not origin_allowed(self.headers.get("Origin"), host=host, port=port):
            self._send(_refused(HTTPStatus.FORBIDDEN, "Origin is not this server"))
            return
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != _FORM_CONTENT_TYPE:
            self._send(
                _refused(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    f"POST bodies must be {_FORM_CONTENT_TYPE}",
                )
            )
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(raw)
        self._send(
            handle_post(
                self._review_server.session,
                self.path,
                form,
                csrf_token=self._review_server.csrf_token,
            )
        )


def build_server(session: ReviewSession, host: str, port: int) -> ReviewServer:
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
    return ReviewServer((host, port), session)


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
