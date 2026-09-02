"""``ai-propose-corrections`` must ground every quote in the document the run read.

A source span records the intake document's bare filename, never a path.
Resolving that name against the process working directory, which is what
``assistant/source_text.py`` used to do, made the command's behavior depend
on where the operator happened to stand when they ran it:

* from any directory but the intake one, every field reported "no source
  text", the loop skipped all of them, and the command wrote a proposals
  file holding an empty list and exited 0. Nothing said the run had been
  unable to read a single document;
* from a directory holding an unrelated file of the same name, the quote
  was verified against that file instead. The verification step still
  passed, because the quote really did appear in the text the model was
  shown. It was the wrong text, and getting there meant sending an
  unrelated local document to the model.

Every test here drives the real ``main(["ai-propose-corrections", ...])``.
Only the provider is a double, so these tests run unchanged against the
code before and after the fix, and the first three fail against the code
before it. ``test_running_from_inside_the_intake_directory_still_proposes``
is the control: it passes in both states, so a failure of the other three
cannot be blamed on the fixture.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

import constituent_reconciler.assistant as assistant
from constituent_reconciler import pipeline
from constituent_reconciler.assistant.provider import ProviderResult
from constituent_reconciler.cli import main
from constituent_reconciler.config import load_recipe

#: The surname in the document the run actually reads, and the surname in
#: the same-named decoy. A quote carrying the second one came from the
#: wrong document.
REAL_SURNAME = "Garciaintake"
DECOY_SURNAME = "Okonkwodecoy"


class _QuotesTheSurnameLine:
    """A provider that proposes one correction quoting the source's surname line.

    The quote is copied out of whatever source text the command supplied,
    so it always verifies. That is deliberate: it makes the identity of the
    document the command chose visible in the output, which is the thing
    under test. Everything else in the command stays real code, including
    the policy gate, the consent filter, and the quote check.
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
            if line.lower().startswith("last name:"):
                quote = line
                break
        if payload["field"] == "last_name" and quote:
            body: dict[str, Any] = {
                "abstain": False,
                "proposed_value": "Corrected",
                "quote": quote,
            }
        else:
            body = {"abstain": True, "reason": "nothing in the source supports a correction"}
        return ProviderResult(
            text=json.dumps(body),
            model=self.model,
            provider=self.name,
            stop_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )


def _intake_document(surname: str) -> str:
    return (
        "COUNTY INTAKE FORM\n"
        "First Name: Maria\n"
        f"Last Name: {surname}\n"
        "Email: maria@example.org\n"
        "Phone: 530-555-0101\n"
    )


def _recipe_text(*, existing: str, incoming: str) -> str:
    return textwrap.dedent(f"""\
        [input]
        existing = "{existing}"
        incoming = "{incoming}"
        id_column = "id"

        [mapping]
        first_name = "First Name"
        last_name = "Last Name"
        email = "Email"
        phone = "Phone"

        [consent]
        column = "Consent"

        [extract]
        backend = "pdfplumber"
        sandbox = false
        """)


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    """A run whose incoming source is a directory holding one text intake."""

    root = tmp_path / "demo"
    (root / "incoming").mkdir(parents=True)
    (root / "roster.csv").write_text(
        "id,First Name,Last Name,Email,Phone,Consent\n"
        f"E001,Maria,{REAL_SURNAME},maria@example.org,530-555-0101,granted\n",
        encoding="utf-8",
    )
    (root / "incoming" / "intake001.txt").write_text(
        _intake_document(REAL_SURNAME), encoding="utf-8"
    )
    (root / "recipe.toml").write_text(
        _recipe_text(existing="roster.csv", incoming="incoming"), encoding="utf-8"
    )
    return root


def _document_record_id(recipe_path: Path) -> str:
    """The id of the one record that came from a document, so it has spans."""

    recipe = load_recipe(str(recipe_path))
    ids = [record.unique_id for record in pipeline.run(recipe).records.values() if record.spans]
    assert len(ids) == 1, f"fixture should produce exactly one record with spans, got {len(ids)}"
    return ids[0]


def _propose(
    *, recipe_path: Path, out_dir: Path, record_id: str, cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    def _fake_provider(**_kwargs: object) -> _QuotesTheSurnameLine:
        return _QuotesTheSurnameLine()

    monkeypatch.setattr(assistant, "make_provider", _fake_provider)
    monkeypatch.chdir(cwd)
    return main(
        [
            "ai-propose-corrections",
            "--config",
            str(recipe_path),
            "--out",
            str(out_dir),
            "--record",
            record_id,
        ]
    )


def _verified_quotes(out_dir: Path) -> list[str]:
    data = json.loads((out_dir / "ai_ocr_proposals.json").read_text(encoding="utf-8"))
    return [p["quote"] for p in data["proposals"] if p["verified"]]


def test_running_from_inside_the_intake_directory_still_proposes(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control. The one working directory the old code handled must keep working.

    This test passes both before and after the fix. It is here so that a
    failure of the three below points at the resolution change and not at
    the fixture, the provider double, or the command wiring.
    """

    out_dir = tmp_path / "out-control"
    record_id = _document_record_id(demo / "recipe.toml")
    exit_code = _propose(
        recipe_path=demo / "recipe.toml",
        out_dir=out_dir,
        record_id=record_id,
        cwd=demo / "incoming",
        monkeypatch=monkeypatch,
    )
    assert exit_code == 0
    quotes = _verified_quotes(out_dir)
    assert quotes == [f"Last Name: {REAL_SURNAME}"]


def test_running_from_another_directory_grounds_the_same_proposal(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command must read the run's documents wherever it is invoked from.

    Before the fix this wrote ``{"proposals": []}`` and exited 0 from any
    directory that did not happen to hold the intake file.
    """

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out_dir = tmp_path / "out-elsewhere"
    record_id = _document_record_id(demo / "recipe.toml")
    exit_code = _propose(
        recipe_path=demo / "recipe.toml",
        out_dir=out_dir,
        record_id=record_id,
        cwd=elsewhere,
        monkeypatch=monkeypatch,
    )
    assert exit_code == 0
    assert _verified_quotes(out_dir) == [f"Last Name: {REAL_SURNAME}"]


def test_a_same_named_file_in_the_working_directory_is_never_read(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quote must come from the document the run read, not a namesake.

    Before the fix the decoy's text was what reached the model and what the
    quote was verified against, so a correction about one person was
    supported by a sentence from another person's file.
    """

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "intake001.txt").write_text(_intake_document(DECOY_SURNAME), encoding="utf-8")
    out_dir = tmp_path / "out-decoy"
    record_id = _document_record_id(demo / "recipe.toml")
    exit_code = _propose(
        recipe_path=demo / "recipe.toml",
        out_dir=out_dir,
        record_id=record_id,
        cwd=decoy,
        monkeypatch=monkeypatch,
    )
    assert exit_code == 0
    quotes = _verified_quotes(out_dir)
    assert quotes == [f"Last Name: {REAL_SURNAME}"]
    assert all(DECOY_SURNAME not in quote for quote in quotes)


def test_a_filename_in_two_source_directories_is_refused_not_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two sources holding the same filename must stop the command, not pick one.

    The span records only the name, so nothing in it says which directory
    the document came from. Before the fix the working directory silently
    decided, and the command exited 0 with a proposal grounded in whichever
    file that happened to be.
    """

    root = tmp_path / "two-batches"
    (root / "prior").mkdir(parents=True)
    (root / "current").mkdir(parents=True)
    (root / "prior" / "roster.csv").write_text(
        "id,First Name,Last Name,Email,Phone,Consent\n"
        f"E001,Maria,{REAL_SURNAME},maria@example.org,530-555-0101,granted\n",
        encoding="utf-8",
    )
    (root / "prior" / "intake001.txt").write_text(_intake_document(DECOY_SURNAME), encoding="utf-8")
    (root / "current" / "intake001.txt").write_text(
        _intake_document(REAL_SURNAME), encoding="utf-8"
    )
    (root / "recipe.toml").write_text(
        _recipe_text(existing="prior", incoming="current"), encoding="utf-8"
    )

    recipe = load_recipe(str(root / "recipe.toml"))
    record_id = next(
        record.unique_id
        for record in pipeline.run(recipe).records.values()
        if record.spans and record.raw.get("last_name") == REAL_SURNAME
    )
    out_dir = tmp_path / "out-ambiguous"
    exit_code = _propose(
        recipe_path=root / "recipe.toml",
        out_dir=out_dir,
        record_id=record_id,
        cwd=root / "current",
        monkeypatch=monkeypatch,
    )
    assert exit_code == 2
    assert not (out_dir / "ai_ocr_proposals.json").exists()
