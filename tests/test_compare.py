"""Tests for the read-only migration cutover comparison (UC-02, PR 1).

Each acceptance criterion from docs/NOVEL-USE-CASES-PLAN.md is held as a test
here: every row accounted for exactly once, an exact duplicate reaching one
identity while a same-name/different-DOB pair stays reviewable, a count-only
summary with no field values, order-independent outcomes, and the
merge-blocking invariant that no compare code path can construct a write
connector, in the spirit of tests/test_no_egress.py.
"""

from __future__ import annotations

import csv
import inspect
import json
from pathlib import Path

import pytest

from constituent_reconciler import compare
from constituent_reconciler.cli import main
from constituent_reconciler.compare import CompareError, CompareResult, Identity, Side
from constituent_reconciler.config import NormalizeConfig, Recipe
from constituent_reconciler.manifest import file_digest
from constituent_reconciler.models import IngestReport, Record, SkippedFile
from constituent_reconciler.schema import MIGRATION_SUMMARY_SCHEMA_VERSION

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compare"


@pytest.fixture(scope="module")
def result() -> CompareResult:
    left = compare.load_side(FIXTURES / "recipe-left.toml", label="left")
    right = compare.load_side(FIXTURES / "recipe-right.toml", label="right")
    return compare.run_compare(left, right)


def test_every_row_on_each_side_is_accounted_for_exactly_once(result: CompareResult) -> None:
    members = [
        member
        for identity in result.identities
        for member in identity.left_members + identity.right_members
    ]
    # Exactly once: the flat member list has no repeats and covers every record.
    assert sorted(members) == sorted(result.records)
    assert result.left_count == 5
    assert result.right_count == 4
    matched, left_only, right_only = compare._status_counts(result)
    assert matched + left_only + right_only == len(result.identities)


def test_an_exact_duplicate_reaches_one_identity(result: CompareResult) -> None:
    maria_ids = {
        unique_id
        for unique_id, record in result.records.items()
        if record.raw.get("first_name") == "Maria"
    }
    # Two identical legacy rows plus one target row.
    assert len(maria_ids) == 3
    holding = [
        identity
        for identity in result.identities
        if maria_ids & set(identity.left_members + identity.right_members)
    ]
    assert len(holding) == 1
    assert holding[0].status == compare.MATCHED
    assert set(holding[0].left_members + holding[0].right_members) == maria_ids
    assert not holding[0].conflicts


def test_same_name_different_dob_stays_reviewable_not_merged(result: CompareResult) -> None:
    devon = [
        identity
        for identity in result.identities
        if any(
            result.records[member].raw.get("first_name") == "Devon"
            for member in identity.left_members + identity.right_members
        )
    ]
    # Not auto-merged: the two Devons remain separate identities, both marked
    # as needing review, connected by exactly one undecided pair.
    assert len(devon) == 2
    assert all(identity.ambiguous for identity in devon)
    assert {identity.status for identity in devon} == {compare.LEFT_ONLY, compare.RIGHT_ONLY}
    assert len(result.review_pairs) == 1


def test_value_conflicts_are_flagged_on_matched_identities(result: CompareResult) -> None:
    sam = next(
        identity
        for identity in result.identities
        if any(
            result.records[member].raw.get("last_name") == "Okafor"
            for member in identity.left_members + identity.right_members
        )
    )
    assert sam.status == compare.MATCHED
    assert set(sam.conflicts) == {"email"}
    left_display, right_display = sam.conflicts["email"]
    assert "sam.okafor@example.org" in left_display
    assert "s.okafor@new.example.org" in right_display


def test_migration_summary_contains_no_field_values(result: CompareResult, tmp_path: Path) -> None:
    path = compare.write_migration_summary(result, tmp_path)
    blob = path.read_text(encoding="utf-8")
    payload = json.loads(blob)
    assert payload["schema_version"] == MIGRATION_SUMMARY_SCHEMA_VERSION
    for record in result.records.values():
        for value in list(record.raw.values()) + list(record.normalized.values()):
            if value:
                assert value not in blob
        assert record.unique_id not in blob
    # The counts partition, and ambiguity is reported beside the partition.
    assert (
        payload["matched_identities"]
        + payload["left_only_identities"]
        + payload["right_only_identities"]
        == payload["identities"]
    )
    assert payload["left_records"] == 5
    assert payload["right_records"] == 4
    assert payload["ambiguous_identities"] == 2
    assert payload["review_pairs"] == 1
    assert payload["identities_with_conflicts"] == 1
    assert payload["conflict_counts"] == {"email": 1}


def test_reordering_an_export_changes_no_ids_or_outcomes(
    result: CompareResult, tmp_path: Path
) -> None:
    for name in ("left.csv", "right.csv"):
        lines = (FIXTURES / name).read_text(encoding="utf-8").strip().splitlines()
        reordered = "\n".join([lines[0], *reversed(lines[1:])]) + "\n"
        (tmp_path / name).write_text(reordered, encoding="utf-8")
    for name in ("recipe-left.toml", "recipe-right.toml"):
        (tmp_path / name).write_text(
            (FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    left = compare.load_side(tmp_path / "recipe-left.toml", label="left")
    right = compare.load_side(tmp_path / "recipe-right.toml", label="right")
    reordered_result = compare.run_compare(left, right)

    assert set(reordered_result.records) == set(result.records)

    def canonical(res: CompareResult) -> set[tuple[str, bool, frozenset[str], frozenset[str]]]:
        return {
            (
                identity.status,
                identity.ambiguous,
                frozenset(identity.left_members),
                frozenset(identity.right_members),
            )
            for identity in res.identities
        }

    assert canonical(reordered_result) == canonical(result)
    assert compare.summary_payload(reordered_result) == compare.summary_payload(result)


def test_compare_cannot_construct_a_write_connector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The left recipe names a live CiviCRM target on purpose. Every route to a
    # connector is replaced with a refusal, so if any compare code path tried
    # to build or call one, this test would fail loudly.
    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("the compare command must not touch a connector or the network")

    monkeypatch.setattr("constituent_reconciler.connectors.get_factory", refuse)
    monkeypatch.setattr("constituent_reconciler.pipeline.get_factory", refuse)
    monkeypatch.setattr("constituent_reconciler.pipeline.build_connector", refuse)
    monkeypatch.setattr("urllib.request.urlopen", refuse)

    code = main(
        [
            "compare",
            "--left",
            str(FIXTURES / "recipe-left-live.toml"),
            "--right",
            str(FIXTURES / "recipe-right.toml"),
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 0
    assert (tmp_path / "cutover_report.csv").exists()
    assert (tmp_path / "cutover_review.csv").exists()
    assert (tmp_path / "migration_summary.json").exists()
    assert (tmp_path / "compare_manifest.json").exists()
    # No write-side artifact appears: the live [output] section was never used.
    assert not (tmp_path / "resolved.csv").exists()
    assert not (tmp_path / "provenance.jsonl").exists()
    # The terminal summary stays count-only.
    captured = capsys.readouterr().out
    assert "identities" in captured
    assert "Maria" not in captured
    assert "Okafor" not in captured


def test_compare_module_never_names_the_connector_registry() -> None:
    # Structural half of the invariant: the compare module cannot even name
    # the package that constructs write targets.
    source = inspect.getsource(compare)
    assert "connectors" not in source


def test_cutover_report_lists_every_identity_with_values_and_flags(
    result: CompareResult, tmp_path: Path
) -> None:
    path = compare.write_cutover_report(result, tmp_path)
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert len(rows) == len(result.identities)
    sam_row = next(row for row in rows if row["last_name_left"] == "Okafor")
    assert sam_row["status"] == compare.MATCHED
    assert sam_row["email_conflict"] == "yes"
    assert sam_row["email_left"] == "sam.okafor@example.org"
    assert sam_row["email_right"] == "s.okafor@new.example.org"
    devon_rows = [
        row for row in rows if "Devon" in (row["first_name_left"], row["first_name_right"])
    ]
    assert len(devon_rows) == 2
    assert all(row["needs_review"] == "yes" for row in devon_rows)


def test_cutover_review_carries_the_undecided_pair(result: CompareResult, tmp_path: Path) -> None:
    path = compare.write_cutover_review(result, tmp_path)
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert len(rows) == 1
    row = rows[0]
    assert {row["left_side"], row["right_side"]} == {"left", "right"}
    assert row["dob_left"] == "1990-04-12"
    assert row["dob_right"] == "1990-04-21"
    assert 0.80 <= float(row["probability"]) < 0.97


def test_compare_manifest_binds_both_recipes_and_inputs(
    result: CompareResult, tmp_path: Path
) -> None:
    left = compare.load_side(FIXTURES / "recipe-left.toml", label="left")
    right = compare.load_side(FIXTURES / "recipe-right.toml", label="right")
    path = compare.write_compare_manifest(
        compare.build_compare_manifest(left, right, result), tmp_path
    )
    blob = path.read_text(encoding="utf-8")
    payload = json.loads(blob)
    assert payload["left"]["recipe_hash"] == file_digest(FIXTURES / "recipe-left.toml")
    assert payload["right"]["recipe_hash"] == file_digest(FIXTURES / "recipe-right.toml")
    assert payload["left"]["input_hashes"] == {"left.csv": file_digest(FIXTURES / "left.csv")}
    assert payload["right"]["input_hashes"] == {"right.csv": file_digest(FIXTURES / "right.csv")}
    assert payload["left"]["mapping"]["first_name"] == "First"
    assert payload["right"]["mapping"]["first_name"] == "given_name"
    assert payload["schema_versions"]["migration_summary"] == MIGRATION_SUMMARY_SCHEMA_VERSION
    # Digests and column names only: no record value enters the manifest.
    for record in result.records.values():
        for value in record.raw.values():
            if value:
                assert value not in blob


def test_a_bare_csv_side_with_canonical_headers_compares(tmp_path: Path) -> None:
    target = tmp_path / "target.csv"
    target.write_text("first_name,last_name,dob\nMaria,Lopez,1985-03-02\n", encoding="utf-8")
    left = compare.load_side(FIXTURES / "recipe-left.toml", label="left")
    right = compare.load_side(target, label="right")
    assert right.bare is True
    outcome = compare.run_compare(left, right)
    # Only fields both sides map are compared; the left-only email column
    # cannot match or conflict here.
    assert outcome.fields == ("first_name", "last_name", "dob")
    matched, left_only, right_only = compare._status_counts(outcome)
    assert matched == 1
    assert right_only == 0
    assert outcome.right_count == 1


def test_a_bare_csv_without_name_columns_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("Given,Surname\nMaria,Lopez\n", encoding="utf-8")
    with pytest.raises(CompareError, match="first_name and last_name"):
        compare.load_side(bad, label="right")


def test_an_unreadable_side_argument_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CompareError, match="cannot read the left side"):
        compare.load_side(tmp_path / "records.xlsx", label="left")


def test_a_side_recipe_with_an_existing_input_is_refused(tmp_path: Path) -> None:
    recipe_path = tmp_path / "recipe.toml"
    recipe_path.write_text(
        "\n".join(
            [
                "[input]",
                'incoming = "a.csv"',
                'existing = "b.csv"',
                "",
                "[mapping]",
                'first_name = "First"',
                'last_name = "Last"',
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(CompareError, match="input.existing"):
        compare.load_side(recipe_path, label="left")


def test_recipes_that_disagree_on_thresholds_fail_closed(tmp_path: Path) -> None:
    variant = tmp_path / "recipe-right.toml"
    text = (FIXTURES / "recipe-right.toml").read_text(encoding="utf-8")
    variant.write_text(text.replace("auto = 0.97", "auto = 0.99"), encoding="utf-8")
    left = compare.load_side(FIXTURES / "recipe-left.toml", label="left")
    right = compare.load_side(variant, label="right")
    with pytest.raises(CompareError, match="disagree on matcher thresholds"):
        compare.run_compare(left, right)


def test_a_bare_side_adopts_the_recipe_sides_thresholds(tmp_path: Path) -> None:
    bare_csv = tmp_path / "bare.csv"
    bare_csv.write_text("first_name,last_name\nMaria,Lopez\n", encoding="utf-8")
    variant = tmp_path / "recipe-right.toml"
    text = (FIXTURES / "recipe-right.toml").read_text(encoding="utf-8")
    variant.write_text(text.replace("auto = 0.97", "auto = 0.99"), encoding="utf-8")
    bare = compare.load_side(bare_csv, label="left")
    recipe_side = compare.load_side(variant, label="right")
    assert compare._resolve_thresholds(bare, recipe_side) == (0.01, 0.99, 0.80)


def test_an_address_backend_mismatch_is_refused_when_address_is_compared() -> None:
    def recipe(backend: str) -> Recipe:
        return Recipe(
            incoming=Path("unused.csv"),
            mapping={name: name for name in ("first_name", "last_name", "address")},
            fields=("first_name", "last_name", "address"),
            normalize=NormalizeConfig(address_backend=backend),
        )

    left = Side(label="left", recipe=recipe("deterministic"), bare=True)
    right = Side(label="right", recipe=recipe("libpostal"), bare=True)
    with pytest.raises(CompareError, match="address_backend"):
        compare.run_compare(left, right)


def test_row_accounting_rejects_a_dropped_doubled_or_stray_record() -> None:
    records = {
        "L1": Record(unique_id="L1", source="left", raw={}),
        "R1": Record(unique_id="R1", source="right", raw={}),
    }
    both = Identity(
        identity_id="L1",
        status=compare.MATCHED,
        left_members=("L1",),
        right_members=("R1",),
        ambiguous=False,
        conflicts={},
    )
    only_left = Identity(
        identity_id="L1",
        status=compare.LEFT_ONLY,
        left_members=("L1",),
        right_members=(),
        ambiguous=False,
        conflicts={},
    )
    stray = Identity(
        identity_id="R2",
        status=compare.RIGHT_ONLY,
        left_members=(),
        right_members=("R2",),
        ambiguous=False,
        conflicts={},
    )
    with pytest.raises(CompareError, match="in no identity"):
        compare._check_accounting(records, [only_left])
    with pytest.raises(CompareError, match="more than one identity"):
        compare._check_accounting(records, [both, only_left])
    with pytest.raises(CompareError, match="matches no ingested record"):
        compare._check_accounting(records, [both, stray])


def test_render_compare_summary_reports_skips_and_failures() -> None:
    # A hand-built result: the renderer must answer for skipped files and for
    # values that normalized to nothing, without printing any field value.
    outcome = CompareResult(
        records={},
        pairs=(),
        clusters=(),
        identities=(),
        review_pairs=(),
        fields=("first_name", "last_name"),
        prior=0.01,
        auto_threshold=0.97,
        review_threshold=0.80,
        left_ingest=IngestReport(
            files_read=("left.csv",),
            files_skipped=(SkippedFile(path="notes.docx", reason="unsupported extension: .docx"),),
        ),
        right_ingest=IngestReport(files_read=("right.csv",)),
        normalization_failures={"dob": {"left": 2}},
    )
    text = compare.render_compare_summary(outcome)
    assert "notes.docx (unsupported extension: .docx)" in text
    assert "dob: left: 2" in text


def test_cli_compare_reports_a_bad_side_and_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "compare",
            "--left",
            str(tmp_path / "missing.toml"),
            "--right",
            str(FIXTURES / "recipe-right.toml"),
            "--out",
            str(tmp_path),
        ]
    )
    assert code == 2
    assert "compare error" in capsys.readouterr().err
