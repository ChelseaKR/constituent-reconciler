from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from constituent_reconciler import pipeline
from constituent_reconciler.config import OutputConfig, load_recipe
from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.evaluate import evaluate
from constituent_reconciler.provenance import verify_log

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"

EXPECTED_AUTO = {
    frozenset(("E003", "N002")),
    frozenset(("E005", "N006")),
    frozenset(("E007", "N005")),
    frozenset(("E010", "N008")),
    frozenset(("E012", "N010")),
    frozenset(("N003", "N011")),
}


def test_pipeline_auto_merges_and_routes_lookalikes_to_review() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    auto = {pair.key() for pair in result.auto_pairs}
    review = {pair.key() for pair in result.review_pairs}
    assert auto == EXPECTED_AUTO
    # A real duplicate with a DOB typo, and a genuine non-duplicate with the same
    # name, both land in review rather than being auto-merged.
    assert frozenset(("E002", "N004")) in review
    assert frozenset(("E008", "N007")) in review


def test_pipeline_eval_gate_is_clean_on_fixtures() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    truth = json.loads((EXAMPLES / "ground_truth.json").read_text(encoding="utf-8"))["clusters"]
    report = evaluate(result.pairs, truth, n_records=len(result.records))
    assert report.n_records == 27
    assert report.n_true_pairs == 7
    assert report.n_auto == 6
    assert report.false_merges == 0
    assert report.false_merge_rate == 0.0
    assert report.missed == 0
    assert report.recall_coverage == 1.0
    assert report.blocking_misses == 0


def test_dv_policy_pack_withholds_revoked_record() -> None:
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    assert recipe.require_consent is True
    result = pipeline.run(recipe)
    _, withheld = partition_by_consent(result.golden, require_consent=recipe.require_consent)
    withheld_members = {member for record in withheld for member in record.members}
    assert "N009" in withheld_members


def test_apply_approved_review_pair_merges_cluster() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    base = pipeline.run(recipe)
    applied = pipeline.run(recipe, force_auto=[frozenset(("E002", "N004"))])
    assert len(applied.clusters) == len(base.clusters) - 1
    merged = [c for c in applied.clusters if set(c.members) == {"E002", "N004"}]
    assert merged


def test_export_writes_csv_review_queue_and_provenance(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    resolved = tmp_path / "resolved.csv"
    assert resolved.exists()
    assert summary.review_path.exists()
    # 27 records minus 6 merges leaves 21 resolved rows plus the header.
    assert len(resolved.read_text(encoding="utf-8").strip().splitlines()) == 22
    # Every exported record produced one provenance entry, and the chain verifies.
    assert summary.logged == 21
    assert summary.provenance_path is not None
    ok, _ = verify_log(summary.provenance_path)
    assert ok


class _RoutingTransport:
    """Fake CiviCRM transport: contacts never pre-exist, so every write creates."""

    def __init__(self) -> None:
        self.created = 0

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        if url.endswith("/get"):
            return 200, json.dumps({"values": []}).encode("utf-8")
        if url.endswith("/create"):
            self.created += 1
            return 200, json.dumps({"values": [{"id": self.created}]}).encode("utf-8")
        return 200, json.dumps({"values": [{"id": 0}]}).encode("utf-8")


class _SalesforceTransport:
    """Fake Salesforce transport: every upsert returns a 201 created."""

    def __init__(self) -> None:
        self.created = 0

    def send(
        self, method: str, url: str, *, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.created += 1
        payload = {"id": f"003{self.created:03d}", "success": True, "created": True}
        return 201, json.dumps(payload).encode("utf-8")


def test_export_via_salesforce_creates_and_logs_provenance(tmp_path: Path) -> None:
    import os

    os.environ["SF_TOKEN"] = "test-token"
    try:
        recipe = replace(
            load_recipe(EXAMPLES / "recipe.toml"),
            output=OutputConfig(
                connector="salesforce",
                endpoint="https://x.my.salesforce.com",
                auth_env="SF_TOKEN",
            ),
        )
        result = pipeline.run(recipe)
        summary = pipeline.export(
            result, recipe, out_dir=tmp_path, sf_transport=_SalesforceTransport()
        )
        assert summary.counts().get("created") == 21
        assert summary.provenance_path is not None
        ok, _ = verify_log(summary.provenance_path)
        assert ok
    finally:
        del os.environ["SF_TOKEN"]


def test_export_via_civicrm_creates_and_logs_provenance(tmp_path: Path) -> None:
    import os

    os.environ["CIVICRM_API_KEY"] = "test-key"
    try:
        recipe = replace(
            load_recipe(EXAMPLES / "recipe.toml"),
            output=OutputConfig(connector="civicrm", endpoint="https://x.example/api4"),
        )
        result = pipeline.run(recipe)
        summary = pipeline.export(
            result, recipe, out_dir=tmp_path, transport=_RoutingTransport()
        )
        # Default policy exports all 21 resolved records; none pre-exist, so all create.
        assert summary.counts().get("created") == 21
        assert summary.provenance_path is not None
        ok, _ = verify_log(summary.provenance_path)
        assert ok
    finally:
        del os.environ["CIVICRM_API_KEY"]
