"""The offline auditor's trace (``constituent-reconcile explain``).

The tests are organised around the ways a trace could look like an answer while
being one: a redacted rendering that still carries a field value, a ``--verify``
that agrees with a tampered log, an absent artifact reported as an absent fact,
and an unknown id traced as an empty cluster.

Fixtures are built as a synthetic output directory rather than by running the
pipeline. That keeps them fast, and more importantly it lets each test put the
run into a state the demo cannot reach -- a missing decisions file, a broken
hash chain, a manifest edited after the fact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from constituent_reconciler import explain as explain_module
from constituent_reconciler import manifest as manifest_module
from constituent_reconciler import provenance as provenance_module
from constituent_reconciler.cli import main
from constituent_reconciler.destruction import NOT_DESTROYED, PII_ARTIFACTS

#: Planted in every place a mapped field value can enter the trace. A redacted
#: rendering that contains this string has leaked, whatever else it got right.
SENTINEL = "ZZ-SENTINEL-VALUE-ZZ"

#: A second sentinel, in the review queue rather than the golden record. The two
#: values reach the trace through different readers, so one sentinel could pass
#: while the other leaked.
REVIEW_SENTINEL = "ZZ-REVIEWED-SENTINEL-ZZ"

#: A third, in a reviewer's correction.
CORRECTION_SENTINEL = "ZZ-CORRECTED-SENTINEL-ZZ"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_run(
    out_dir: Path,
    *,
    decisions: bool = True,
    corrections: bool = True,
    withheld: bool = False,
    provenance: bool = True,
    manifest: bool = True,
    review_queue: bool = True,
    auto_merges: bool = True,
) -> Path:
    """A synthetic run directory holding one merged cluster and one review pair.

    The cluster ``existing:E001`` has three members so that a transitive edge
    exists: E001-N001 and E001-N002 are scored, N001-N002 is not. A two-member
    fixture would have exactly one edge and no way to lose the distinction.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    _write(
        out_dir / "resolved.csv",
        "cluster_id,primary,members,consent,first_name,last_name\n"
        f"existing:E001,existing:E001,existing:E001|incoming:N001|incoming:N002,granted,{SENTINEL},reyes\n"
        "existing:E009,existing:E009,existing:E009,granted,solo,quinn\n",
    )
    if review_queue:
        _write(
            out_dir / "review_queue.csv",
            "left,right,probability,left_source,right_source,"
            "first_name_left,first_name_right,last_name_left,last_name_right\n"
            f"existing:E001,incoming:N002,0.8722,existing,incoming,{SENTINEL},"
            f"{REVIEW_SENTINEL},Reyes,Reyes\n",
        )
    if auto_merges:
        _write(
            out_dir / "auto_merges.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "auto_threshold": 0.97,
                    "review_threshold": 0.8,
                    "pair_count": 1,
                    "pairs": [
                        {
                            "left": "existing:E001",
                            "right": "incoming:N001",
                            "probability": 0.9917,
                            "band": "auto",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
        )
    if decisions:
        _write(
            out_dir / "decisions.json",
            json.dumps(
                {
                    "decisions_schema": 2,
                    "approved": [["existing:E001", "incoming:N002"]],
                    "rejected": [],
                    "audit": {
                        "existing:E001|incoming:N002": [
                            {
                                "reviewer": "Dana Okafor",
                                "verdict": "approved",
                                "decided_at": "2026-09-01T10:00:00+00:00",
                            }
                        ]
                    },
                },
                indent=2,
            )
            + "\n",
        )
    if corrections:
        _write(
            out_dir / "corrections.json",
            json.dumps(
                {
                    "corrections": [
                        {
                            "left": "existing:E001",
                            "right": "incoming:N002",
                            "side": "left",
                            "field": "first_name",
                            "value": CORRECTION_SENTINEL,
                            "reviewer": "Dana Okafor",
                            "corrected_at": "2026-09-01T10:01:00+00:00",
                        }
                    ]
                },
                indent=2,
            )
            + "\n",
        )
    if withheld:
        _write(
            out_dir / "withheld.csv",
            "cluster_id,members,reason\n"
            "existing:E009,existing:E009,consent absent for the declared destination\n",
        )
    stored_manifest: dict[str, object] = {
        "created_at": "2026-09-01T09:00:00+00:00",
        "recipe_hash": "a" * 64,
        "input_hashes": {"existing.csv": "b" * 64},
        "package_version": "0.8.0",
        "policy_pack": "default",
        "thresholds": {"prior": 0.01, "auto": 0.97, "review": 0.8},
    }
    if manifest:
        _write(
            out_dir / "run_manifest.json",
            json.dumps(stored_manifest, indent=2, sort_keys=True) + "\n",
        )
    if provenance:
        log = provenance_module.ProvenanceLog(out_dir / "provenance.jsonl")
        log.append_run_start(manifest_module.manifest_hash(stored_manifest))
        log.append(
            action="written",
            record_id="existing:E001",
            members=["existing:E001", "incoming:N001", "incoming:N002"],
            consent=True,
            payload={"first_name": SENTINEL, "last_name": "reyes"},
            external_id="existing:E001",
            field_sources={"first_name": "existing:E001", "last_name": "incoming:N001"},
            fill_policy="survivor-then-lowest-id",
        )
    return out_dir


# ---------------------------------------------------------------------------
# the trace an auditor asked for
# ---------------------------------------------------------------------------


def test_a_merged_cluster_names_its_members_the_probability_and_the_entry_hash(
    tmp_path: Path,
) -> None:
    trace = explain_module.explain(_build_run(tmp_path / "out"), cluster="existing:E001")

    assert [m.record_id for m in trace.members] == [
        "existing:E001",
        "incoming:N001",
        "incoming:N002",
    ]
    assert [m.source for m in trace.members] == ["existing", "incoming", "incoming"]

    auto_edge = next(
        e for e in trace.edges if {e.left, e.right} == {"existing:E001", "incoming:N001"}
    )
    assert auto_edge.probability == pytest.approx(0.9917)
    assert auto_edge.band == "auto"

    assert not isinstance(trace.provenance, explain_module.Absent)
    assert len(trace.provenance.entry_hash) == 64
    assert trace.provenance.external_id == "existing:E001"
    assert trace.provenance.field_sources["last_name"] == "incoming:N001"


def test_a_reviewed_edge_names_the_reviewer_and_when_they_decided(tmp_path: Path) -> None:
    trace = explain_module.explain(_build_run(tmp_path / "out"), cluster="existing:E001")

    edge = next(e for e in trace.edges if {e.left, e.right} == {"existing:E001", "incoming:N002"})
    assert edge.verdict == "approved"
    assert not isinstance(edge.reviews, explain_module.Absent)
    assert edge.reviews[0]["reviewer"] == "Dana Okafor"
    assert edge.reviews[0]["decided_at"] == "2026-09-01T10:00:00+00:00"


def test_a_transitive_pair_is_named_as_one_not_given_an_invented_probability(
    tmp_path: Path,
) -> None:
    # N001 and N002 are in one cluster without ever having been scored against
    # each other. A probability here would be a claim about a comparison that
    # never happened.
    trace = explain_module.explain(_build_run(tmp_path / "out"), cluster="existing:E001")

    edge = next(e for e in trace.edges if {e.left, e.right} == {"incoming:N001", "incoming:N002"})
    assert edge.band == "transitive"
    assert isinstance(edge.probability, explain_module.Absent)
    assert "transitively" in str(edge.probability)


def test_a_record_id_resolves_to_its_cluster(tmp_path: Path) -> None:
    trace = explain_module.explain(_build_run(tmp_path / "out"), record="incoming:N002")
    assert trace.cluster_id == "existing:E001"
    assert trace.asked_for == "record incoming:N002"


# ---------------------------------------------------------------------------
# an unknown id is refused, never traced as an empty cluster
# ---------------------------------------------------------------------------


def test_an_unknown_cluster_id_is_refused(tmp_path: Path) -> None:
    with pytest.raises(explain_module.ExplainError, match="no cluster 'nope'"):
        explain_module.explain(_build_run(tmp_path / "out"), cluster="nope")


def test_an_unknown_record_id_is_refused(tmp_path: Path) -> None:
    with pytest.raises(explain_module.ExplainError, match="no record 'incoming:N404'"):
        explain_module.explain(_build_run(tmp_path / "out"), record="incoming:N404")


def test_a_missing_resolved_csv_is_refused_rather_than_read_as_no_clusters(
    tmp_path: Path,
) -> None:
    out = _build_run(tmp_path / "out")
    (out / "resolved.csv").unlink()
    with pytest.raises(explain_module.ExplainError, match="dry-run"):
        explain_module.explain(out, cluster="existing:E001")


def test_a_run_with_no_edge_evidence_at_all_is_refused(tmp_path: Path) -> None:
    # Without both files every edge would render "transitive", which would state
    # something about the matcher that these bytes do not support.
    out = _build_run(tmp_path / "out", review_queue=False, auto_merges=False)
    with pytest.raises(explain_module.ExplainError, match="neither auto_merges.json"):
        explain_module.explain(out, cluster="existing:E001")


def test_an_unknown_rendering_mode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(explain_module.ExplainError, match="unknown rendering mode"):
        explain_module.explain(
            _build_run(tmp_path / "out"), cluster="existing:E001", mode="partial"
        )


# ---------------------------------------------------------------------------
# the planted-sentinel redaction test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sentinel", [SENTINEL, REVIEW_SENTINEL, CORRECTION_SENTINEL])
def test_the_redacted_rendering_contains_no_mapped_field_value(
    tmp_path: Path, sentinel: str
) -> None:
    """The issue's own acceptance test, run against three different readers.

    Each sentinel enters the trace by a different path -- the golden record in
    ``resolved.csv``, a reviewed value in ``review_queue.csv``, and a reviewer's
    correction -- so a redaction that covered one and missed another cannot pass
    by covering the loudest one.
    """

    out = _build_run(tmp_path / "out")
    # The literal is passed here, not the module constant, so that changing the
    # constant cannot make the test agree with the code about the wrong string.
    trace = explain_module.explain(out, cluster="existing:E001", mode="redacted")

    markdown = explain_module.render_trace(trace)
    payload = json.dumps(explain_module.trace_payload(trace), sort_keys=True)

    assert sentinel not in markdown
    assert sentinel not in payload
    # And it is absent because it was never read, not because it was blanked:
    # nothing anywhere in the trace structure holds it.
    assert all(sentinel not in json.dumps(member.values) for member in trace.members)


def test_the_full_rendering_does_carry_those_values(tmp_path: Path) -> None:
    """The control on the test above: a redaction that hid everything would pass
    the sentinel check while making the full rendering useless."""

    out = _build_run(tmp_path / "out")
    markdown = explain_module.render_trace(explain_module.explain(out, cluster="existing:E001"))
    assert SENTINEL in markdown
    assert REVIEW_SENTINEL in markdown
    assert CORRECTION_SENTINEL in markdown


def test_a_document_span_survives_redaction_because_it_is_not_a_value(tmp_path: Path) -> None:
    out = _build_run(tmp_path / "out", review_queue=False)
    _write(
        out / "review_queue.csv",
        "left,right,probability,left_source,right_source,"
        "first_name_left,first_name_right,first_name_left_span,first_name_right_span\n"
        f"existing:E001,incoming:N002,0.8722,existing,incoming,{SENTINEL},x,"
        "intake.pdf:p2,intake.pdf:p3\n",
    )
    trace = explain_module.explain(out, cluster="existing:E001", mode="redacted")
    markdown = explain_module.render_trace(trace)
    # The format is SourceSpan.__str__'s own ("<file>:p<page>"), so the fixture
    # cannot pass on a span shape the pipeline never writes.
    assert "intake.pdf:p2" in markdown
    assert SENTINEL not in markdown


# ---------------------------------------------------------------------------
# absence is never rendered as a fact
# ---------------------------------------------------------------------------


def test_a_missing_decisions_file_and_an_undecided_pair_read_differently(
    tmp_path: Path,
) -> None:
    with_file = explain_module.explain(_build_run(tmp_path / "a"), cluster="existing:E001")
    without_file = explain_module.explain(
        _build_run(tmp_path / "b", decisions=False), cluster="existing:E001"
    )

    undecided = next(
        e for e in with_file.edges if {e.left, e.right} == {"existing:E001", "incoming:N001"}
    )
    same_edge = next(
        e for e in without_file.edges if {e.left, e.right} == {"existing:E001", "incoming:N001"}
    )
    assert str(undecided.verdict) == "not recorded (no reviewer decided this pair)"
    assert str(same_edge.verdict) == "not recorded (this run has no decisions.json)"
    assert str(undecided.verdict) != str(same_edge.verdict)


def test_a_missing_corrections_file_and_an_empty_one_read_differently(tmp_path: Path) -> None:
    absent = explain_module.explain(
        _build_run(tmp_path / "a", corrections=False), cluster="existing:E001"
    )
    assert isinstance(absent.corrections, explain_module.Absent)
    assert "has no corrections.json" in render_of(absent)

    present = explain_module.explain(_build_run(tmp_path / "b"), cluster="existing:E009")
    assert present.corrections == ()
    assert "exists and records none for this cluster" in render_of(present)


def test_a_withheld_cluster_names_the_reason_and_an_unwithheld_one_says_so(
    tmp_path: Path,
) -> None:
    out = _build_run(tmp_path / "out", withheld=True)
    withheld = explain_module.explain(out, cluster="existing:E009")
    assert withheld.withheld_reason == "consent absent for the declared destination"

    written = explain_module.explain(out, cluster="existing:E001")
    assert isinstance(written.withheld_reason, explain_module.Absent)
    assert "not on the withheld list" in str(written.withheld_reason)


def test_an_unreadable_probability_is_not_reported_as_zero(tmp_path: Path) -> None:
    out = _build_run(tmp_path / "out", auto_merges=False)
    _write(
        out / "auto_merges.json",
        json.dumps(
            {"pairs": [{"left": "existing:E001", "right": "incoming:N001", "probability": "n/a"}]}
        ),
    )
    trace = explain_module.explain(out, cluster="existing:E001")
    edge = next(e for e in trace.edges if {e.left, e.right} == {"existing:E001", "incoming:N001"})
    assert isinstance(edge.probability, explain_module.Absent)
    assert "unreadable probability" in str(edge.probability)


def test_a_run_with_no_provenance_log_says_so(tmp_path: Path) -> None:
    out = _build_run(tmp_path / "out", provenance=False)
    trace = explain_module.explain(out, cluster="existing:E001")
    assert isinstance(trace.provenance, explain_module.Absent)
    assert "has no provenance.jsonl" in str(trace.provenance)


def render_of(trace: explain_module.Trace) -> str:
    return explain_module.render_trace(trace)


# ---------------------------------------------------------------------------
# --verify re-derives rather than restating
# ---------------------------------------------------------------------------


def test_verify_confirms_an_intact_run(tmp_path: Path) -> None:
    trace = explain_module.explain(
        _build_run(tmp_path / "out"), cluster="existing:E001", verify=True
    )
    assert trace.verification is not None
    assert trace.verification.chain_ok is True
    assert trace.verification.entry_hash_ok is True
    assert trace.verification.manifest_hash_ok is True
    assert trace.verification.ok is True


def test_verify_fails_on_an_entry_edited_after_the_fact(tmp_path: Path) -> None:
    out = _build_run(tmp_path / "out")
    path = out / "provenance.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["consent"] = False  # the field an auditor would most want to trust
    lines[1] = json.dumps(entry, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    trace = explain_module.explain(out, cluster="existing:E001", verify=True)
    assert trace.verification is not None
    assert trace.verification.entry_hash_ok is False
    assert trace.verification.ok is False


def test_verify_fails_when_the_manifest_changed_after_the_run(tmp_path: Path) -> None:
    # The log is internally consistent; only the manifest it claims to describe
    # has moved. A verifier that checked the chain alone would pass this.
    out = _build_run(tmp_path / "out")
    stored = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    stored["thresholds"]["auto"] = 0.5
    _write(out / "run_manifest.json", json.dumps(stored, indent=2, sort_keys=True) + "\n")

    trace = explain_module.explain(out, cluster="existing:E001", verify=True)
    assert trace.verification is not None
    assert trace.verification.chain_ok is True
    assert trace.verification.entry_hash_ok is True
    assert trace.verification.manifest_hash_ok is False
    assert trace.verification.ok is False


def test_verify_does_not_pass_a_trace_it_could_not_check(tmp_path: Path) -> None:
    out = _build_run(tmp_path / "out", provenance=False)
    trace = explain_module.explain(out, cluster="existing:E001", verify=True)
    assert trace.verification is not None
    assert isinstance(trace.verification.entry_hash_ok, explain_module.Absent)
    assert trace.verification.ok is False


def test_verify_reports_a_broken_chain_as_broken(tmp_path: Path) -> None:
    out = _build_run(tmp_path / "out")
    path = out / "provenance.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[1] + "\n", encoding="utf-8")  # first entry removed

    trace = explain_module.explain(out, cluster="existing:E001", verify=True)
    assert trace.verification is not None
    assert trace.verification.chain_ok is False
    assert trace.verification.ok is False


# ---------------------------------------------------------------------------
# writing, and the destruction inventory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "output_format", "expected"),
    [
        ("full", "markdown", "explain_trace.md"),
        ("full", "json", "explain_trace.json"),
        ("redacted", "markdown", "explain_trace_redacted.md"),
        ("redacted", "json", "explain_trace_redacted.json"),
    ],
)
def test_write_trace_lands_in_the_run_directory_under_a_fixed_name(
    tmp_path: Path, mode: str, output_format: str, expected: str
) -> None:
    out = _build_run(tmp_path / "out")
    trace = explain_module.explain(out, cluster="existing:E001", mode=mode)
    path = explain_module.write_trace(trace, output_format)
    assert path == out / expected
    assert path.read_text(encoding="utf-8")


def test_every_written_trace_is_classified_for_destruction() -> None:
    assert "explain_trace.md" in PII_ARTIFACTS
    assert "explain_trace.json" in PII_ARTIFACTS
    assert "explain_trace_redacted.md" in NOT_DESTROYED
    assert "explain_trace_redacted.json" in NOT_DESTROYED


def test_the_json_payload_states_which_rendering_it_is(tmp_path: Path) -> None:
    out = _build_run(tmp_path / "out")
    full = explain_module.trace_payload(explain_module.explain(out, cluster="existing:E001"))
    redacted = explain_module.trace_payload(
        explain_module.explain(out, cluster="existing:E001", mode="redacted")
    )
    assert full["rendering"] == "full"
    assert redacted["rendering"] == "redacted"
    # A consumer must never have to infer redaction from an empty mapping.
    assert redacted["members"][0]["values"] == "withheld from the redacted rendering"  # type: ignore[index]


# ---------------------------------------------------------------------------
# the CLI surface
# ---------------------------------------------------------------------------


def test_cli_prints_a_trace_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _build_run(tmp_path / "out")
    assert main(["explain", "--out", str(out), "--cluster", "existing:E001"]) == 0
    assert "# Trace for existing:E001" in capsys.readouterr().out


def test_cli_exits_non_zero_on_an_unknown_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _build_run(tmp_path / "out")
    assert main(["explain", "--out", str(out), "--cluster", "nope"]) != 0
    assert "explain error" in capsys.readouterr().err


def test_cli_exits_non_zero_when_verification_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _build_run(tmp_path / "out")
    stored = json.loads((out / "run_manifest.json").read_text(encoding="utf-8"))
    stored["policy_pack"] = "dv"
    _write(out / "run_manifest.json", json.dumps(stored, indent=2, sort_keys=True) + "\n")
    assert main(["explain", "--out", str(out), "--cluster", "existing:E001", "--verify"]) == 1
    assert "verification did not pass" in capsys.readouterr().err


def test_cli_json_and_write_land_the_expected_artifact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _build_run(tmp_path / "out")
    code = main(
        [
            "explain",
            "--out",
            str(out),
            "--cluster",
            "existing:E001",
            "--redact",
            "--format",
            "json",
            "--write",
        ]
    )
    assert code == 0
    capsys.readouterr()
    written = out / "explain_trace_redacted.json"
    assert written.is_file()
    assert SENTINEL not in written.read_text(encoding="utf-8")


def test_cli_refuses_to_be_asked_for_both_a_cluster_and_a_record(tmp_path: Path) -> None:
    out = _build_run(tmp_path / "out")
    with pytest.raises(SystemExit):
        main(["explain", "--out", str(out), "--cluster", "existing:E001", "--record", "x"])
