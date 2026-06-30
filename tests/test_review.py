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
from constituent_reconciler.models import RunResult
from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.review.server import (
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


def _session(
    tmp_path: Path, *, recipe_name: str = "recipe.toml", privacy: bool = False
) -> tuple[RunResult, Recipe, ReviewSession]:
    recipe = load_recipe(EXAMPLES / recipe_name)
    result = pipeline.run(recipe)
    return result, recipe, ReviewSession(
        result,
        recipe.fields,
        tmp_path / "decisions.json",
        privacy_mode=privacy,
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
    overview = handle_get(session, "/")
    assert overview.status == HTTPStatus.OK
    assert 'lang="en"' in overview.body
    assert "Review queue" in overview.body
    # The queue lists a one-line rationale beside each pair, so a reviewer can
    # triage before opening it. A blocked review pair agrees on at least one
    # field by construction.
    assert "agree on" in overview.body

    pair = handle_get(session, "/pair/0")
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
    assert handle_get(session, "/pair/99").status == HTTPStatus.NOT_FOUND
    assert handle_get(session, "/nope").status == HTTPStatus.NOT_FOUND


def test_handle_post_records_and_redirects(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    response = handle_post(session, "/pair/0", {"verdict": ["approve"]})
    assert response.status == HTTPStatus.SEE_OTHER
    assert response.location in ("/pair/1", "/")
    assert session.verdict(0) == APPROVED


def test_handle_post_bad_verdict_is_400(tmp_path: Path) -> None:
    _, _, session = _session(tmp_path)
    assert handle_post(session, "/pair/0", {"verdict": ["nope"]}).status == HTTPStatus.BAD_REQUEST
    assert session.verdict(0) is None


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

        data = urllib.parse.urlencode({"verdict": "approve"}).encode("utf-8")
        request = urllib.request.Request(base + "/pair/0", data=data, method="POST")

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: object, **kwargs: object) -> None:
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            assert error.code == HTTPStatus.SEE_OTHER
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    # The decision made over HTTP landed in the decisions file.
    payload = json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert len(payload["approved"]) == 1
