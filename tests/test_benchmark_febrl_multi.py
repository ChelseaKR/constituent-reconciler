"""Tests for the FEBRL datasets 1-3 benchmark harness (#68).

Nothing here reaches the network. ``fetch`` is exercised against a
pre-populated directory, and the conversion is exercised against a small
inline sample in FEBRL's exact single-file on-disk shape (comma-space
separated header, split address columns, YYYYMMDD dates, empty cells) --
unlike FEBRL4, originals and duplicates share one file here, and one
original in the sample carries *two* duplicates so the multi-duplicate
grouping this module exists to get right (see its module docstring) is
exercised directly, not just plausible.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from tools.benchmark import febrl_multi
from tools.benchmark.febrl4 import UPSTREAM_COMMIT

from constituent_reconciler.decisions import band_pairs
from constituent_reconciler.evaluate import evaluate
from constituent_reconciler.models import Band

HEADER = (
    "rec_id, given_name, surname, street_number, address_1, address_2, "
    "suburb, postcode, state, date_of_birth, soc_sec_id\n"
)

# rec-1: one duplicate. rec-2: two duplicates (the case that distinguishes
# proper clustering from a naive per-pair derivation). rec-3: no duplicate.
# rec-9-dup-0: an orphan duplicate with no original. Both of the last two
# must drop out of ground truth.
SAMPLE = (
    HEADER
    + """\
rec-1-org, michaela, neumann, 8, stanley street, miami, winston hills, 4223, nsw, 19151111, 5304218
rec-1-dup-0, michaela, neumann, 8, stanley st, miami, winston hills, 4223, nsw, 19151111, 5304218
rec-2-org, courtney, painter, 12, pinkerton circuit, , richlands, 4560, vic, 19161214, 4066625
rec-2-dup-0, courtney, painter, 12, pinkerton circuit, , richlands, 4560, vic, 19161214, 4066625
rec-2-dup-1, courtnee, painter, 12, pinkerton crct, , richlands, 4560, vic, 19161214, 4066625
rec-3-org, lonely, original, 1, only street, , nowhere, 1000, qld, 19700101, 1111111
rec-9-dup-0, orphan, duplicate, 2, stray street, , elsewhere, 2000, wa, 19800202, 2222222
"""
)


@pytest.fixture
def raw_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "raw"
    directory.mkdir()
    (directory / "dataset1.csv").write_text(SAMPLE, encoding="utf-8")
    return directory


def _spec() -> febrl_multi.DatasetSpec:
    return febrl_multi.DATASETS[1]


def _repin(monkeypatch: pytest.MonkeyPatch, raw_dir: Path) -> None:
    digest = hashlib.sha256((raw_dir / "dataset1.csv").read_bytes()).hexdigest()
    monkeypatch.setitem(
        febrl_multi.DATASETS,
        1,
        febrl_multi.DatasetSpec(1, "dataset1.csv", digest, "low"),
    )


def test_truth_clusters_groups_more_than_one_duplicate_per_original() -> None:
    """The property this module exists for: rec-2's two duplicates are one cluster.

    A naive per-pair derivation (one [org, dup] pair per duplicate, the shape
    that is correct for FEBRL4's always-one-duplicate layout) would produce
    two separate two-member clusters here instead of one three-member
    cluster, silently dropping the true (dup-0, dup-1) pair -- a real pair
    under Splink's dedupe_only matching (matching/splink_backend.py), where
    every record is compared against every other record regardless of which
    file role it plays.
    """

    ids = [
        "rec-1-org",
        "rec-1-dup-0",
        "rec-2-org",
        "rec-2-dup-0",
        "rec-2-dup-1",
        "rec-3-org",
        "rec-9-dup-0",
    ]
    clusters = febrl_multi.truth_clusters(ids)

    by_size = sorted(clusters, key=len)
    assert [sorted(c) for c in by_size] == [
        ["existing:rec-1-org", "incoming:rec-1-dup-0"],
        [
            "existing:rec-2-org",
            "incoming:rec-2-dup-0",
            "incoming:rec-2-dup-1",
        ],
    ]
    # rec-3 (no duplicate) and rec-9-dup-0 (no original) both drop out.
    assert sum(len(c) for c in clusters) == 5


def test_prepared_true_pairs_counts_every_pairwise_combination(
    raw_dir: Path, tmp_path: Path
) -> None:
    """n_true_pairs must match evaluate.truth_pairs' own combinatorial count.

    rec-1's cluster (2 members) contributes C(2,2)=1 pair; rec-2's cluster (3
    members) contributes C(3,2)=3 pairs (org-dup0, org-dup1, and dup0-dup1,
    the pair a per-pair derivation would miss). Total: 4, not 3.
    """

    spec = _spec()
    prepared = febrl_multi.prepare(spec, raw_dir, tmp_path / "out")
    assert prepared.n_true_pairs == 4
    assert prepared.n_existing == 3  # rec-1-org, rec-2-org, rec-3-org
    assert prepared.n_incoming == 4  # the two rec-1/rec-2 duplicates plus the orphan


def test_ground_truth_clusters_namespace_ids_by_source(raw_dir: Path, tmp_path: Path) -> None:
    spec = _spec()
    febrl_multi.prepare(spec, raw_dir, tmp_path / "out")
    truth = json.loads((tmp_path / "out" / "ground_truth.json").read_text(encoding="utf-8"))
    for cluster in truth["clusters"]:
        assert cluster[0].startswith("existing:")
        assert all(member.startswith("incoming:") for member in cluster[1:])


def test_ground_truth_declares_provenance(raw_dir: Path, tmp_path: Path) -> None:
    spec = _spec()
    febrl_multi.prepare(spec, raw_dir, tmp_path / "out")
    truth = json.loads((tmp_path / "out" / "ground_truth.json").read_text(encoding="utf-8"))
    assert UPSTREAM_COMMIT in truth["provenance"]
    assert "not collected from real people" in truth["provenance"]
    assert "low" in truth["provenance"]


def test_prepare_passes_dob_through_unconverted(raw_dir: Path, tmp_path: Path) -> None:
    spec = _spec()
    febrl_multi.prepare(spec, raw_dir, tmp_path / "out")
    with (tmp_path / "out" / "existing.csv").open(newline="", encoding="utf-8") as handle:
        rows = {row["id"]: row for row in csv.DictReader(handle)}
    assert rows["rec-1-org"]["dob"] == "19151111"


def test_fetch_accepts_cached_files_matching_the_pin(
    raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repin(monkeypatch, raw_dir)
    febrl_multi.fetch(febrl_multi.DATASETS[1], raw_dir, offline=True)


def test_fetch_rejects_a_cached_file_that_does_not_match_the_pin(
    raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repin(monkeypatch, raw_dir)
    (raw_dir / "dataset1.csv").write_text(SAMPLE + "rec-4-org, extra, row,,,,,,,,\n")
    with pytest.raises(SystemExit, match="does not match the pinned digest"):
        febrl_multi.fetch(febrl_multi.DATASETS[1], raw_dir, offline=True)


def test_fetch_offline_refuses_to_download(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--offline was given"):
        febrl_multi.fetch(febrl_multi.DATASETS[1], tmp_path / "empty", offline=True)


def test_run_scores_the_sample_and_reports_flow_through(
    raw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repin(monkeypatch, raw_dir)
    markdown, report, _gate_pass = febrl_multi.run(
        1, tmp_path / "out", gate=1.0, offline=True, raw_dir=raw_dir
    )
    assert report.n_records == 7
    assert report.n_true_pairs == 4
    assert "## Flow-through evidence" in markdown
    assert "### Threshold sweep" in markdown
    assert "Records ingested: 7" in markdown
    assert hashlib.sha256((raw_dir / "dataset1.csv").read_bytes()).hexdigest() in markdown


def test_run_aborts_on_a_stale_ground_truth_file(
    raw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repin(monkeypatch, raw_dir)
    out_dir = tmp_path / "out"
    real_prepare = febrl_multi.prepare

    def prepare_then_corrupt(
        spec: febrl_multi.DatasetSpec, raw: Path, out: Path
    ) -> febrl_multi.Prepared:
        prepared = real_prepare(spec, raw, out)
        truth = json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))
        truth["clusters"] = truth["clusters"][:1]
        (out / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")
        return prepared

    monkeypatch.setattr(febrl_multi, "prepare", prepare_then_corrupt)
    with pytest.raises(SystemExit, match="ground-truth mismatch"):
        febrl_multi.run(1, out_dir, gate=1.0, offline=True, raw_dir=raw_dir)


def test_threshold_sweep_reorders_bands_without_rescoring() -> None:
    """A higher auto threshold can only shrink the auto band, never grow it."""

    truth = [["a", "b"], ["c", "d"]]
    scored = [("a", "b", 0.99), ("c", "d", 0.50), ("e", "f", 0.10)]
    pairs = band_pairs(scored, auto_threshold=0.80, review_threshold=0.20)
    assert {p.band for p in pairs} == {Band.AUTO, Band.REVIEW, Band.DROP}

    sweep = febrl_multi.threshold_sweep(
        pairs, truth, n_records=6, auto_thresholds=(0.40, 0.90, 0.999), review_threshold=0.20
    )
    by_threshold = dict(sweep)
    assert by_threshold[0.40].n_auto == 2  # both a-b (0.99) and c-d (0.50) clear 0.40
    assert by_threshold[0.90].n_auto == 1  # only a-b (0.99) clears 0.90
    assert by_threshold[0.999].n_auto == 0  # neither clears 0.999

    # A direct call to evaluate() at the matching threshold must agree exactly
    # with what the sweep computed -- the sweep is not a separate code path.
    direct = evaluate(
        band_pairs(scored, auto_threshold=0.90, review_threshold=0.20), truth, n_records=6
    )
    assert by_threshold[0.90] == direct
