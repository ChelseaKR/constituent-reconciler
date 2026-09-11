"""Pulling the existing side: the snapshot, what the manifest records, the refusals.

#144's four "Done when" criteria live here, each as a named test: a mocked
CiviCRM pull yields the same records as the CSV export of that same data; the
manifest carries the snapshot's digest and the API version, and a second run
against the snapshot reproduces the first; a policy pack that requires local
targets refuses the pull before a request is built (in
``tests/test_source_recipe.py``, beside the other refusals); and an HTTP error
part way through pagination leaves no snapshot on disk.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.cli import main
from constituent_reconciler.config import load_recipe
from constituent_reconciler.connectors import civicrm_source
from constituent_reconciler.connectors.base import ConnectorError
from constituent_reconciler.models import RunResult

#: Three contacts as CiviCRM API v4 returns them, and the same three as the CSV
#: export an operator produces by hand. The test that compares the two reads
#: them from here, so they cannot drift apart.
_CONTACTS: list[dict[str, object]] = [
    {
        "id": 11,
        "external_identifier": "E001",
        "first_name": "Alice",
        "last_name": "Walker",
        "birth_date": "1970-05-12",
        "email_primary.email": "alice@example.org",
        "phone_primary.phone": "555-123-4567",
    },
    {
        "id": 12,
        "external_identifier": "E002",
        "first_name": "Wei",
        "last_name": "Chen",
        "birth_date": "1968-01-22",
        "email_primary.email": "wei@example.org",
        "phone_primary.phone": "",
    },
    {
        "id": 13,
        "external_identifier": "E003",
        "first_name": "Beatriz",
        "last_name": "Rivera",
        "birth_date": "1988-03-09",
        "email_primary.email": "",
        "phone_primary.phone": "415-555-0100",
    },
]

_EXISTING_CSV = "id,first,last,dob,email,phone,consent\n" + "".join(
    f"{c['external_identifier']},{c['first_name']},{c['last_name']},{c['birth_date']},"
    f"{c['email_primary.email']},{c['phone_primary.phone']},unmapped\n"
    for c in _CONTACTS
)

_RECIPE = """[input]
incoming = "incoming.csv"
existing = "{existing}"
id_column = "id"

[mapping]
first_name = "first"
last_name  = "last"
dob        = "dob"
email      = "email"
phone      = "phone"

[consent]
column = "consent"
"""

#: Only a recipe that names a connector may carry this: a [source] section
#: without one is refused at load, which is the point of that refusal.
_SOURCE = '\n[source]\nendpoint = "https://crm.example.org/civicrm/ajax/api4"\n'


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The credential every pull needs. Its absence is refused before any
    request, which ``tests/test_connectors_civicrm_source.py`` pins."""
    monkeypatch.setenv("CIVICRM_API_KEY", "test-key")


class _Transport:
    """Answers Contact.get from a queue of pages, recording every request."""

    def __init__(self, pages: list[tuple[int, dict[str, object]]]) -> None:
        self._pages = pages
        self.calls: list[bytes] = []

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        self.calls.append(body)
        status, payload = self._pages.pop(0)
        return status, json.dumps(payload).encode("utf-8")


class _RefusingTransport:
    """A transport that fails if it is so much as constructed."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("a network transport was built where none should be")


def _one_page() -> _Transport:
    return _Transport([(200, {"values": list(_CONTACTS)})])


def _write(tmp_path: Path, *, existing: str) -> Path:
    (tmp_path / "incoming.csv").write_text(
        "first,last,dob,email,phone,consent\n"
        "Alice,Walker,1970-05-12,alice@example.org,555-123-4567,granted\n"
        "Dana,Okafor,1991-11-30,dana@example.org,555-222-0000,granted\n",
        encoding="utf-8",
    )
    body = _RECIPE.format(existing=existing)
    if existing.startswith("connector:"):
        body += _SOURCE
    path = tmp_path / "recipe.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _existing_records(result: RunResult) -> dict[str, dict[str, str]]:
    return {
        record.unique_id: dict(record.raw)
        for record in result.records.values()
        if record.source == "existing"
    }


def test_a_mocked_pull_yields_the_same_records_as_the_csv_export_of_that_data(
    tmp_path: Path,
) -> None:
    """#144's first criterion. Consent is compared separately: a pull cannot
    know it, and the CSV carries whatever the operator exported."""
    pulled_dir = tmp_path / "pulled"
    pulled_dir.mkdir()
    recipe = load_recipe(_write(pulled_dir, existing="connector:civicrm"))
    read_recipe, snapshot = pipeline.pull_existing(
        recipe, pulled_dir / "out", transport=_one_page()
    )
    pulled = _existing_records(pipeline.run(read_recipe))

    from_file = tmp_path / "from-file"
    from_file.mkdir()
    (from_file / "existing.csv").write_text(_EXISTING_CSV, encoding="utf-8")
    exported = _existing_records(
        pipeline.run(load_recipe(_write(from_file, existing="existing.csv")))
    )

    assert pulled == exported
    assert snapshot.rows == len(_CONTACTS)


def test_the_snapshot_is_written_in_the_recipes_own_column_names(tmp_path: Path) -> None:
    """A canonical header would need an edited recipe to read back, and a
    replay that needs an edited recipe is not a replay."""
    recipe = load_recipe(_write(tmp_path, existing="connector:civicrm"))
    _, snapshot = pipeline.pull_existing(recipe, tmp_path / "out", transport=_one_page())
    header = snapshot.path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "id,first,last,dob,email,phone,consent"


def test_every_pulled_record_reads_as_consent_withheld(tmp_path: Path) -> None:
    """No vendor consent mapping ships, so a pull can never imply a grant."""
    from datetime import date

    recipe = load_recipe(_write(tmp_path, existing="connector:civicrm"))
    read_recipe, _ = pipeline.pull_existing(recipe, tmp_path / "out", transport=_one_page())
    result = pipeline.run(read_recipe)
    existing = [r for r in result.records.values() if r.source == "existing"]
    assert existing
    for record in existing:
        assert record.consent.reason(as_of=date(2026, 9, 11)) is not None


def test_the_manifest_records_the_pull_and_the_snapshot_replays_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#144's second criterion, through the real command."""
    transport = _one_page()
    monkeypatch.setattr(civicrm_source, "UrllibTransport", lambda *a, **k: transport)

    recipe_path = _write(tmp_path, existing="connector:civicrm")
    out_dir = tmp_path / "out"
    assert main(["run", "--config", str(recipe_path), "--out", str(out_dir)]) == 0

    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    entry = manifest["existing_snapshot"]
    assert entry["connector"] == "civicrm"
    assert entry["api_version"] == civicrm_source.API_VERSION
    assert entry["rows"] == len(_CONTACTS)
    snapshot = out_dir / "existing_snapshot.csv"
    from constituent_reconciler.manifest import file_digest

    assert entry["digest"] == file_digest(snapshot)

    # The replay: the same recipe pointed at the snapshot, and no transport at
    # all, because nothing should reach the network on the way through.
    monkeypatch.setattr(civicrm_source, "UrllibTransport", _RefusingTransport)
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    (replay_dir / "existing.csv").write_text(snapshot.read_text(encoding="utf-8"), encoding="utf-8")
    replayed = _existing_records(
        pipeline.run(load_recipe(_write(replay_dir, existing="existing.csv")))
    )
    pulled = _existing_records(pipeline.run(load_recipe(_write(tmp_path, existing=str(snapshot)))))
    assert replayed == pulled


def test_a_run_without_a_pull_records_no_snapshot_at_all(tmp_path: Path) -> None:
    """Absent, not empty: "a pull that returned nothing" is a different fact
    from "no pull", and a key present with zeros would state the first."""
    (tmp_path / "existing.csv").write_text(_EXISTING_CSV, encoding="utf-8")
    out_dir = tmp_path / "out"
    recipe_path = _write(tmp_path, existing="existing.csv")
    assert main(["run", "--config", str(recipe_path), "--out", str(out_dir)]) == 0
    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert "existing_snapshot" not in manifest


def test_an_http_error_part_way_through_leaves_no_snapshot(tmp_path: Path) -> None:
    """#144's fourth criterion. A short snapshot is worse than none: it reads
    as a complete CRM with people missing, and each one becomes a duplicate."""
    recipe = load_recipe(_write(tmp_path, existing="connector:civicrm"))
    out_dir = tmp_path / "out"
    failing = _Transport(
        [(200, {"values": list(_CONTACTS)}), (500, {"error": "gateway went away"})]
    )
    # page_size below the first page's length, so a second page is asked for.
    recipe = replace(recipe, source=replace(recipe.source, page_size=3))

    with pytest.raises(ConnectorError, match="failed at offset 3"):
        pipeline.pull_existing(recipe, out_dir, transport=failing)

    assert not (out_dir / "existing_snapshot.csv").exists()
    assert list(out_dir.iterdir()) == [], "a partial snapshot was left behind"


def test_a_dry_run_pulls_nothing_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run is network-free, and running without the existing side would
    match every incoming record against nothing and call them all new."""
    monkeypatch.setattr(civicrm_source, "UrllibTransport", _RefusingTransport)
    recipe_path = _write(tmp_path, existing="connector:civicrm")
    out_dir = tmp_path / "out"

    assert main(["run", "--config", str(recipe_path), "--out", str(out_dir), "--dry-run"]) == 2
    assert "dry run" in capsys.readouterr().err
    assert not (out_dir / "existing_snapshot.csv").exists()
