"""Run the real writers, destroy, then grep for what survived.

``tests/test_destruction_inventory.py`` proves every artifact is classified.
It cannot prove a classification is right, because whether a file carries
personal data is a fact about the bytes, not about the filename. This module
proves that half the only way it can be proved: it feeds the real commands
input whose field values are unmistakable sentinel strings, lets the real
writers put those values wherever they put them, runs a real destruction pass,
and then reads every byte still under the out directory looking for a sentinel.

Nothing here consults ``PII_ARTIFACTS``. A file the list forgets is found the
same way a file the list gets wrong is found, by its content. That is exactly
what the earlier tests could not do: each of them planted its sentinel in a
file whose name it read off ``PII_ARTIFACTS`` first, so an omission was
invisible to them by construction.

Coverage is the honest limit. The sweep only sees artifacts the commands
driven below actually write, which is ``reconcile run`` (both the default CSV
connector and a CRM import file, with household grouping on) and ``reconcile
ai-propose-corrections``. ``compare``, ``compare-apply``, ``plan-split``, and
``apply-repair`` need multi-file cutover and live-CRM fixtures; their
artifacts stay covered by the per-artifact tests in test_destruction.py and by
the inventory check. Widening this sweep to them is worth doing.
"""

from __future__ import annotations

import json
import textwrap
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import constituent_reconciler.assistant as assistant
from constituent_reconciler.assistant.provider import ProviderResult
from constituent_reconciler.cli import main
from constituent_reconciler.destruction import PROVENANCE_FILENAME, destroy
from constituent_reconciler.provenance import ProvenanceLog, verify_log

#: Field values chosen so a substring search cannot produce a false positive
#: and normalization cannot destroy them: the email survives lowercasing, and
#: the surname and street tokens survive the case folding that name and
#: address standardization apply. The sweep matches case-insensitively.
SENTINEL_EMAIL = "planted-pii-9081726354@example.org"
SENTINEL_SURNAME = "Plantedpiisurname"
SENTINEL_STREET = "Plantedpiistreet"
SENTINELS = (SENTINEL_EMAIL, SENTINEL_SURNAME, SENTINEL_STREET)


class _QuotingProvider:
    """A provider that returns one verified, quote-bound correction.

    Every other part of ``ai-propose-corrections`` is the real code: the
    policy gate, the consent filter, the quote verification in
    ``ocr_propose``, and the write itself. Only the network call is stubbed,
    which is also the one thing this repository forbids a test to make.
    """

    name = "test-double"
    model = "test-double-model"

    def is_enabled(self) -> bool:
        return True

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, cache_system: bool = True
    ) -> ProviderResult:
        payload = json.loads(user)
        quote = ""
        for line in payload["source_text"].splitlines():
            if line.lower().startswith("email:"):
                quote = line
                break
        if payload["field"] == "email" and quote:
            body: dict[str, Any] = {
                "abstain": False,
                "proposed_value": "corrected@example.org",
                "quote": quote,
            }
        else:
            body = {"abstain": True, "reason": "no OCR defect apparent in this field"}
        return ProviderResult(
            text=json.dumps(body),
            model=self.model,
            provider=self.name,
            stop_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )


def _write_fixture(root: Path, *, connector: str) -> Path:
    """A recipe plus the two intake files, all sentinel-laced."""

    (root / "existing.csv").write_text(
        "id,First Name,Last Name,DOB,Email,Phone,Address,Consent\n"
        f"E001,Maria,{SENTINEL_SURNAME},1985-03-14,{SENTINEL_EMAIL},530-555-0101,"
        f"742 {SENTINEL_STREET} Springfield CA 95814,granted\n"
        f"E002,Luis,{SENTINEL_SURNAME},1982-07-02,luis@example.org,530-555-0102,"
        f"742 {SENTINEL_STREET} Springfield CA 95814,granted\n",
        encoding="utf-8",
    )
    # A plain-text intake, so the record carries real source spans and
    # ai-propose-corrections has a document to ground a quote in.
    (root / "intake.txt").write_text(
        "COUNTY INTAKE FORM\n"
        "First Name: Maria\n"
        f"Last Name: {SENTINEL_SURNAME}\n"
        "Date of Birth: 03/14/1985\n"
        f"Email: {SENTINEL_EMAIL}\n"
        "Phone: 530-555-0101\n",
        encoding="utf-8",
    )
    recipe = root / f"recipe-{connector}.toml"
    recipe.write_text(
        textwrap.dedent(f"""\
            [input]
            existing = "existing.csv"
            incoming = "intake.txt"
            id_column = "id"

            [mapping]
            first_name = "First Name"
            last_name = "Last Name"
            dob = "DOB"
            email = "Email"
            phone = "Phone"
            address = "Address"

            [consent]
            column = "Consent"

            [extract]
            backend = "pdfplumber"
            sandbox = false

            [thresholds]
            prior = 0.01
            auto = 0.97
            review = 0.80

            [policy]
            pack = "default"

            [household]
            enabled = true

            [output]
            connector = "{connector}"
            """),
        encoding="utf-8",
    )
    return recipe


def _surviving_sentinels(out_dir: Path) -> dict[str, list[str]]:
    """Every sentinel still readable under ``out_dir``, to the files holding it."""

    survivors: dict[str, list[str]] = {}
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file():
            continue
        blob = path.read_bytes().decode("utf-8", errors="replace").lower()
        for sentinel in SENTINELS:
            if sentinel.lower() in blob:
                survivors.setdefault(sentinel, []).append(path.relative_to(out_dir).as_posix())
    return survivors


@pytest.fixture
def populated_out_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An out directory filled by the real commands, not by planting files.

    ``chdir`` is required rather than cosmetic: a text intake's source span
    records the document's bare filename, so the source text
    ``ai-propose-corrections`` verifies its quote against is only findable
    when the working directory is the one holding the intake file.
    """

    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "out"

    for connector in ("csv", "civicrm_csv"):
        recipe = _write_fixture(tmp_path, connector=connector)
        assert main(["run", "--config", str(recipe), "--out", str(out_dir)]) == 0

    monkeypatch.setattr(
        assistant, "make_provider", lambda name=None, model=None: _QuotingProvider()
    )
    record_id = _text_sourced_record_id(out_dir)
    code = main(
        [
            "ai-propose-corrections",
            "--config",
            str(tmp_path / "recipe-csv.toml"),
            "--out",
            str(out_dir),
            "--record",
            record_id,
        ]
    )
    assert code == 0
    return out_dir


def _text_sourced_record_id(out_dir: Path) -> str:
    """The id the text intake resolved to, read back off the run's own output."""

    rows = (out_dir / "resolved.csv").read_text(encoding="utf-8").splitlines()
    for row in rows[1:]:
        for member in row.split(",")[2].split("|"):
            if not member.startswith("existing:"):
                return member
    raise AssertionError("the text intake produced no record id")


def test_the_fixture_really_puts_sentinels_in_the_artifacts(populated_out_dir: Path) -> None:
    """Guard the guard: a sweep over an out directory with no PII in it passes trivially."""

    before = _surviving_sentinels(populated_out_dir)
    assert set(before) == set(SENTINELS), f"the fixture did not reach every artifact: {before}"
    written = {name for names in before.values() for name in names}
    for expected in ("resolved.csv", "household_suggestions.csv", "ai_ocr_proposals.json"):
        assert expected in written, f"{expected} was not written with a sentinel in it"


def test_no_sentinel_survives_a_destruction_pass(populated_out_dir: Path) -> None:
    """The whole claim of ``reconcile destroy``, checked against the bytes.

    This is the assertion that fails on the pre-fix code: it reported
    ``ai_ocr_proposals.json`` and ``household_suggestions.csv`` still holding
    a raw field value after a pass that exited 0 and issued certificates.
    """

    log = ProvenanceLog(populated_out_dir / PROVENANCE_FILENAME)
    summary = destroy(populated_out_dir, timedelta(0), policy="0d", log=log, dry_run=False)

    assert summary.destroyed, "the pass destroyed nothing, so it proves nothing"
    survivors = _surviving_sentinels(populated_out_dir)
    assert not survivors, (
        "`reconcile destroy` reported success and issued destruction certificates, "
        "but these planted field values are still readable under the out directory. "
        "Each file named here is written by this package and is missing from "
        f"destruction.PII_ARTIFACTS: {survivors}"
    )
    ok, message = verify_log(populated_out_dir / PROVENANCE_FILENAME)
    assert ok, message


def test_the_sweep_detects_an_artifact_dropped_from_the_list(
    populated_out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: with a name removed from the list, the sweep must fail.

    Without this, a sweep broken into always finding nothing would report
    green forever. Dropping ``resolved.csv`` reproduces, deliberately, the
    exact shape of the defect this module exists for.
    """

    import constituent_reconciler.destruction as destruction

    monkeypatch.setattr(
        destruction,
        "PII_ARTIFACTS",
        tuple(n for n in destruction.PII_ARTIFACTS if n != "resolved.csv"),
    )
    log = ProvenanceLog(populated_out_dir / PROVENANCE_FILENAME)
    destroy(populated_out_dir, timedelta(0), policy="0d", log=log, dry_run=False)

    survivors = _surviving_sentinels(populated_out_dir)
    assert survivors, "the sweep found nothing even with an artifact dropped from the list"
    assert any("resolved.csv" in names for names in survivors.values())
