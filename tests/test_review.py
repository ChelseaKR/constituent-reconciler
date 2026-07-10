"""Tests for the local web review queue.

The session logic is tested directly; the server is tested both through its pure
``handle_get``/``handle_post`` functions and once end to end over a real loopback
socket. The no-egress and minimization properties are asserted as behavior: a
non-loopback bind is refused under the DV pack, and the only persisted artifact
carries record ids and verdicts but no field values.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from pathlib import Path

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.config import Recipe, load_recipe
from constituent_reconciler.models import Band, Pair, Record, RunResult
from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.review.render import render_pair
from constituent_reconciler.review.server import (
    RequestContext,
    build_server,
    handle_get,
    handle_post,
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

# The host:port unit tests present as the bound authority. Real values only
# matter for the end-to-end socket test, which computes its own.
_AUTHORITY = "127.0.0.1:8765"


def _context(**headers: str) -> RequestContext:
    """A RequestContext with a valid Host header, plus any extra headers."""

    return RequestContext(authority=_AUTHORITY, headers={"Host": _AUTHORITY, **headers})


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


# -- cluster and golden-record preview (EXP-02) -------------------------------


def _synthetic_session(
    tmp_path: Path, pairs: tuple[Pair, ...], *, fields: tuple[str, ...] = ("first_name",)
) -> ReviewSession:
    # A, B, C all normalize to the same first name, so any pair of them merges
    # cleanly when the test forces the edge; the interesting behavior under
    # test is which records end up in one cluster and what the golden record
    # says, not the matcher's own scoring.
    records = {
        record_id: Record(
            unique_id=record_id,
            source="existing" if record_id == "A" else "incoming",
            raw={"first_name": record_id},
            normalized={"first_name": "ann"},
        )
        for record_id in "ABC"
    }
    result = RunResult(records=records, pairs=pairs, clusters=(), golden=())
    return ReviewSession(result, fields, tmp_path / "decisions.json")


def _index_of(session: ReviewSession, left: str, right: str) -> int:
    key = frozenset((left, right))
    return next(v.index for v in session.views() if frozenset((v.left_id, v.right_id)) == key)


def test_cluster_preview_of_an_undecided_pair_previews_an_approval(tmp_path: Path) -> None:
    session = _synthetic_session(tmp_path, (Pair("A", "B", 0.85, Band.REVIEW),))
    preview = session.cluster_preview(0)
    assert preview is not None
    assert preview.merged is True
    assert preview.conflict is False
    (group,) = preview.groups
    assert {member.record_id for member in group.members} == {"A", "B"}
    # The golden record is previewed before the decision is made, not only after.
    assert group.golden
    assert any(f.field == "first_name" and f.value == "ann" for f in group.golden)


def test_cluster_preview_of_a_rejected_pair_shows_two_separate_clusters(tmp_path: Path) -> None:
    session = _synthetic_session(tmp_path, (Pair("A", "B", 0.85, Band.REVIEW),))
    session.record(0, REJECTED)
    preview = session.cluster_preview(0)
    assert preview is not None
    assert preview.merged is False
    assert preview.conflict is False
    assert len(preview.groups) == 2
    assert {m.record_id for group in preview.groups for m in group.members} == {"A", "B"}
    # Neither side merged, so neither previews a golden record.
    assert all(not group.golden for group in preview.groups)


def test_cluster_preview_flags_a_transitive_contradiction(tmp_path: Path) -> None:
    # A-B and B-C are both approved, which transitively unions A and C even
    # though no one approved A-C directly -- and here a reviewer explicitly
    # rejected A-C. The preview must refuse to show that as a clean merge.
    pairs = (
        Pair("A", "B", 0.85, Band.REVIEW),
        Pair("B", "C", 0.85, Band.REVIEW),
        Pair("A", "C", 0.85, Band.REVIEW),
    )
    session = _synthetic_session(tmp_path, pairs)
    session.record(_index_of(session, "A", "B"), APPROVED)
    session.record(_index_of(session, "B", "C"), APPROVED)
    ac_index = _index_of(session, "A", "C")
    session.record(ac_index, REJECTED)

    preview = session.cluster_preview(ac_index)
    assert preview is not None
    assert preview.merged is False
    assert preview.conflict is True
    assert preview.groups == ()


def test_cluster_preview_surfaces_a_pending_edge_inside_a_growing_cluster(tmp_path: Path) -> None:
    # The EXP-02 scenario: approving A-B, then opening the still-pending B-C
    # pair, should show that approving it forms a 3-record cluster, and that
    # the matcher separately scored A and C apart -- visible before it is
    # written, not only inferable after the fact from three separate pairs.
    pairs = (
        Pair("A", "B", 0.85, Band.REVIEW),
        Pair("B", "C", 0.85, Band.REVIEW),
        Pair("A", "C", 0.60, Band.DROP),
    )
    session = _synthetic_session(tmp_path, pairs)
    session.record(_index_of(session, "A", "B"), APPROVED)
    bc_index = _index_of(session, "B", "C")

    preview = session.cluster_preview(bc_index)
    assert preview is not None
    assert preview.merged is True
    (group,) = preview.groups
    assert {member.record_id for member in group.members} == {"A", "B", "C"}
    statuses = {(edge.left, edge.right): edge.status for edge in group.edges}
    assert statuses[("A", "B")] == "approved"
    assert statuses[("B", "C")] == "pending"
    assert statuses[("A", "C")] == "scored-apart"


def test_render_pair_includes_the_cluster_preview_section(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    view = session.view(0)
    assert view is not None
    html = render_pair(session, view, apply_command="reconcile apply --config r.toml")
    assert "What this creates" in html
    assert "golden record" in html


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
    overview = handle_get(session, "/", _context())
    assert overview.status == HTTPStatus.OK
    assert 'lang="en"' in overview.body
    assert "Review queue" in overview.body
    # The queue lists a one-line rationale beside each pair, so a reviewer can
    # triage before opening it. A blocked review pair agrees on at least one
    # field by construction.
    assert "agree on" in overview.body

    pair = handle_get(session, "/pair/0", _context())
    assert pair.status == HTTPStatus.OK
    assert "<table" in pair.body
    # Accessibility: status is conveyed with a text label, not colour alone.
    assert "Agreement" in pair.body
    assert "Approve merge" in pair.body
    # R11: a plain-language rationale sits beside the pair, not source spans alone.
    assert "What matches and what differs" in pair.body
    assert "agree on" in pair.body
    # FIX-01: the form carries the per-run token so a POST can be checked.
    assert f'value="{session.token}"' in pair.body


def test_handle_get_unknown_pair_is_404(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    assert handle_get(session, "/pair/99", _context()).status == HTTPStatus.NOT_FOUND
    assert handle_get(session, "/nope", _context()).status == HTTPStatus.NOT_FOUND


def test_handle_get_wrong_host_is_forbidden(tmp_path: Path) -> None:
    # A hostile page pointed at a rebound hostname would carry a Host header
    # this server never bound to; refusing it is the DNS-rebinding defense.
    _, _, session = _session(tmp_path)
    forged = RequestContext(authority=_AUTHORITY, headers={"Host": "evil.example:8765"})
    assert handle_get(session, "/", forged).status == HTTPStatus.FORBIDDEN


def test_handle_post_records_and_redirects(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    response = handle_post(
        session, "/pair/0", {"verdict": ["approve"], "token": [session.token]}, _context()
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.location in ("/pair/1", "/")
    assert session.verdict(0) == APPROVED


def test_handle_post_bad_verdict_is_400(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    response = handle_post(
        session, "/pair/0", {"verdict": ["nope"], "token": [session.token]}, _context()
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    assert session.verdict(0) is None


def test_handle_post_without_token_is_forbidden(tmp_path: Path) -> None:
    # A forged cross-site POST cannot know a token it never saw rendered.
    _, _, session = _session(tmp_path)
    response = handle_post(session, "/pair/0", {"verdict": ["approve"]}, _context())
    assert response.status == HTTPStatus.FORBIDDEN
    assert session.verdict(0) is None


def test_handle_post_with_wrong_token_is_forbidden(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    response = handle_post(
        session, "/pair/0", {"verdict": ["approve"], "token": ["not-the-token"]}, _context()
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert session.verdict(0) is None


def test_handle_post_wrong_host_is_forbidden(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    forged = RequestContext(authority=_AUTHORITY, headers={"Host": "evil.example:8765"})
    response = handle_post(
        session, "/pair/0", {"verdict": ["approve"], "token": [session.token]}, forged
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert session.verdict(0) is None


def test_handle_post_cross_origin_is_forbidden(tmp_path: Path) -> None:
    # A same-origin form post carries no Origin header, or one that matches;
    # a cross-site fetch()/form carries a foreign Origin, and is refused.
    _, _, session = _session(tmp_path)
    foreign = _context(Origin="https://evil.example")
    response = handle_post(
        session, "/pair/0", {"verdict": ["approve"], "token": [session.token]}, foreign
    )
    assert response.status == HTTPStatus.FORBIDDEN
    assert session.verdict(0) is None


def test_handle_post_same_origin_is_allowed(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    same_origin = _context(Origin=f"http://{_AUTHORITY}")
    response = handle_post(
        session, "/pair/0", {"verdict": ["approve"], "token": [session.token]}, same_origin
    )
    assert response.status == HTTPStatus.SEE_OTHER
    assert session.verdict(0) == APPROVED


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

        data = urllib.parse.urlencode({"verdict": "approve", "token": session.token}).encode(
            "utf-8"
        )
        request = urllib.request.Request(base + "/pair/0", data=data, method="POST")

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            assert error.code == HTTPStatus.SEE_OTHER
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert len(payload["approved"]) == 1


def test_server_refuses_a_post_with_no_token_over_a_real_socket(tmp_path: Path) -> None:
    # FIX-01 end to end: a forged cross-site POST that never saw the rendered
    # page cannot supply the token, and the server refuses it rather than
    # recording a verdict.
    _, _, session = _session(tmp_path)
    server = build_server(session, "127.0.0.1", 0)
    host, port = server.socket.getsockname()[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{host}:{port}"
    try:
        data = urllib.parse.urlencode({"verdict": "approve"}).encode("utf-8")
        request = urllib.request.Request(base + "/pair/0", data=data, method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            opener.open(request, timeout=5)
            raised = False
        except urllib.error.HTTPError as error:
            raised = True
            assert error.code == HTTPStatus.FORBIDDEN
        assert raised
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert session.verdict(0) is None
