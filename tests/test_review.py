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
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import cast

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.config import Recipe, load_recipe
from constituent_reconciler.models import Band, Pair, Record, RunResult
from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.review.calibration import generate_calibration_pairs
from constituent_reconciler.review.render import render_pair
from constituent_reconciler.review.server import (
    HeaderSource,
    RequestContext,
    build_server,
    handle_get,
    handle_post,
)
from constituent_reconciler.review.session import (
    APPROVED,
    AWAITING_SECOND,
    REJECTED,
    FieldCell,
    PairView,
    ReviewSession,
    approved_without_second_approval,
    rationale_for,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"

# The host:port unit tests present as the bound authority. Real values only
# matter for the end-to-end socket test, which computes its own.
_AUTHORITY = "127.0.0.1:8765"


def _headers(mapping: dict[str, str]) -> HeaderSource:
    """A plain dict as a HeaderSource.

    ``dict.get``'s overloads satisfy the protocol at runtime, but mypy cannot
    unify them with the protocol's single defaulted signature, hence the cast.
    """

    return cast(HeaderSource, mapping)


def _context(**headers: str) -> RequestContext:
    """A RequestContext with a valid Host header, plus any extra headers."""

    return RequestContext(authority=_AUTHORITY, headers=_headers({"Host": _AUTHORITY, **headers}))


def _session(
    tmp_path: Path,
    *,
    recipe_name: str = "recipe.toml",
    privacy: bool = False,
    reviewer: str = "casey",
    require_second: bool = False,
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
            reviewer=reviewer,
            privacy_mode=privacy,
            require_second_reviewer=require_second,
        ),
    )


# -- session -----------------------------------------------------------------


def test_session_exposes_the_review_pairs(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    assert session.total == 2
    views = session.views()
    # The two known lookalike pairs are routed to review by the pipeline.
    keys = {frozenset((v.left_id, v.right_id)) for v in views}
    assert frozenset(("existing:E002", "incoming:N004")) in keys
    assert frozenset(("existing:E008", "incoming:N007")) in keys


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
    resumed = ReviewSession(result, recipe.fields, tmp_path / "decisions.json", reviewer="jordan")
    same = next(v for v in resumed.views() if v.left_id == view.left_id)
    assert resumed.verdict(same.index) == REJECTED
    # The audit trail survives the resume: the original reviewer is still named.
    assert [entry.reviewer for entry in resumed.audit(same.index)] == ["casey"]


def test_session_resumes_from_a_version1_flat_file(tmp_path: Path) -> None:
    # A decisions file written before the audit trail existed has only the flat
    # approved/rejected lists. It resumes, attributed to "unrecorded".
    result, recipe, session = _session(tmp_path)
    view = session.views()[0]
    (tmp_path / "decisions.json").write_text(
        json.dumps({"approved": [[view.left_id, view.right_id]], "rejected": []}),
        encoding="utf-8",
    )
    resumed = ReviewSession(result, recipe.fields, tmp_path / "decisions.json", reviewer="jordan")
    assert resumed.verdict(view.index) == APPROVED
    assert [entry.reviewer for entry in resumed.audit(view.index)] == ["unrecorded"]


def test_decisions_file_carries_schema_version(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    session.record(0, APPROVED)
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    # Version 2 added the audit section beside the version-1 lists.
    assert payload["decisions_schema"] == 2


def test_stale_decision_warns_instead_of_silently_dropping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A decisions file that references ids absent from the current run (source
    # rows changed between review and apply) names the dropped verdict on
    # stderr rather than ignoring it without a trace.
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "decisions_schema": 1,
                "approved": [["existing:GONE1", "incoming:GONE2"]],
                "rejected": [],
            }
        ),
        encoding="utf-8",
    )
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    session = ReviewSession(result, recipe.fields, decisions_path, reviewer="casey")
    stderr = capsys.readouterr().err
    assert "existing:GONE1" in stderr
    assert "not in this run's review queue" in stderr
    assert session.counts().pending == session.total


def test_next_undecided_skips_decided_pairs(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    assert session.next_undecided() == 0
    session.record(0, APPROVED)
    assert session.next_undecided(after=0) == 1
    session.record(1, REJECTED)
    assert session.next_undecided() is None


def test_decisions_file_carries_no_field_values(tmp_path: Path) -> None:
    # Minimization: the persisted artifact is ids, verdicts, reviewer names,
    # and timestamps. No name, email, or other field value of a reviewed
    # record may appear in it.
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
    return ReviewSession(result, fields, tmp_path / "decisions.json", reviewer="casey")


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
    html = render_pair(session, view, apply_command="constituent-reconcile apply --config r.toml")
    assert "What this creates" in html
    assert "golden record" in html


# -- reviewer audit trail ------------------------------------------------------


def test_blank_reviewer_is_refused(tmp_path: Path) -> None:
    # An unattributed verdict would defeat the audit trail, fail-closed.
    with pytest.raises(ValueError, match="blank"):
        _session(tmp_path, reviewer="   ")
    _, _, session = _session(tmp_path)
    with pytest.raises(ValueError, match="blank"):
        session.record(0, APPROVED, reviewer="")


def test_each_verdict_is_attributed_in_the_audit_section(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    view = session.views()[0]
    session.record(view.index, APPROVED)
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert payload["decisions_schema"] == 2
    key = "|".join(sorted((view.left_id, view.right_id)))
    entries = payload["audit"][key]
    assert len(entries) == 1
    assert entries[0]["reviewer"] == "casey"
    assert entries[0]["verdict"] == APPROVED
    # decided_at is a parseable ISO 8601 UTC timestamp.
    datetime.fromisoformat(entries[0]["decided_at"])


def test_rerecording_overwrites_the_same_reviewers_entry(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    session.record(0, APPROVED)
    session.record(0, REJECTED)
    assert session.verdict(0) == REJECTED
    assert [entry.verdict for entry in session.audit(0)] == [REJECTED]


def test_clear_removes_every_reviewers_entry(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path, require_second=True)
    session.record(0, APPROVED, reviewer="casey")
    session.record(0, APPROVED, reviewer="jordan")
    session.clear(0)
    assert session.verdict(0) is None
    assert session.audit(0) == ()
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert payload["approved"] == [] and payload["audit"] == {}


# -- two-person review ---------------------------------------------------------


def test_two_person_mode_holds_a_single_approval(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path, require_second=True)
    view = session.views()[0]
    session.record(view.index, APPROVED)
    assert session.verdict(view.index) == AWAITING_SECOND
    counts = session.counts()
    assert counts.approved == 0 and counts.awaiting_second == 1
    # The held approval is in the audit trail but not in the approved list, so
    # `constituent-reconcile apply` cannot merge it.
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert payload["approved"] == []
    key = "|".join(sorted((view.left_id, view.right_id)))
    assert payload["audit"][key][0]["reviewer"] == "casey"


def test_second_distinct_reviewer_completes_the_approval(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path, require_second=True)
    session.record(0, APPROVED, reviewer="casey")
    session.record(0, APPROVED, reviewer="jordan")
    assert session.verdict(0) == APPROVED
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert len(payload["approved"]) == 1


def test_the_same_reviewer_cannot_supply_both_approvals(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path, require_second=True)
    session.record(0, APPROVED, reviewer="casey")
    session.record(0, APPROVED, reviewer="casey")
    # A repeat approval overwrites the same entry; it never counts twice.
    assert session.verdict(0) == AWAITING_SECOND
    assert len(session.audit(0)) == 1
    # The server refuses it outright with an explanation.
    response = handle_post(
        session, "/pair/0", {"verdict": ["approve"], "token": [session.token]}, _context()
    )
    assert response.status == HTTPStatus.CONFLICT
    assert "different reviewer" in response.body


@pytest.mark.parametrize(
    "second_spelling",
    ["CASEY", "casey", "Casey ", " Casey"],
    ids=["all-caps", "all-lower", "trailing-space", "leading-space"],
)
def test_a_case_or_spacing_variant_of_one_reviewer_cannot_supply_both_approvals(
    tmp_path: Path, second_spelling: str
) -> None:
    """#97: the two-person gate must not be satisfiable by one person typing
    their own name differently the second time. Every variant here reads as
    the same identity as "Casey" under `_reviewer_identity_key` and must
    overwrite, not add to, the existing entry."""
    _, _, session = _session(tmp_path, require_second=True)
    session.record(0, APPROVED, reviewer="Casey")
    session.record(0, APPROVED, reviewer=second_spelling)
    assert session.verdict(0) == AWAITING_SECOND
    assert len(session.audit(0)) == 1
    assert len(session.approvers(0)) == 1


def test_a_doubled_internal_space_is_the_same_reviewer(tmp_path: Path) -> None:
    """The exact second reproduction from the issue: 'Jane Doe' and
    'Jane  Doe' (doubled internal space) is one person, not two."""
    _, _, session = _session(tmp_path, require_second=True)
    session.record(0, APPROVED, reviewer="Jane Doe")
    session.record(0, APPROVED, reviewer="Jane  Doe")
    assert session.verdict(0) == AWAITING_SECOND
    assert len(session.audit(0)) == 1
    assert len(session.approvers(0)) == 1


def test_a_genuinely_different_reviewer_still_completes_the_approval_across_spellings(
    tmp_path: Path,
) -> None:
    """The fix must not be so aggressive that it merges two different real
    reviewers. 'Jordan' and 'Casey' share no normalized identity, so the
    second approval still completes the pair, exactly as before #97."""
    _, _, session = _session(tmp_path, require_second=True)
    session.record(0, APPROVED, reviewer="Casey")
    session.record(0, APPROVED, reviewer="Jordan")
    assert session.verdict(0) == APPROVED
    assert len(session.approvers(0)) == 2


def test_next_undecided_recognizes_its_own_approval_under_a_different_spelling(
    tmp_path: Path,
) -> None:
    """A reviewer resuming under a different capitalization than they used
    the first time must not be shown their own already-approved pair as
    still outstanding (the read-side companion to #97's write-side fix)."""
    _, _, session = _session(tmp_path, reviewer="Casey", require_second=True)
    session.record(0, APPROVED, reviewer="Casey")
    assert session.verdict(0) == AWAITING_SECOND
    # Before resuming, pair 0 is correctly this same session's own outstanding
    # slot: it was never open for Casey to "find" again mid-session.
    assert session.next_undecided(after=-1) != 0

    resumed = ReviewSession(
        session._result,
        session._fields,
        session._decisions_path,
        reviewer="casey",
        require_second_reviewer=True,
    )
    assert resumed.verdict(0) == AWAITING_SECOND
    # The bug: raw string comparison would say "casey" != "Casey" and offer
    # pair 0 back to the same person as if it were still open for them.
    assert resumed.next_undecided(after=-1) != 0


def test_a_rejection_rejects_immediately_in_two_person_mode(tmp_path: Path) -> None:
    # Disagreement never merges: one rejection outweighs any approvals.
    _, _, session = _session(tmp_path, require_second=True)
    session.record(0, APPROVED, reviewer="casey")
    session.record(0, REJECTED, reviewer="jordan")
    assert session.verdict(0) == REJECTED
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert payload["approved"] == []
    assert len(payload["rejected"]) == 1


def test_next_undecided_offers_awaiting_pairs_to_a_different_reviewer(
    tmp_path: Path,
) -> None:
    result, recipe, session = _session(tmp_path, require_second=True)
    session.record(0, APPROVED)
    # The reviewer who approved has nothing more to do on pair 0.
    assert session.next_undecided() == 1
    # A different reviewer resumes from the same file and is offered pair 0.
    other = ReviewSession(
        result,
        recipe.fields,
        tmp_path / "decisions.json",
        reviewer="jordan",
        require_second_reviewer=True,
    )
    assert other.next_undecided() == 0


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
    forged = RequestContext(authority=_AUTHORITY, headers=_headers({"Host": "evil.example:8765"}))
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


def test_pages_name_the_reviewer(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    assert "Reviewing as <strong>casey</strong>" in handle_get(session, "/", _context()).body
    assert "Reviewing as <strong>casey</strong>" in handle_get(session, "/pair/0", _context()).body


def test_pages_show_pairs_awaiting_a_second_reviewer(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path, require_second=True)
    assert "Two-person review is on" in handle_get(session, "/", _context()).body
    session.record(0, APPROVED)
    overview = handle_get(session, "/", _context()).body
    assert "AWAITING SECOND REVIEWER" in overview
    assert "1 awaiting a second reviewer" in overview
    pair = handle_get(session, "/pair/0", _context()).body
    assert "approved by casey" in pair
    assert "different reviewer" in pair


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
    forged = RequestContext(authority=_AUTHORITY, headers=_headers({"Host": "evil.example:8765"}))
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


# -- constituent-reconcile apply against the audit trail ----------------------------------


def test_apply_refuses_a_file_awaiting_a_second_reviewer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from constituent_reconciler.cli import main

    _, _, session = _session(tmp_path, require_second=True)
    view = session.views()[0]
    session.record(view.index, APPROVED)
    code = main(
        [
            "apply",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--decisions",
            str(tmp_path / "decisions.json"),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "awaiting" in err
    # The refusal names the held pairs so the team knows what to finish.
    assert view.left_id in err and view.right_id in err


def test_apply_accepts_the_file_once_the_second_reviewer_approves(
    tmp_path: Path,
) -> None:
    from constituent_reconciler.cli import main

    _, _, session = _session(tmp_path, require_second=True)
    for view in session.views():
        session.record(view.index, APPROVED, reviewer="casey")
        session.record(view.index, APPROVED, reviewer="jordan")
    code = main(
        [
            "apply",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--decisions",
            str(tmp_path / "decisions.json"),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0


# -- the two-person gate is a policy, so apply re-checks it ------------------
#
# The audit-trail check above is derived: it flags a pair recorded in ``audit``
# but absent from ``approved``, which is the shape a session in two-person mode
# writes for a held single approval. That shape only exists when the review ran
# under two-person mode. A decisions file produced under single-reviewer mode
# puts every approved pair straight into ``approved`` with one approver, so the
# derived check finds nothing to flag, and before this gate ``apply`` merged it
# even under a pack that requires two people.


def test_apply_under_a_two_person_pack_refuses_a_single_reviewer_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from constituent_reconciler.cli import main

    # The review ran under the default pack, so the session never held the
    # approval back; the file is exactly what the tool writes there.
    _, _, session = _session(tmp_path, require_second=False)
    view = session.views()[0]
    session.record(view.index, APPROVED, reviewer="casey")
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert payload["approved"] == [[view.left_id, view.right_id]]

    # Applying it under the DV pack, which requires two reviewers, must refuse.
    code = main(
        [
            "apply",
            "--config",
            str(EXAMPLES / "recipe-dv.toml"),
            "--decisions",
            str(tmp_path / "decisions.json"),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "second reviewer" in err
    assert view.left_id in err and view.right_id in err
    # Nothing was written: the refusal happens before the pipeline runs.
    assert not (tmp_path / "out" / "resolved.csv").exists()


def test_apply_under_a_two_person_pack_accepts_two_distinct_approvers(
    tmp_path: Path,
) -> None:
    from constituent_reconciler.cli import main

    _, _, session = _session(tmp_path, require_second=True)
    for view in session.views():
        session.record(view.index, APPROVED, reviewer="casey")
        session.record(view.index, APPROVED, reviewer="jordan")
    code = main(
        [
            "apply",
            "--config",
            str(EXAMPLES / "recipe-dv.toml"),
            "--decisions",
            str(tmp_path / "decisions.json"),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0


def test_apply_under_a_two_person_pack_refuses_a_file_with_no_audit_trail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from constituent_reconciler.cli import main

    # A version-1 file, or a hand-edited one, records no attribution at all.
    # Two-person approval cannot be shown from it, so it is refused rather
    # than read as "nothing is awaiting a second reviewer".
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps({"approved": [["existing:E002", "incoming:N004"]], "rejected": []}),
        encoding="utf-8",
    )
    code = main(
        [
            "apply",
            "--config",
            str(EXAMPLES / "recipe-dv.toml"),
            "--decisions",
            str(decisions_path),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2
    assert "second reviewer" in capsys.readouterr().err


def test_apply_without_a_two_person_pack_still_accepts_one_approver(
    tmp_path: Path,
) -> None:
    from constituent_reconciler.cli import main

    # The gate is the pack's, not a new blanket requirement: a single-reviewer
    # file applied under the default pack keeps working.
    _, _, session = _session(tmp_path, require_second=False)
    session.record(0, APPROVED, reviewer="casey")
    code = main(
        [
            "apply",
            "--config",
            str(EXAMPLES / "recipe.toml"),
            "--decisions",
            str(tmp_path / "decisions.json"),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 0


def test_approved_without_second_approval_counts_distinct_identities() -> None:
    def entry(reviewer: str, verdict: str = APPROVED) -> dict[str, str]:
        return {"reviewer": reviewer, "verdict": verdict, "decided_at": "2026-01-01T00:00:00+00:00"}

    # Two distinct people: satisfied.
    assert not approved_without_second_approval(
        {"approved": [["a", "b"]], "audit": {"a|b": [entry("casey"), entry("jordan")]}}
    )
    # One person recording twice under a spelling variant is still one person.
    assert approved_without_second_approval(
        {"approved": [["a", "b"]], "audit": {"a|b": [entry("Casey Ng"), entry("casey  ng")]}}
    ) == ["a|b"]
    # A rejection is not an approval, so it does not count toward the two.
    assert approved_without_second_approval(
        {"approved": [["a", "b"]], "audit": {"a|b": [entry("casey"), entry("jordan", REJECTED)]}}
    ) == ["a|b"]
    # An audit key written in the other order still matches its approved pair.
    assert not approved_without_second_approval(
        {"approved": [["b", "a"]], "audit": {"a|b": [entry("casey"), entry("jordan")]}}
    )
    # A missing or unusable audit section proves nothing, so it is not a pass.
    assert approved_without_second_approval({"approved": [["a", "b"]]}) == ["a|b"]
    assert approved_without_second_approval({"approved": [["a", "b"]], "audit": "nope"}) == ["a|b"]
    # Nothing approved, nothing to confirm.
    assert not approved_without_second_approval({"approved": [], "audit": {}})


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


# -- planted reviewer calibration (EXP-09) -----------------------------------


def _calibrated_session(tmp_path: Path, count: int = 3) -> ReviewSession:
    result, recipe, _ = _session(tmp_path)
    return ReviewSession(
        result,
        recipe.fields,
        tmp_path / "calibrated-decisions.json",
        reviewer="casey",
        calibration=generate_calibration_pairs(count, recipe.fields),
    )


def test_calibration_is_deterministic_and_visibly_synthetic() -> None:
    fields = ("first_name", "last_name", "dob", "email", "phone")
    planted = generate_calibration_pairs(4, fields)
    assert planted == generate_calibration_pairs(4, fields)
    assert {item.known_answer for item in planted} == {True, False}
    assert all(item.left.source == "calibration" for item in planted)
    assert all("example.invalid" in item.left.raw["email"] for item in planted)


def test_calibration_generator_rejects_a_negative_count() -> None:
    with pytest.raises(ValueError, match="zero or positive"):
        generate_calibration_pairs(-1, ("first_name", "last_name"))


def test_calibration_pairs_never_reach_decisions_or_audit(tmp_path: Path) -> None:
    session = _calibrated_session(tmp_path)
    for view in session.views():
        session.record(view.index, APPROVED)
    payload = session.to_decisions()
    approved = payload["approved"]
    assert isinstance(approved, list) and len(approved) == 2
    blob = json.dumps(payload)
    assert "CAL-" not in blob
    assert "Calibration" not in blob


def test_calibration_results_are_per_reviewer_and_ignore_two_person_gate(
    tmp_path: Path,
) -> None:
    result, recipe, _ = _session(tmp_path)
    planted = generate_calibration_pairs(2, recipe.fields)
    session = ReviewSession(
        result,
        recipe.fields,
        tmp_path / "decisions.json",
        reviewer="casey",
        require_second_reviewer=True,
        calibration=planted,
    )
    synthetic = [view for view in session.views() if view.synthetic]
    for view in synthetic:
        answer = next(
            item.known_answer
            for item in planted
            if item.pair.key() == frozenset((view.left_id, view.right_id))
        )
        session.record(view.index, APPROVED if answer else REJECTED)
    verdicts, answers = session.calibration_results()
    assert verdicts == answers
    assert len(verdicts) == 2


def test_calibration_banner_is_disclosed_without_marking_one_pair(tmp_path: Path) -> None:
    session = _calibrated_session(tmp_path, count=3)
    overview = handle_get(session, "/", _context()).body
    assert "3 planted known-answer pairs for calibration" in overview
    planted_view = next(view for view in session.views() if view.synthetic)
    page = handle_get(session, f"/pair/{planted_view.index}", _context()).body
    assert page.count("planted known-answer") == 1


# -- attributed field correction (EXP-01) -----------------------------------


def test_correction_is_attributed_and_kept_out_of_decisions(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    view = session.views()[0]
    session.correct(view.index, field="dob", side="right", value="1972-03-08")

    assert session.verdict(view.index) == APPROVED
    correction = session.corrections_for(view.index)["dob"]
    assert correction.reviewer == "casey"
    assert correction.record_id == view.right_id
    decisions_blob = session.decisions_path.read_text(encoding="utf-8")
    corrections_blob = session.corrections_path.read_text(encoding="utf-8")
    assert "1972-03-08" not in decisions_blob
    assert "1972-03-08" in corrections_blob
    assert correction.corrected_at in corrections_blob


def test_correction_invalidates_prior_approval_and_requires_fresh_second_review(
    tmp_path: Path,
) -> None:
    result, recipe, first = _session(tmp_path, require_second=True, reviewer="casey")
    first.record(0, APPROVED, reviewer="jordan")
    first.correct(0, field="dob", side="right", value="1972-03-08")
    assert first.verdict(0) == AWAITING_SECOND
    assert [entry.reviewer for entry in first.audit(0)] == ["casey"]

    second = ReviewSession(
        result,
        recipe.fields,
        first.decisions_path,
        reviewer="jordan",
        require_second_reviewer=True,
    )
    assert second.corrections_for(0)["dob"].value == "1972-03-08"
    second.record(0, APPROVED)
    assert second.verdict(0) == APPROVED
    assert second.corrections_for(0)["dob"].reviewer == "casey"


def test_reject_or_correcting_reviewers_plain_approval_abandons_correction(
    tmp_path: Path,
) -> None:
    _, _, session = _session(tmp_path)
    session.correct(0, field="dob", side="right", value="1972-03-08")
    session.record(0, APPROVED)
    assert session.corrections_for(0) == {}
    session.correct(0, field="dob", side="right", value="1972-03-08")
    session.record(0, REJECTED, reviewer="jordan")
    assert session.corrections_for(0) == {}


def test_correction_handler_validates_and_render_shows_attribution(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    bad = handle_post(
        session,
        "/pair/0",
        {
            "verdict": ["correct"],
            "field": ["dob"],
            "side": ["right"],
            "value": ["   "],
            "token": [session.token],
        },
        _context(),
    )
    assert bad.status == HTTPStatus.BAD_REQUEST
    session.correct(0, field="dob", side="right", value="1972-03-08")
    html = render_pair(session, session.views()[0], apply_command="constituent-reconcile apply")
    assert "Corrected to 1972-03-08 by casey" in html
    assert "corrections.json" in html
