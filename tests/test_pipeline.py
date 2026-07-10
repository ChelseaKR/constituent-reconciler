from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from constituent_reconciler import pipeline
from constituent_reconciler.config import HouseholdConfig, OutputConfig, load_recipe
from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.evaluate import evaluate
from constituent_reconciler.models import Consent, GoldenRecord
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
    withheld_members = {member for entry in withheld for member in entry.members}
    assert "N009" in withheld_members
    by_members = {entry.members: entry for entry in withheld}
    n009 = next(entry for members, entry in by_members.items() if "N009" in members)
    assert n009.reason == "revoked"


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


def test_comparable_export_off_by_default_produces_no_report(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    assert recipe.comparable_export is False
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    assert summary.comparable is None
    assert summary.comparable_path is None
    assert not (tmp_path / "comparable_report.json").exists()


def test_recipe_comparable_export_writes_comparable_report_via_run(tmp_path: Path) -> None:
    # This is the gap the README claims but the original implementation never
    # wired: a recipe with [comparable].export = true must make a plain
    # ``reconcile run`` (pipeline.export here) emit comparable_report.json,
    # not only the standalone ``export-comparable`` command.
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    export_recipe = replace(
        recipe,
        comparable_export=True,
        comparable_period="2026-Q2",
    )
    result = pipeline.run(export_recipe)
    summary = pipeline.export(result, export_recipe, out_dir=tmp_path)

    assert summary.comparable is not None
    assert summary.comparable.period == "2026-Q2"
    assert summary.comparable_path == tmp_path / "comparable_report.json"
    assert summary.comparable_path.exists()

    payload = json.loads(summary.comparable_path.read_text(encoding="utf-8"))
    assert payload["profile"] == "coc-comparable"
    assert payload["period"] == "2026-Q2"
    # No record id, member list, or raw field value in the shareable report.
    assert "N001" not in json.dumps(payload)
    assert "id" not in payload["breakdowns"]


def test_export_comparable_standalone_cli_path_still_works(tmp_path: Path) -> None:
    # The standalone ``export-comparable`` command (pipeline.export_comparable,
    # called from cli.py's _cmd_export_comparable) predates and is independent
    # of the recipe's [comparable].export flag: it always emits the report, and
    # must keep doing so unchanged by the recipe-driven wiring above.
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    assert recipe.comparable_export is False
    result = pipeline.run(recipe)
    report, report_path = pipeline.export_comparable(result, recipe, out_dir=tmp_path)

    assert report.profile == "coc-comparable"
    assert report_path == tmp_path / "comparable_report.json"
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["profile"] == "coc-comparable"
    assert "breakdowns" in payload


def _household_golden(
    cluster_id: str, *, first: str = "", last: str = "", address: str = ""
) -> GoldenRecord:
    fields: dict[str, str] = {}
    if first:
        fields["first_name"] = first
    if last:
        fields["last_name"] = last
    if address:
        fields["address"] = address
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields=fields,
        primary=cluster_id,
        consent=Consent(status="granted"),
    )


def test_household_suggestions_off_by_default(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    assert recipe.household.enabled is False
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    assert summary.household_path is None
    assert summary.household_suggestions == ()
    assert not (tmp_path / "household_suggestions.csv").exists()


def test_household_suggestions_written_when_enabled(tmp_path: Path) -> None:
    # pipeline.run() executes against the recipe's real (address-less) fields;
    # only the export-time recipe adds "address" and turns grouping on, so the
    # matcher never has to score an unmapped field. The golden records fed to
    # export are swapped in directly, the same pattern test_connectors_crm_csv
    # uses to isolate the export step from ingestion and matching.
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    export_recipe = replace(
        recipe,
        fields=(*recipe.fields, "address"),
        household=HouseholdConfig(enabled=True),
    )
    synthetic_golden = (
        _household_golden("E1", first="jane", last="reyes", address="123 N MAIN ST"),
        _household_golden("E2", first="john", last="reyes", address="123 N MAIN ST"),
        _household_golden("E3", first="wei", last="chen", address="9 OAK AVE"),
    )
    result = replace(result, golden=synthetic_golden)

    summary = pipeline.export(result, export_recipe, out_dir=tmp_path)

    assert summary.household_path is not None
    assert summary.household_path.exists()
    assert len(summary.household_suggestions) == 1
    suggestion = summary.household_suggestions[0]
    assert suggestion.members == ("E1", "E2")

    rows = list(csv.DictReader(summary.household_path.open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["household_id"] == "HH-E1"
    assert rows[0]["members"] == "E1|E2"
    assert rows[0]["confirmed"] == ""  # never auto-confirmed


def test_household_confirmation_populates_crm_export_column(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    export_recipe = replace(
        recipe,
        fields=(*recipe.fields, "address"),
        household=HouseholdConfig(enabled=True),
        output=OutputConfig(connector="civicrm_csv"),
    )
    synthetic_golden = (
        _household_golden("E1", first="jane", last="reyes", address="123 N MAIN ST"),
        _household_golden("E2", first="john", last="reyes", address="123 N MAIN ST"),
    )
    result = replace(result, golden=synthetic_golden)

    summary = pipeline.export(
        result, export_recipe, out_dir=tmp_path, confirmed_households=["HH-E1"]
    )

    rows = list(csv.DictReader((tmp_path / "civicrm_import.csv").open(encoding="utf-8")))
    by_id = {row["external_identifier"]: row["household_external_id"] for row in rows}
    assert by_id == {"E1": "HH-E1", "E2": "HH-E1"}
    # The suggestion file also shows it as confirmed, for a reviewer re-opening it.
    assert summary.household_path is not None
    suggestion_rows = list(csv.DictReader(summary.household_path.open(encoding="utf-8")))
    assert suggestion_rows[0]["confirmed"] == "yes"


def test_household_grouping_never_runs_under_dv_unless_recipe_opts_in(tmp_path: Path) -> None:
    # The invariant the ideation item calls "provably never runs unless
    # explicitly enabled": loading the demo recipe under the dv override still
    # leaves household grouping off, so export produces no suggestion file even
    # though records here would otherwise agree on address and surname.
    recipe = load_recipe(EXAMPLES / "recipe.toml", policy_pack="dv")
    assert recipe.household.enabled is False
    result = pipeline.run(recipe)
    result = replace(
        result,
        golden=(
            _household_golden("E1", first="jane", last="reyes", address="123 N MAIN ST"),
            _household_golden("E2", first="john", last="reyes", address="123 N MAIN ST"),
        ),
    )
    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    assert summary.household_path is None
    assert summary.household_suggestions == ()


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
        summary = pipeline.export(result, recipe, out_dir=tmp_path, transport=_RoutingTransport())
        # Default policy exports all 21 resolved records; none pre-exist, so all create.
        assert summary.counts().get("created") == 21
        assert summary.provenance_path is not None
        ok, _ = verify_log(summary.provenance_path)
        assert ok
    finally:
        del os.environ["CIVICRM_API_KEY"]


def _write_expiry_fixture(tmp_path: Path) -> Path:
    """Two records: one consent expired yesterday, one still good until tomorrow."""

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    (tmp_path / "incoming.csv").write_text(
        "id,first,last,dob,consent,expires\n"
        f"X1,Alice,Expired,1980-01-01,granted,{yesterday}\n"
        f"X2,Bob,Current,1981-02-02,granted,{tomorrow}\n",
        encoding="utf-8",
    )
    recipe_path = tmp_path / "recipe.toml"
    recipe_path.write_text(
        "[input]\n"
        'incoming = "incoming.csv"\n'
        'id_column = "id"\n'
        "\n"
        "[mapping]\n"
        'first_name = "first"\n'
        'last_name = "last"\n'
        'dob = "dob"\n'
        "\n"
        "[consent]\n"
        'column = "consent"\n'
        'expires = "expires"\n'
        "require = true\n",
        encoding="utf-8",
    )
    return recipe_path


def test_expired_consent_is_withheld_end_to_end(tmp_path: Path) -> None:
    """The merge-blocking invariant: an expired consent never reaches export.

    A per-record expiry date read from the recipe's mapped column is checked
    against today on every run; there is no default expiry window baked in
    anywhere, only what the recipe explicitly maps.
    """

    recipe = load_recipe(_write_expiry_fixture(tmp_path))
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path / "out")

    assert summary.withheld_path is not None
    withheld_text = summary.withheld_path.read_text(encoding="utf-8")
    assert "X1" in withheld_text
    assert "expired" in withheld_text
    assert "X2" not in withheld_text

    resolved_text = (tmp_path / "out" / "resolved.csv").read_text(encoding="utf-8")
    assert "X2" in resolved_text
    assert "X1" not in resolved_text
