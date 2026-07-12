"""Conformance suite every registered connector must pass.

The registry in ``connectors/__init__.py`` is the single list of write
targets a recipe can name, so this suite parametrizes over it: a new
connector added to the registry is picked up here with no test edits, and
must honor the same behavioral contract as the existing four.

The contract, drawn from ``connectors/base.py`` and the DV pack's no-egress
invariant:

* a dry run writes nothing to disk and sends nothing over the transport;
* every WriteResult action is in the known vocabulary, and ``is_write``
  agrees with it;
* an ``is_local=False`` connector never touches the filesystem during a
  write (``is_local`` is load-bearing for the DV pack's refusal);
* the external id round-trips: results are keyed by cluster id, and a local
  connector writes the cluster id into its file so a CRM-side upsert can
  key on it;
* an unregistered name fails with a clear error, fail-closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.config import OutputConfig, Recipe
from constituent_reconciler.connectors import (
    CONNECTOR_REGISTRY,
    WRITE_ACTIONS,
    Connector,
    get_factory,
)
from constituent_reconciler.models import Consent, GoldenRecord
from tests.conftest import FakeCivicrmTransport, FakeSalesforceTransport, FakeWebhookTransport

FIELDS = ("first_name", "last_name", "dob", "email", "phone")

# The full action vocabulary from connectors/base.py: real writes plus the
# non-write outcomes a connector may report.
NON_WRITE_ACTIONS = frozenset({"would-write", "skipped", "error"})
KNOWN_ACTIONS = WRITE_ACTIONS | NON_WRITE_ACTIONS

AUTH_ENV = "CONFORMANCE_AUTH_TOKEN"

RECORDS = (
    GoldenRecord(
        cluster_id="C001",
        members=("E1", "N1"),
        fields={"first_name": "jane", "last_name": "doe", "email": "jane@example.org"},
        primary="E1",
        consent=Consent(status="granted"),
    ),
    GoldenRecord(
        cluster_id="C002",
        members=("N2",),
        fields={"first_name": "amir", "last_name": "khan", "dob": "1980-02-03"},
        primary="N2",
        consent=Consent(status="granted"),
    ),
)


@pytest.fixture(autouse=True)
def _auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The network factories read their credential from the environment."""
    monkeypatch.setenv(AUTH_ENV, "test-token")


def _output_for(name: str) -> OutputConfig:
    if name == "civicrm":
        return OutputConfig(connector=name, endpoint="https://crm.example/api4", auth_env=AUTH_ENV)
    if name == "salesforce":
        return OutputConfig(
            connector=name, endpoint="https://x.my.salesforce.com", auth_env=AUTH_ENV
        )
    if name == "webhook":
        return OutputConfig(
            connector=name, endpoint="https://example.org/hooks/reconciler", auth_env=AUTH_ENV
        )
    return OutputConfig(connector=name)


def _fake_transport(name: str, *, live: bool) -> object | None:
    if name == "civicrm":
        responses: list[tuple[int, dict[str, object]]] = []
        if live:
            for i, record in enumerate(RECORDS, start=1):
                responses += [(200, {"values": []}), (200, {"values": [{"id": i}]})]
                for field_name in ("email", "phone"):
                    if record.fields.get(field_name):
                        responses += [
                            (200, {"values": []}),
                            (200, {"values": [{"id": f"{i}-{field_name}"}]}),
                        ]
        return FakeCivicrmTransport(responses)
    if name == "salesforce":
        sf_responses: list[tuple[int, dict[str, object] | None]] = (
            [
                (201, {"id": f"003{i:03d}", "success": True, "created": True})
                for i, _ in enumerate(RECORDS, start=1)
            ]
            if live
            else []
        )
        return FakeSalesforceTransport(sf_responses)
    if name == "webhook":
        return FakeWebhookTransport([(200, b"") for _ in RECORDS] if live else [])
    return None


def _build(name: str, out_dir: Path, *, live: bool) -> tuple[Connector, list[Any]]:
    """Build a connector through the registry with a fake transport.

    ``live`` queues enough fake responses for one write of RECORDS; a dry-run
    build queues none, so any transport use fails loudly. Returns the
    connector and the live list of calls the fake transport records (empty
    list for local connectors, which take no transport).
    """
    fake = _fake_transport(name, live=live)
    transports = {name: fake} if fake is not None else {}
    calls = (
        fake.calls
        if isinstance(fake, (FakeCivicrmTransport, FakeSalesforceTransport, FakeWebhookTransport))
        else []
    )
    connector = get_factory(name)(_output_for(name), out_dir, transports)
    return connector, calls


def _files_under(out_dir: Path) -> list[Path]:
    return [p for p in out_dir.rglob("*") if p.is_file()]


def test_registry_covers_the_recipe_connector_names() -> None:
    # The names a recipe's [output] section can use today. New connectors add
    # to this set; removing or renaming one is a breaking recipe change.
    assert set(CONNECTOR_REGISTRY) >= {
        "csv",
        "salesforce_csv",
        "civicrm_csv",
        "civicrm",
        "salesforce",
        "webhook",
    }


@pytest.mark.parametrize("name", sorted(CONNECTOR_REGISTRY))
def test_factory_builds_a_conforming_connector(name: str, tmp_path: Path) -> None:
    connector, _ = _build(name, tmp_path, live=False)
    assert isinstance(connector, Connector)
    assert connector.name
    assert isinstance(connector.is_local, bool)
    # Building a connector must not itself touch the out directory.
    assert _files_under(tmp_path) == []


@pytest.mark.parametrize("name", sorted(CONNECTOR_REGISTRY))
def test_dry_run_writes_nothing_anywhere(name: str, tmp_path: Path) -> None:
    connector, calls = _build(name, tmp_path, live=False)

    results = connector.write_all(RECORDS, FIELDS, dry_run=True)

    assert _files_under(tmp_path) == []
    assert calls == []
    assert [r.action for r in results] == ["would-write"] * len(RECORDS)
    assert not any(r.is_write for r in results)


@pytest.mark.parametrize("name", sorted(CONNECTOR_REGISTRY))
def test_write_actions_stay_in_the_known_vocabulary(name: str, tmp_path: Path) -> None:
    connector, _ = _build(name, tmp_path, live=True)

    results = connector.write_all(RECORDS, FIELDS, dry_run=False)

    assert len(results) == len(RECORDS)
    for result in results:
        assert result.action in KNOWN_ACTIONS
        assert result.is_write == (result.action in WRITE_ACTIONS)


@pytest.mark.parametrize("name", sorted(CONNECTOR_REGISTRY))
def test_is_local_matches_where_the_write_lands(name: str, tmp_path: Path) -> None:
    connector, calls = _build(name, tmp_path, live=True)

    connector.write_all(RECORDS, FIELDS, dry_run=False)

    files = _files_under(tmp_path)
    if connector.is_local:
        # One artifact inside out_dir and nothing over the wire. Both file
        # connectors (CsvConnector and CrmCsvConnector) report "written" per
        # record even though the artifact is a single file; that is current,
        # intentional behavior, covered by the vocabulary test above.
        assert len(files) == 1
        assert calls == []
    else:
        # is_local=False is load-bearing for the DV pack: the write goes over
        # the transport and must leave the local filesystem untouched.
        assert files == []
        assert len(calls) > 0


@pytest.mark.parametrize("name", sorted(CONNECTOR_REGISTRY))
def test_external_id_round_trips(name: str, tmp_path: Path) -> None:
    connector, _ = _build(name, tmp_path, live=True)

    results = connector.write_all(RECORDS, FIELDS, dry_run=False)

    for record, result in zip(RECORDS, results, strict=True):
        assert result.record_id == record.cluster_id
        # Every current connector reports the id an upsert would key on.
        assert result.external_id
    if connector.is_local:
        # The cluster id lands in the file, so a CRM-side import can upsert
        # on the external-id column and a re-import stays idempotent.
        (artifact,) = _files_under(tmp_path)
        content = artifact.read_text(encoding="utf-8")
        for record in RECORDS:
            assert record.cluster_id in content


def test_unknown_name_raises_and_lists_known_names() -> None:
    with pytest.raises(ValueError, match="unknown output connector: 'fax'") as excinfo:
        get_factory("fax")
    for known in sorted(CONNECTOR_REGISTRY):
        assert known in str(excinfo.value)


def test_build_connector_rejects_an_unknown_name(tmp_path: Path) -> None:
    recipe = Recipe(
        incoming=tmp_path / "incoming.csv",
        mapping={"first_name": "first", "last_name": "last"},
        output=OutputConfig(connector="fax"),
    )
    with pytest.raises(ValueError, match="unknown output connector: 'fax'"):
        pipeline.build_connector(recipe, tmp_path)
