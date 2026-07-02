"""The local review server.

A small ``http.server`` handler over a ``ReviewSession``: no framework, no
third-party dependency, consistent with keeping everything around the matcher on
the standard library. The server binds the loopback interface and, under a policy
pack that requires local targets, refuses any non-loopback bind, fail-closed, so
the review surface cannot become an egress path for client PII.

Request handling is split from socket binding on purpose. ``handle_get`` and
``handle_post`` return ``(status, body)`` from the session alone, so they are
tested without a socket; ``build_server`` binds a real loopback socket for the
end-to-end path and the CLI.
"""

from __future__ import annotations

import webbrowser
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.review import render
from constituent_reconciler.review.session import (
    APPROVED,
    AWAITING_SECOND,
    REJECTED,
    ReviewSession,
)

# Interfaces that keep traffic on the machine. A non-loopback bind under the DV
# pack is refused, mirroring the connector local-target gate.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_VERDICT_FORM = {"approve": APPROVED, "reject": REJECTED}


class _Response:
    __slots__ = ("status", "body", "location")

    def __init__(self, status: int, body: str = "", location: str | None = None) -> None:
        self.status = status
        self.body = body
        self.location = location


def _apply_command(session: ReviewSession) -> str:
    return f"reconcile apply --config <recipe.toml> --decisions {session.decisions_path}"


def handle_get(session: ReviewSession, path: str) -> _Response:
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
            return _Response(HTTPStatus.NOT_FOUND, "Not found")
        view = session.view(index)
        if view is None:
            return _Response(HTTPStatus.NOT_FOUND, "No such pair")
        return _Response(
            HTTPStatus.OK,
            render.render_pair(session, view, apply_command=_apply_command(session)),
        )
    return _Response(HTTPStatus.NOT_FOUND, "Not found")


def handle_post(session: ReviewSession, path: str, form: dict[str, list[str]]) -> _Response:
    """Record a verdict and redirect to the next undecided pair (or the queue)."""

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
    if (
        verdict == APPROVED
        and session.verdict(index) == AWAITING_SECOND
        and session.reviewer in session.approvers(index)
    ):
        # Two-person mode: the same name cannot supply both approvals. The
        # held approval stays held until a different reviewer confirms it.
        return _Response(
            HTTPStatus.CONFLICT,
            f"You ({session.reviewer}) already approved this pair. A different "
            "reviewer must provide the second approval before it can merge.",
        )
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

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._send(handle_get(self.session, self.path))

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(raw)
        self._send(handle_post(self.session, self.path, form))


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
    return ThreadingHTTPServer((host, port), handler)


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
    print(f"  Reviewing as {session.reviewer}.")
    if session.require_second_reviewer:
        print(
            "  Two-person review is on: a merge takes effect only after two "
            "different reviewers approve it."
        )
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
