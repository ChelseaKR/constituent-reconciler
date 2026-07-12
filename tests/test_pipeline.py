from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from constituent_reconciler import matching, pipeline
from constituent_reconciler.config import HouseholdConfig, OutputConfig, Recipe, load_recipe
from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.evaluate import evaluate
from constituent_reconciler.models import Consent, Correction, GoldenRecord, Record
from constituent_reconciler.provenance import verify_log

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"

EXPECTED_AUTO = {
    frozenset(("existing:E003", "incoming:N002")),
    frozenset(("existing:E005", "incoming:N006")),
    frozenset(("existing:E007", "incoming:N005")),
    frozenset(("existing:E010", "incoming:N008")),
    frozenset(("existing:E012", "incoming:N010")),
    frozenset(("incoming:N003", "incoming:N011")),
}


def test_pipeline_auto_merges_and_routes_lookalikes_to_review() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    auto = {pair.key() for pair in result.auto_pairs}
    review = {pair.key() for pair in result.review_pairs}
    assert auto == EXPECTED_AUTO
    # A real duplicate with a DOB typo, and a genuine non-duplicate with the same
    # name, both land in review rather than being auto-merged.
    assert frozenset(("existing:E002", "incoming:N004")) in review
    assert frozenset(("existing:E008", "incoming:N007")) in review


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
    assert "incoming:N009" in withheld_members
    by_members = {entry.members: entry for entry in withheld}
    n009 = next(entry for members, entry in by_members.items() if "incoming:N009" in members)
    assert n009.reason == "revoked"


def test_every_field_source_names_a_member_carrying_the_value() -> None:
    # Lineage property over the whole demo run: for every golden record, every
    # non-empty field's field_sources entry names a member of that cluster whose
    # normalized value is exactly the merged value; empty fields have no entry.
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    assert any(len(g.members) > 1 for g in result.golden)  # the property is exercised on merges
    for golden in result.golden:
        for field_name in recipe.fields:
            value = golden.fields[field_name]
            if not value:
                assert field_name not in golden.field_sources
                continue
            source = golden.field_sources[field_name]
            assert source in golden.members
            assert result.records[source].normalized.get(field_name) == value


def test_default_fill_policy_is_named_and_unknown_policy_is_refused(tmp_path: Path) -> None:
    # The demo recipe does not name a policy, so the default applies.
    assert load_recipe(EXAMPLES / "recipe.toml").fill_policy == "survivor-then-lowest-id"
    base = (
        '[input]\nincoming = "incoming.csv"\n\n'
        '[mapping]\nfirst_name = "First Name"\nlast_name = "Last Name"\n\n'
    )
    named = tmp_path / "recipe-named.toml"
    named.write_text(base + '[policy]\nfill = "survivor-then-lowest-id"\n', encoding="utf-8")
    assert load_recipe(named).fill_policy == "survivor-then-lowest-id"
    # A typo (or a reserved, unimplemented policy) fails at load time,
    # fail-closed, matching the recipe loader's strictness elsewhere.
    unknown = tmp_path / "recipe-unknown.toml"
    unknown.write_text(base + '[policy]\nfill = "most-recent-wins"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="fill"):
        load_recipe(unknown)


def test_apply_approved_review_pair_merges_cluster() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    base = pipeline.run(recipe)
    applied = pipeline.run(recipe, force_auto=[frozenset(("existing:E002", "incoming:N004"))])
    assert len(applied.clusters) == len(base.clusters) - 1
    merged = [c for c in applied.clusters if set(c.members) == {"existing:E002", "incoming:N004"}]
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
    # Each entry carries the fill policy and field-level lineage as member ids
    # only: no field value may leak into the lineage map (DV minimization).
    result_by_id = {golden.cluster_id: golden for golden in result.golden}
    entries = [
        json.loads(line)
        for line in summary.provenance_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert entries
    for entry in entries:
        assert entry["fill_policy"] == "survivor-then-lowest-id"
        golden = result_by_id[entry["record_id"]]
        assert entry["field_sources"] == golden.field_sources
        assert all(source in entry["members"] for source in entry["field_sources"].values())


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


def test_recipe_provenance_section_sets_tsa_url_and_flag_overrides(tmp_path: Path) -> None:
    recipe_path = tmp_path / "recipe.toml"
    recipe_path.write_text(
        '[input]\nincoming = "in.csv"\n\n'
        '[mapping]\nfirst_name = "first"\nlast_name = "last"\n\n'
        '[provenance]\ntsa_url = "https://tsa.example/tsr"\n',
        encoding="utf-8",
    )
    assert load_recipe(recipe_path).tsa_url == "https://tsa.example/tsr"
    # The --tsa-url flag overrides the recipe, same pattern as --policy-pack.
    overridden = load_recipe(recipe_path, tsa_url="https://other.example/tsr")
    assert overridden.tsa_url == "https://other.example/tsr"
    # A recipe with no [provenance] section keeps the local-clock default.
    assert load_recipe(EXAMPLES / "recipe.toml").tsa_url == ""


class _RecordedAuthority:
    name = "rfc3161:test"

    def __init__(self) -> None:
        self.stamped: list[str] = []

    def stamp(self, digest: str) -> str:
        self.stamped.append(digest)
        return "2026-01-01T00:00:00+00:00"


def test_export_constructs_rfc3161_authority_from_tsa_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[str] = []
    stamper = _RecordedAuthority()

    def _fake_authority(url: str) -> _RecordedAuthority:
        built.append(url)
        return stamper

    monkeypatch.setattr(pipeline, "Rfc3161Authority", _fake_authority)
    recipe = replace(load_recipe(EXAMPLES / "recipe.toml"), tsa_url="https://tsa.example/tsr")
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    assert built == ["https://tsa.example/tsr"]
    assert len(stamper.stamped) == summary.logged == 21
    assert summary.provenance_path is not None
    first = json.loads(summary.provenance_path.read_text(encoding="utf-8").splitlines()[0])
    assert first["authority"] == "rfc3161:test"
    ok, _ = verify_log(summary.provenance_path)
    assert ok


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


class _WebhookTransport:
    """Fake webhook transport: every POST succeeds with an empty 200."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        self.calls.append((url, headers, body))
        return 200, b""


def test_export_via_webhook_creates_and_logs_provenance(tmp_path: Path) -> None:
    recipe = replace(
        load_recipe(EXAMPLES / "recipe.toml"),
        output=OutputConfig(connector="webhook", endpoint="https://example.org/hooks/reconciler"),
    )
    result = pipeline.run(recipe)
    transport = _WebhookTransport()
    summary = pipeline.export(result, recipe, out_dir=tmp_path, webhook_transport=transport)

    # Default policy exports all 21 resolved records; the webhook connector
    # reports each as a plain "written" (no upsert-lookup response to read
    # created/updated from).
    assert summary.counts().get("written") == 21
    assert len(transport.calls) == 21
    assert summary.provenance_path is not None
    ok, _ = verify_log(summary.provenance_path)
    assert ok


def _write_scope_fixture(tmp_path: Path) -> Path:
    """Two records: consent scoped to civicrm only, and consent scoped to webhook."""

    (tmp_path / "incoming.csv").write_text(
        "id,first,last,dob,consent,scope\n"
        "X1,Alice,CivicrmOnly,1980-01-01,granted,civicrm\n"
        'X2,Bob,WebhookToo,1981-02-02,granted,"civicrm,webhook"\n',
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
        'scope = "scope"\n'
        "require = true\n"
        "\n"
        "[output]\n"
        'connector = "webhook"\n'
        'endpoint = "https://example.org/hooks/reconciler"\n',
        encoding="utf-8",
    )
    return recipe_path


def test_webhook_export_honors_consent_scope_not_just_status(tmp_path: Path) -> None:
    """A record whose consent is scoped away from 'webhook' is withheld from it.

    This is the same consent lifecycle gate every connector shares
    (``consent.partition_by_consent``, given ``destination=recipe.output.
    connector``); the webhook connector adds nothing of its own here, which is
    the point -- a record explicitly consented to civicrm only must not leak
    out a newly added network destination just because its status is
    "granted".
    """

    recipe = load_recipe(_write_scope_fixture(tmp_path))
    result = pipeline.run(recipe)
    transport = _WebhookTransport()
    summary = pipeline.export(result, recipe, out_dir=tmp_path / "out", webhook_transport=transport)

    assert summary.withheld_path is not None
    withheld_text = summary.withheld_path.read_text(encoding="utf-8")
    assert "X1" in withheld_text
    assert "out-of-scope" in withheld_text
    assert "X2" not in withheld_text

    sent_external_ids = {json.loads(body)["external_id"] for _, _, body in transport.calls}
    assert "incoming:X1" not in sent_external_ids
    assert "incoming:X2" in sent_external_ids


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


# -- record identity ----------------------------------------------------------

_ID_MAPPING = {"first_name": "First Name", "last_name": "Last Name"}


def _read_for_ids(path: Path, *, id_column: str | None = None) -> list[Record]:
    return pipeline.read_records(
        path,
        "incoming",
        mapping=_ID_MAPPING,
        id_column=id_column,
        consent_column=None,
        id_prefix="N",
    )


def test_inserting_a_row_leaves_other_generated_ids_unchanged(tmp_path: Path) -> None:
    # The failure this guards against: a row inserted into a CSV between
    # `reconcile review` and `reconcile apply` must not re-bind recorded
    # verdicts to different people. Ids derive from content, not position.
    before = tmp_path / "before.csv"
    before.write_text("First Name,Last Name\nAda,Lovelace\nGrace,Hopper\n", encoding="utf-8")
    after = tmp_path / "after.csv"
    after.write_text(
        "First Name,Last Name\nAda,Lovelace\nNew,Person\nGrace,Hopper\n",
        encoding="utf-8",
    )
    ids_before = {r.raw["first_name"]: r.unique_id for r in _read_for_ids(before)}
    ids_after = {r.raw["first_name"]: r.unique_id for r in _read_for_ids(after)}
    assert ids_after["Ada"] == ids_before["Ada"]
    assert ids_after["Grace"] == ids_before["Grace"]
    assert ids_after["New"] not in ids_before.values()


def test_exact_duplicate_rows_get_distinct_deterministic_ids(tmp_path: Path) -> None:
    path = tmp_path / "dupes.csv"
    path.write_text(
        "First Name,Last Name\nAda,Lovelace\nAda,Lovelace\nAda,Lovelace\n",
        encoding="utf-8",
    )
    first = [r.unique_id for r in _read_for_ids(path)]
    second = [r.unique_id for r in _read_for_ids(path)]
    assert len(set(first)) == 3
    assert first == second
    assert first[1] == f"{first[0]}-2"
    assert first[2] == f"{first[0]}-3"


def _identity_recipe(tmp_path: Path) -> Recipe:
    return Recipe(
        incoming=tmp_path / "incoming.csv",
        existing=tmp_path / "existing.csv",
        mapping=_ID_MAPPING,
        id_column="id",
        fields=("first_name", "last_name"),
    )


def test_duplicate_user_supplied_ids_within_one_source_raise(tmp_path: Path) -> None:
    (tmp_path / "existing.csv").write_text(
        "id,First Name,Last Name\nX001,Ada,Lovelace\nX001,Grace,Hopper\n",
        encoding="utf-8",
    )
    (tmp_path / "incoming.csv").write_text(
        "id,First Name,Last Name\nY001,Jean,Bartik\n", encoding="utf-8"
    )
    with pytest.raises(pipeline.DuplicateIdError, match="existing:X001"):
        pipeline.run(_identity_recipe(tmp_path))


def test_identical_ids_across_sources_do_not_collide(tmp_path: Path) -> None:
    # The same id column value in both files used to overwrite one record with
    # the other in the run's record map. Namespacing keeps both.
    (tmp_path / "existing.csv").write_text(
        "id,First Name,Last Name\nX001,Ada,Lovelace\n", encoding="utf-8"
    )
    (tmp_path / "incoming.csv").write_text(
        "id,First Name,Last Name\nX001,Grace,Hopper\n", encoding="utf-8"
    )
    result = pipeline.run(_identity_recipe(tmp_path))
    assert set(result.records) == {"existing:X001", "incoming:X001"}


def test_correction_is_applied_before_normalization_and_preserves_consent() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    correction = Correction(
        record_id="incoming:N004",
        field="dob",
        value="1972-03-08",
        reviewer="casey",
        corrected_at="2026-07-12T00:00:00+00:00",
    )
    result = pipeline.run(
        recipe,
        force_auto=[frozenset(("existing:E002", "incoming:N004"))],
        corrections=[correction],
    )
    corrected = result.records["incoming:N004"]
    assert corrected.raw["dob"] == "1972-03-08"
    assert corrected.normalized["dob"] == "1972-03-08"
    assert corrected.consent.status == "granted"
    golden = next(record for record in result.golden if "incoming:N004" in record.members)
    assert golden.fields["dob"] == "1972-03-08"


def test_correction_fails_closed_on_unknown_record_or_unmapped_field() -> None:
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    with pytest.raises(ValueError, match="not present"):
        pipeline.run(recipe, corrections=[Correction("missing", "dob", "1972-03-08")])
    with pytest.raises(ValueError, match="does not map"):
        pipeline.run(recipe, corrections=[Correction("incoming:N004", "not_a_field", "x")])


def test_force_drop_is_binding_across_transitive_auto_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = tmp_path / "incoming.csv"
    incoming.write_text(
        "id,First Name,Last Name\nA,Ada,One\nB,Ada,Two\nC,Ada,Three\n",
        encoding="utf-8",
    )
    recipe = Recipe(
        incoming=incoming,
        mapping={"first_name": "First Name", "last_name": "Last Name"},
        id_column="id",
        fields=("first_name", "last_name"),
        auto_threshold=0.97,
        review_threshold=0.80,
    )
    monkeypatch.setattr(
        matching,
        "score_pairs",
        lambda records, fields, prior: [
            ("incoming:A", "incoming:B", 0.99),
            ("incoming:B", "incoming:C", 0.99),
            ("incoming:A", "incoming:C", 0.20),
        ],
    )
    rejected = frozenset(("incoming:A", "incoming:C"))
    result = pipeline.run(recipe, force_drop=[rejected])
    assert all(not rejected <= set(golden.members) for golden in result.golden)
    assert {pair.key() for pair in result.review_pairs} == {
        frozenset(("incoming:A", "incoming:B")),
        frozenset(("incoming:B", "incoming:C")),
    }
    assert all("reviewer separated" in pair.note for pair in result.review_pairs)
