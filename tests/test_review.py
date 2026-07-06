"""Tests for the local web review queue.

The session logic is tested directly; the server is tested both through its pure
``handle_get``/``handle_post`` functions and end to end over a real loopback
socket. The no-egress and minimization properties are asserted as behavior: a
non-loopback bind is refused under the DV pack, and the only persisted artifact
carries record ids and verdicts but no field values. The web-boundary checks
(per-session token, Host, Origin, content type) are asserted both as pure
predicates and over the socket, because the boundary is part of the no-egress
claim.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.config import Recipe, load_recipe
from constituent_reconciler.models import RunResult
from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.review.server import (
    build_server,
    handle_get,
    handle_post,
    host_allowed,
    origin_allowed,
)
from constituent_reconciler.review.session import (
    APPROVED,
    REJECTED,
    FieldCell,
    PairView,
    ReviewSession,
    rationale_for,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


def _session(
    tmp_path: Path, *, recipe_name: str = "recipe.toml", privacy: bool = False
) -> tuple[RunResult, Recipe, ReviewSession]:
    recipe = load_recipe(EXAMPLES / recipe_name)
    result = pipeline.run(recipe)
    return (
        result,
        recipe,
        ReviewSession(
            result,
            recipe.fields,
            tmp_path / "decisions.json",
            privacy_mode=privacy,
        ),
    )


# -- session -----------------------------------------------------------------


def test_session_exposes_the_review_pairs(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    assert session.total == 2
    views = session.views()
    # The two known lookalike pairs are routed to review by the pipeline.
    keys = {frozenset((v.left_id, v.right_id)) for v in views}
    assert frozenset(("E002", "N004")) in keys
    assert frozenset(("E008", "N007")) in keys


def test_record_writes_through_to_decisions_file(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    session.record(0, APPROVED)
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert len(payload["approved"]) == 1
    assert payload["rejected"] == []
    counts = session.counts()
    assert counts.approved == 1 and counts.pending == 1


def test_unknown_verdict_is_refused(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    with pytest.raises(ValueError):
        session.record(0, "maybe")


def test_session_resumes_from_existing_decisions(tmp_path: Path) -> None:
    result, recipe, session = _session(tmp_path)
    view = session.views()[0]
    session.record(view.index, REJECTED)
    # A fresh session over the same run re-attaches the verdict by pair id.
    resumed = ReviewSession(result, recipe.fields, tmp_path / "decisions.json")
    same = next(v for v in resumed.views() if v.left_id == view.left_id)
    assert resumed.verdict(same.index) == REJECTED


def test_next_undecided_skips_decided_pairs(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    assert session.next_undecided() == 0
    session.record(0, APPROVED)
    assert session.next_undecided(after=0) == 1
    session.record(1, REJECTED)
    assert session.next_undecided() is None


def test_decisions_file_carries_no_field_values(tmp_path: Path) -> None:
    # Minimization: the only persisted artifact is ids and verdicts. No name,
    # email, or other field value of a reviewed record may appear in it.
    result, recipe, session = _session(tmp_path)
    for view in session.views():
        session.record(view.index, APPROVED)
    blob = (tmp_path / "decisions.json").read_text(encoding="utf-8")
    for record in result.records.values():
        for value in record.raw.values():
            if value and len(value) > 2:
                assert value not in blob


# -- match rationale ---------------------------------------------------------


def _cell(field: str, *, comparable: bool, agrees: bool) -> FieldCell:
    return FieldCell(
        field=field,
        left="x",
        right="y",
        left_span="",
        right_span="",
        agrees=agrees,
        comparable=comparable,
    )


def _view(fields: tuple[FieldCell, ...]) -> PairView:
    return PairView(
        index=0,
        left_id="E001",
        right_id="N002",
        left_source="existing",
        right_source="incoming",
        probability=0.9,
        fields=fields,
    )


def test_match_rationale_buckets_and_summarizes() -> None:
    view = _view(
        (
            _cell("last_name", comparable=True, agrees=True),
            _cell("dob", comparable=True, agrees=False),
            _cell("email", comparable=False, agrees=False),
        )
    )
    rationale = rationale_for(view)
    assert rationale.agree == ("last name",)
    assert rationale.differ == ("date of birth",)
    assert rationale.uncompared == ("email",)
    assert rationale.summary() == (
        "These records agree on last name. They differ on date of birth. "
        "Email was blank on at least one record, so it could not be compared."
    )
    assert rationale.short() == "agree on last name; differ on date of birth; email not compared"


def test_match_rationale_joins_multiple_fields_and_pluralizes() -> None:
    view = _view(
        (
            _cell("first_name", comparable=True, agrees=True),
            _cell("last_name", comparable=True, agrees=True),
            _cell("address", comparable=True, agrees=True),
            _cell("email", comparable=False, agrees=False),
            _cell("phone", comparable=False, agrees=False),
        )
    )
    summary = rationale_for(view).summary()
    assert "agree on first name, last name, and address" in summary
    assert "Email and phone were blank on at least one record, so they could not be compared" in (
        summary
    )


# -- pure request handlers ---------------------------------------------------


def test_handle_get_renders_overview_and_pair(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    overview = handle_get(session, "/", csrf_token="tok")
    assert overview.status == HTTPStatus.OK
    assert 'lang="en"' in overview.body
    assert "Review queue" in overview.body
    # The queue lists a one-line rationale beside each pair, so a reviewer can
    # triage before opening it. A blocked review pair agrees on at least one
    # field by construction.
    assert "agree on" in overview.body

    pair = handle_get(session, "/pair/0", csrf_token="tok")
    assert pair.status == HTTPStatus.OK
    assert "<table" in pair.body
    # Accessibility: status is conveyed with a text label, not colour alone.
    assert "Agreement" in pair.body
    assert "Approve merge" in pair.body
    # R11: a plain-language rationale sits beside the pair, not source spans alone.
    assert "What matches and what differs" in pair.body
    assert "agree on" in pair.body


def test_handle_get_unknown_pair_is_404(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    assert handle_get(session, "/pair/99", csrf_token="tok").status == HTTPStatus.NOT_FOUND
    assert handle_get(session, "/nope", csrf_token="tok").status == HTTPStatus.NOT_FOUND


def test_handle_post_records_and_redirects(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    form = {"verdict": ["approve"], "token": ["tok"]}
    response = handle_post(session, "/pair/0", form, csrf_token="tok")
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.location in ("/pair/1", "/")
    assert session.verdict(0) == APPROVED


def test_handle_post_bad_verdict_is_400(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    form = {"verdict": ["nope"], "token": ["tok"]}
    assert handle_post(session, "/pair/0", form, csrf_token="tok").status == HTTPStatus.BAD_REQUEST
    assert session.verdict(0) is None


# -- web boundary: token, Host, Origin ----------------------------------------


def test_pair_page_embeds_the_session_token(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    pair = handle_get(session, "/pair/0", csrf_token="sekrit-tok")
    assert 'name="token" value="sekrit-tok"' in pair.body


def test_post_without_token_is_refused(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    response = handle_post(session, "/pair/0", {"verdict": ["approve"]}, csrf_token="tok")
    assert response.status == HTTPStatus.FORBIDDEN
    assert session.verdict(0) is None


def test_post_with_wrong_token_is_refused(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    form = {"verdict": ["approve"], "token": ["forged"]}
    response = handle_post(session, "/pair/0", form, csrf_token="tok")
    assert response.status == HTTPStatus.FORBIDDEN
    assert session.verdict(0) is None


def test_empty_server_token_fails_closed(tmp_path: Path) -> None:
    # A server built without a token must refuse every POST, never accept
    # an empty-string match.
    _, _, session = _session(tmp_path)
    form = {"verdict": ["approve"], "token": [""]}
    response = handle_post(session, "/pair/0", form, csrf_token="")
    assert response.status == HTTPStatus.FORBIDDEN
    assert session.verdict(0) is None


def test_host_allowed_accepts_only_this_loopback_server() -> None:
    assert host_allowed("127.0.0.1:8765", host="127.0.0.1", port=8765)
    assert host_allowed("localhost:8765", host="127.0.0.1", port=8765)
    assert host_allowed("[::1]:8765", host="127.0.0.1", port=8765)
    # DNS rebinding: the attacker's name resolves here but arrives in Host.
    assert not host_allowed("evil.example.com:8765", host="127.0.0.1", port=8765)
    assert not host_allowed("127.0.0.1:9999", host="127.0.0.1", port=8765)
    assert not host_allowed("127.0.0.1", host="127.0.0.1", port=8765)  # port 80 implied
    assert not host_allowed(None, host="127.0.0.1", port=8765)
    assert not host_allowed("", host="127.0.0.1", port=8765)
    assert not host_allowed("not a host:xyz", host="127.0.0.1", port=8765)


def test_origin_allowed_accepts_own_origin_or_absence() -> None:
    assert origin_allowed(None, host="127.0.0.1", port=8765)  # non-browser client
    assert origin_allowed("http://127.0.0.1:8765", host="127.0.0.1", port=8765)
    assert origin_allowed("http://localhost:8765", host="127.0.0.1", port=8765)
    assert not origin_allowed("http://evil.example.com", host="127.0.0.1", port=8765)
    assert not origin_allowed("http://127.0.0.1:9999", host="127.0.0.1", port=8765)
    assert not origin_allowed("https://127.0.0.1:8765", host="127.0.0.1", port=8765)
    assert not origin_allowed("null", host="127.0.0.1", port=8765)
    assert not origin_allowed("", host="127.0.0.1", port=8765)


# -- no-egress / privacy -----------------------------------------------------


def test_dv_pack_refuses_a_non_loopback_bind(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path, recipe_name="recipe-dv.toml", privacy=True)
    assert session.privacy_mode is True
    with pytest.raises(PolicyViolation, match="loopback"):
        build_server(session, "0.0.0.0", 0)


def test_loopback_bind_is_allowed_under_dv(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path, recipe_name="recipe-dv.toml", privacy=True)
    server = build_server(session, "127.0.0.1", 0)
    try:
        assert server.socket.getsockname()[0] == "127.0.0.1"
    finally:
        server.server_close()


# -- end to end over a real loopback socket ----------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _status_of(request: urllib.request.Request) -> int:
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=5) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return error.code


def test_server_serves_over_loopback_and_records_a_decision(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    server = build_server(session, "127.0.0.1", 0)
    host, port = server.socket.getsockname()[:2]
    assert host == "127.0.0.1"  # bound to loopback, no external interface
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    try:
        index = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
        assert "Review queue" in index

        # The pair page embeds the per-session token; a real browser posts it back.
        page = urllib.request.urlopen(base + "/pair/0", timeout=5).read().decode("utf-8")
        found = re.search(r'name="token" value="([^"]+)"', page)
        assert found is not None
        assert found.group(1) == server.csrf_token

        data = urllib.parse.urlencode({"verdict": "approve", "token": found.group(1)}).encode(
            "utf-8"
        )
        request = urllib.request.Request(base + "/pair/0", data=data, method="POST")
        assert _status_of(request) == HTTPStatus.SEE_OTHER
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    # The decision made over HTTP landed in the decisions file.
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert len(payload["approved"]) == 1


def test_server_refuses_forged_and_rebound_requests(tmp_path: Path) -> None:
    """The web boundary over a real socket: Host, Origin, token, content type.

    A hostile page cannot read pair data via DNS rebinding (foreign Host on
    GET), and cannot forge a verdict via cross-site request forgery (missing
    token, foreign Origin, or a non-form content type on POST). No refused
    request may change the session.
    """

    _, _, session = _session(tmp_path)
    server = build_server(session, "127.0.0.1", 0)
    host, port = server.socket.getsockname()[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    good = urllib.parse.urlencode({"verdict": "approve", "token": server.csrf_token}).encode(
        "utf-8"
    )
    forged = urllib.parse.urlencode({"verdict": "approve"}).encode("utf-8")
    try:
        # DNS rebinding: the request reaches the socket but carries a foreign Host.
        rebound = urllib.request.Request(
            base + "/pair/0", headers={"Host": f"evil.example.com:{port}"}
        )
        assert _status_of(rebound) == HTTPStatus.FORBIDDEN

        # CSRF: a cross-site form post has no token.
        assert (
            _status_of(urllib.request.Request(base + "/pair/0", data=forged, method="POST"))
            == HTTPStatus.FORBIDDEN
        )

        # CSRF: a browser stamps the attacking page's origin on the post.
        cross_origin = urllib.request.Request(
            base + "/pair/0",
            data=good,
            method="POST",
            headers={"Origin": "http://evil.example.com"},
        )
        assert _status_of(cross_origin) == HTTPStatus.FORBIDDEN

        # Content-type discipline: only form-encoded bodies are parsed.
        wrong_type = urllib.request.Request(
            base + "/pair/0",
            data=good,
            method="POST",
            headers={"Content-Type": "text/plain"},
        )
        assert _status_of(wrong_type) == HTTPStatus.UNSUPPORTED_MEDIA_TYPE

        # The server's own origin with the real token still works.
        own_origin = urllib.request.Request(
            base + "/pair/0",
            data=good,
            method="POST",
            headers={"Origin": f"http://{host}:{port}"},
        )
        assert _status_of(own_origin) == HTTPStatus.SEE_OTHER
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    # Only the one legitimate post changed anything.
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert len(payload["approved"]) == 1
    assert payload["rejected"] == []
