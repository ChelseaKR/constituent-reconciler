"""Tests for the FEBRL4 benchmark harness.

Nothing here reaches the network. ``fetch`` is exercised against a
pre-populated directory, and the conversion is exercised against a small inline
sample in FEBRL's exact on-disk shape (comma-space separated header, split
address columns, YYYYMMDD dates, empty cells).
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from tools.benchmark import febrl4, run_eval

# FEBRL's header, split only so the source line stays within the line limit.
HEADER = (
    "rec_id, given_name, surname, street_number, address_1, address_2, "
    "suburb, postcode, state, date_of_birth, soc_sec_id\n"
)

# Two originals and their duplicates, plus one original with no duplicate and
# one duplicate with no original, so the truth derivation has to drop both.
SAMPLE_A = (
    HEADER
    + """\
rec-1-org, michaela, neumann, 8, stanley street, miami, winston hills, 4223, nsw, 19151111, 5304218
rec-2-org, courtney, painter, 12, pinkerton circuit, , richlands, 4560, vic, 19161214, 4066625
rec-3-org, lonely, original, 1, only street, , nowhere, 1000, qld, 19700101, 1111111
"""
)

SAMPLE_B = (
    HEADER
    + """\
rec-1-dup-0, michaela, neumann, 8, stanley st, miami, winston hills, 4223, nsw, 19151111, 5304218
rec-2-dup-0, courtney, , 12, pinkerton circuit, , richlands, 4560, vic, 19161214, 4066625
rec-9-dup-0, orphan, duplicate, 2, stray street, , elsewhere, 2000, wa, 19800202, 2222222
"""
)


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "raw"
    directory.mkdir()
    (directory / "dataset4a.csv").write_text(SAMPLE_A, encoding="utf-8")
    (directory / "dataset4b.csv").write_text(SAMPLE_B, encoding="utf-8")
    return directory


def _repin(monkeypatch: pytest.MonkeyPatch, raw_dir: Path) -> None:
    """Point the pinned digests at the sample files so fetch() accepts them."""

    digests = {
        name: hashlib.sha256((raw_dir / name).read_bytes()).hexdigest()
        for name in ("dataset4a.csv", "dataset4b.csv")
    }
    monkeypatch.setattr(febrl4, "SOURCES", digests)


def test_compose_address_drops_empty_parts() -> None:
    composed = febrl4.compose_address(
        {
            "street_number": "12",
            "address_1": "pinkerton circuit",
            "address_2": "",
            "suburb": "richlands",
            "state": "vic",
            "postcode": "4560",
        }
    )
    assert composed == "12 pinkerton circuit, richlands vic 4560"


def test_compose_address_keeps_locality_between_street_and_suburb() -> None:
    composed = febrl4.compose_address(
        {
            "street_number": "8",
            "address_1": "stanley street",
            "address_2": "miami",
            "suburb": "winston hills",
            "state": "nsw",
            "postcode": "4223",
        }
    )
    assert composed == "8 stanley street, miami, winston hills nsw 4223"


def test_compose_address_of_an_empty_row_is_empty() -> None:
    """An all-blank address must not become punctuation that reads as populated."""

    assert febrl4.compose_address(dict.fromkeys(("street_number", "address_1"), "")) == ""


def test_truth_clusters_pairs_only_originals_with_duplicates(raw_dir: Path, tmp_path: Path) -> None:
    prepared = febrl4.prepare(raw_dir, tmp_path / "out")
    clusters = json.loads((tmp_path / "out" / "ground_truth.json").read_text(encoding="utf-8"))

    assert prepared.n_existing == 3
    assert prepared.n_incoming == 3
    # rec-3-org has no duplicate and rec-9-dup-0 has no original; both drop out.
    assert prepared.n_true_pairs == 2
    assert sorted(clusters["clusters"]) == [
        ["existing:rec-1-org", "incoming:rec-1-dup-0"],
        ["existing:rec-2-org", "incoming:rec-2-dup-0"],
    ]


def test_truth_clusters_namespace_ids_by_source(raw_dir: Path, tmp_path: Path) -> None:
    """Cluster members must carry the same source prefix the pipeline assigns."""

    febrl4.prepare(raw_dir, tmp_path / "out")
    clusters = json.loads((tmp_path / "out" / "ground_truth.json").read_text(encoding="utf-8"))
    for left, right in clusters["clusters"]:
        assert left.startswith("existing:")
        assert right.startswith("incoming:")


def test_prepare_passes_dob_through_unconverted(raw_dir: Path, tmp_path: Path) -> None:
    """The converter must not pre-normalize dates on the pipeline's behalf.

    Converting YYYYMMDD to ISO here would make the benchmark unable to detect a
    normalizer that cannot read the source format, which is exactly the gap it
    did detect.
    """

    febrl4.prepare(raw_dir, tmp_path / "out")
    with (tmp_path / "out" / "existing.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["dob"] == "19151111"


def test_prepare_writes_a_recipe_without_a_consent_section(raw_dir: Path, tmp_path: Path) -> None:
    """FEBRL4 has no consent column; the recipe must not invent one."""

    febrl4.prepare(raw_dir, tmp_path / "out")
    recipe = (tmp_path / "out" / "recipe.toml").read_text(encoding="utf-8")
    # Section headers only: the recipe's own comments mention [consent] to
    # explain the omission, and matching those would pass for the wrong reason.
    sections = [line.strip() for line in recipe.splitlines() if line.startswith("[")]
    assert "[consent]" not in sections
    assert "[mapping]" in sections


def test_ground_truth_declares_provenance(raw_dir: Path, tmp_path: Path) -> None:
    febrl4.prepare(raw_dir, tmp_path / "out")
    truth = json.loads((tmp_path / "out" / "ground_truth.json").read_text(encoding="utf-8"))
    assert febrl4.UPSTREAM_COMMIT in truth["provenance"]
    assert "not collected from real people" in truth["provenance"]


def test_fetch_accepts_cached_files_matching_the_pin(
    raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repin(monkeypatch, raw_dir)
    febrl4.fetch(raw_dir, offline=True)


def test_fetch_rejects_a_cached_file_that_does_not_match_the_pin(
    raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong-digest file is a different corpus and must never be scored."""

    _repin(monkeypatch, raw_dir)
    (raw_dir / "dataset4a.csv").write_text(SAMPLE_A + "rec-4-org, extra, row,,,,,,,,\n")
    with pytest.raises(SystemExit, match="does not match the pinned digest"):
        febrl4.fetch(raw_dir, offline=True)


def test_fetch_offline_refuses_to_download(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--offline was given"):
        febrl4.fetch(tmp_path / "empty", offline=True)


def test_run_eval_scores_the_sample_and_reports_flow_through(
    raw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end smoke test of the harness on a three-record sample.

    The assertions are about provenance and wiring, not accuracy: three records
    are far too few for the metrics to mean anything, but they are enough to
    prove the corpus digests, counts, and ids reach the report.
    """

    _repin(monkeypatch, raw_dir)
    markdown, report, _gate_pass = run_eval.run(
        tmp_path / "out", gate=1.0, offline=True, raw_dir=raw_dir
    )

    assert report.n_records == 6
    assert report.n_true_pairs == 2
    assert "## Flow-through evidence" in markdown
    assert "Records ingested: 6" in markdown
    for name in ("dataset4a.csv", "dataset4b.csv"):
        assert hashlib.sha256((raw_dir / name).read_bytes()).hexdigest() in markdown


def test_run_eval_report_does_not_claim_a_kappa_failure(
    raw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repin(monkeypatch, raw_dir)
    markdown, _report, _gate = run_eval.run(
        tmp_path / "out", gate=1.0, offline=True, raw_dir=raw_dir
    )
    assert "Not applicable to this run" in markdown
    assert "Generated by `make eval-benchmark`" in markdown


def test_run_eval_aborts_on_a_stale_ground_truth_file(
    raw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truth file that does not match the corpus must stop the run, not score it.

    This is the guard against reporting confident metrics for a corpus that
    never reached the resolver.
    """

    _repin(monkeypatch, raw_dir)
    out_dir = tmp_path / "out"

    real_prepare = febrl4.prepare

    def prepare_then_corrupt(raw: Path, out: Path) -> febrl4.Prepared:
        prepared = real_prepare(raw, out)
        truth = json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))
        truth["clusters"] = truth["clusters"][:1]
        (out / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")
        return prepared

    monkeypatch.setattr(run_eval, "prepare", prepare_then_corrupt)
    with pytest.raises(SystemExit, match="ground-truth mismatch"):
        run_eval.run(out_dir, gate=1.0, offline=True, raw_dir=raw_dir)
