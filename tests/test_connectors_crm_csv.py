from __future__ import annotations

import csv
from pathlib import Path

from constituent_reconciler.connectors.civicrm import IMPORT_FIELD_MAP as CIVICRM_MAP
from constituent_reconciler.connectors.crm_csv import (
    CIVICRM_IMPORT_MAP,
    SALESFORCE_IMPORT_MAP,
    CrmCsvConnector,
)
from constituent_reconciler.connectors.salesforce import FIELD_MAP as SF_MAP
from constituent_reconciler.models import GoldenRecord

FIELDS = ("first_name", "last_name", "dob", "email", "phone")


def _golden(cluster_id: str, fields: dict[str, str], consent: bool = True) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields=fields,
        primary=cluster_id,
        consent=consent,
    )


def _sf(path: Path) -> CrmCsvConnector:
    return CrmCsvConnector(
        "salesforce_csv", path, SALESFORCE_IMPORT_MAP, external_id_column="External_Id__c"
    )


def _civi(path: Path) -> CrmCsvConnector:
    return CrmCsvConnector(
        "civicrm_csv", path, CIVICRM_IMPORT_MAP, external_id_column="external_identifier"
    )


def test_import_maps_match_the_live_connector_schema() -> None:
    # The export file maps to the same schema the live push uses, so the two
    # paths cannot drift. CiviCRM's import columns differ from its API join map,
    # so the export uses the dedicated import map.
    assert SALESFORCE_IMPORT_MAP is SF_MAP
    assert CIVICRM_IMPORT_MAP is CIVICRM_MAP


def test_salesforce_csv_has_npsp_headers_and_external_id(tmp_path: Path) -> None:
    path = tmp_path / "salesforce_import.csv"
    record = _golden(
        "E1",
        {"first_name": "jane", "last_name": "doe", "dob": "1990-01-01", "email": "j@x.org"},
    )
    results = _sf(path).write_all([record], FIELDS, dry_run=False)

    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows[0]["FirstName"] == "jane"
    assert rows[0]["Birthdate"] == "1990-01-01"
    assert rows[0]["Email"] == "j@x.org"
    # The cluster id is written under the external-id column for a CRM-side upsert.
    assert rows[0]["External_Id__c"] == "E1"
    assert results[0].action == "written"
    assert results[0].is_write is True


def test_civicrm_csv_uses_plain_import_columns(tmp_path: Path) -> None:
    path = tmp_path / "civicrm_import.csv"
    record = _golden("E1", {"first_name": "jane", "last_name": "doe", "email": "j@x.org"})
    _civi(path).write_all([record], FIELDS, dry_run=False)

    header = path.read_text(encoding="utf-8").splitlines()[0]
    # Plain column names, not the API v4 join syntax (email_primary.email).
    assert "email" in header.split(",")
    assert "email_primary.email" not in header
    assert "external_identifier" in header.split(",")


def test_only_mapped_active_fields_become_columns(tmp_path: Path) -> None:
    # A run that does not map address must produce no address column.
    path = tmp_path / "salesforce_import.csv"
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    _sf(path).write_all([record], FIELDS, dry_run=False)
    header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "MailingStreet" not in header
    assert header == ["FirstName", "LastName", "Birthdate", "Email", "Phone", "External_Id__c"]


def test_address_column_appears_when_mapped(tmp_path: Path) -> None:
    path = tmp_path / "civicrm_import.csv"
    fields = ("first_name", "last_name", "address")
    record = _golden("E1", {"first_name": "jane", "last_name": "doe", "address": "1 main st"})
    _civi(path).write_all([record], fields, dry_run=False)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows[0]["street_address"] == "1 main st"


def test_dry_run_writes_no_file(tmp_path: Path) -> None:
    path = tmp_path / "salesforce_import.csv"
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    results = _sf(path).write_all([record], FIELDS, dry_run=True)
    assert not path.exists()
    assert results[0].action == "would-write"
    assert results[0].is_write is False


def test_crm_csv_targets_are_local() -> None:
    # Local files: the DV pack permits them while refusing the network push.
    assert CrmCsvConnector.is_local is True


def test_household_column_absent_unless_set(tmp_path: Path) -> None:
    # The most important default: a connector nobody calls set_household_column
    # on writes exactly the file it always wrote, no extra column.
    path = tmp_path / "civicrm_import.csv"
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    _civi(path).write_all([record], FIELDS, dry_run=False)
    header = path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "household_external_id" not in header


def test_household_column_carries_confirmed_id_only(tmp_path: Path) -> None:
    path = tmp_path / "civicrm_import.csv"
    e1 = _golden("E1", {"first_name": "jane", "last_name": "reyes"})
    e2 = _golden("E2", {"first_name": "john", "last_name": "reyes"})
    e3 = _golden("E3", {"first_name": "wei", "last_name": "chen"})
    connector = _civi(path)
    # E1 and E2 are a confirmed household; E3 was never in a confirmed group.
    connector.set_household_column({"E1": "HH-E1", "E2": "HH-E1"})
    connector.write_all([e1, e2, e3], FIELDS, dry_run=False)

    rows = {row["external_identifier"]: row for row in csv.DictReader(path.open(encoding="utf-8"))}
    assert rows["E1"]["household_external_id"] == "HH-E1"
    assert rows["E2"]["household_external_id"] == "HH-E1"
    assert rows["E3"]["household_external_id"] == ""


def test_household_column_name_is_configurable(tmp_path: Path) -> None:
    path = tmp_path / "salesforce_import.csv"
    record = _golden("E1", {"first_name": "jane", "last_name": "reyes"})
    connector = _sf(path)
    connector.set_household_column({"E1": "HH-E1"}, column="Household_External_Id__c")
    connector.write_all([record], FIELDS, dry_run=False)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows[0]["Household_External_Id__c"] == "HH-E1"
