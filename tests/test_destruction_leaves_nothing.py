"""Run the real writers, destroy, then grep for what survived.

``tests/test_destruction_inventory.py`` proves every artifact is classified.
It cannot prove a classification is right, because whether a file carries
personal data is a fact about the bytes, not about the filename. This module
proves that half the only way it can be proved: it feeds the real commands
input whose field values are unmistakable sentinel strings, lets the real
writers put those values wherever they put them, runs a real destruction pass,
and then reads every byte still under the out directory looking for a sentinel.

Nothing here consults ``PII_ARTIFACTS`` when it sweeps. A file the list forgets
is found the same way a file the list gets wrong is found, by its content. That
is exactly what the earlier tests could not do: each of them planted its
sentinel in a file whose name it read off ``PII_ARTIFACTS`` first, so an
omission was invisible to them by construction.

Three out directories are built, one per surface, because the commands need
different inputs and different destinations and merging them would mean one
scenario silently overwriting another's manifest:

* ``run``: ``constituent-reconcile run`` over an existing CSV and a text intake, through
  the csv, civicrm_csv and salesforce_csv connectors with household grouping
  and the stage cache on, then ``constituent-reconcile ai-propose-corrections``.
* ``cutover``: ``constituent-reconcile compare``, the review session ``constituent-reconcile
  compare-review`` serves, then ``constituent-reconcile compare-apply``.
* ``repair``: ``constituent-reconcile run`` against a CiviCRM double, then ``plan-split``,
  two ``approve-repair`` calls, and ``apply-repair --execute``.

Coverage is no longer a comment. ``SWEPT_BY_CONTENT`` and
``SWEPT_BY_EXISTENCE`` classify every name on ``destruction.PII_ARTIFACTS``,
and ``test_the_sweep_exercises_every_destroyable_artifact`` fails when a name
appears on neither, so a new destroyable artifact cannot be added without
either extending this fixture or writing down why it cannot be driven. That
guard is the reason the earlier limitation ("``compare``, ``compare-apply``,
``plan-split`` and ``apply-repair`` need multi-file cutover and live-CRM
fixtures") could quietly outlive its own truth: it lived in a docstring, and a
docstring cannot fail.

Only two things are doubles, and both are the things this repository forbids a
test to do for real: the model call behind ``ai-propose-corrections``, and the
CiviCRM API transport. Every gate, filter, verification and write around them
is the real code.
"""

from __future__ import annotations

import json
import shutil
import textwrap
import urllib.parse
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import constituent_reconciler.assistant as assistant
import constituent_reconciler.destruction as destruction
from constituent_reconciler import compare, compare_apply, pipeline
from constituent_reconciler.assistant.provider import ProviderResult
from constituent_reconciler.cli import main
from constituent_reconciler.destruction import PROVENANCE_FILENAME, destroy
from constituent_reconciler.provenance import ProvenanceLog, verify_log
from constituent_reconciler.review.session import APPROVED, ReviewSession
from constituent_reconciler.stage_cache import CACHE_DIR_NAME

#: Field values chosen so a substring search cannot produce a false positive
#: and normalization cannot destroy them: the email survives lowercasing, and
#: the surname and street tokens survive the case folding that name and
#: address standardization apply. The sweep matches case-insensitively.
SENTINEL_EMAIL = "planted-pii-9081726354@example.org"
SENTINEL_SURNAME = "Plantedpiisurname"
SENTINEL_STREET = "Plantedpiistreet"
#: A reviewer-supplied replacement value, which is the only kind of field value
#: that reaches ``corrections.json``: it is typed by a person in the review
#: queue, not read out of either source file.
SENTINEL_CORRECTION = "Plantedpiicorrection"
SENTINELS = (SENTINEL_EMAIL, SENTINEL_SURNAME, SENTINEL_STREET, SENTINEL_CORRECTION)

#: The one CiviCRM version ``connectors.civicrm``'s repair declaration lists as
#: verified. ``apply_repair_plan`` reads the live version and refuses anything
#: else, so the double has to answer with this exact string or the repair half
#: of the fixture never reaches a write.
VERIFIED_CIVICRM_VERSION = "6.17.2"

#: Every ``destruction.PII_ARTIFACTS`` name whose bytes this sweep proves, to
#: the command whose real writer produces it here. "Proves" is the strong
#: claim: the file must hold at least one planted sentinel before the
#: destruction pass and none of them may be readable anywhere afterwards.
SWEPT_BY_CONTENT: dict[str, str] = {
    "resolved.csv": "constituent-reconcile run, csv connector",
    "review_queue.csv": "constituent-reconcile run, on the uncertain pair the fixture plants",
    "civicrm_import.csv": "constituent-reconcile run, civicrm_csv connector",
    "salesforce_import.csv": "constituent-reconcile run, salesforce_csv connector",
    "household_suggestions.csv": "constituent-reconcile run, [household] enabled",
    "ai_ocr_proposals.json": "constituent-reconcile ai-propose-corrections",
    "cutover_report.csv": "constituent-reconcile compare",
    "cutover_review.csv": "constituent-reconcile compare",
    "corrections.json": "the review session constituent-reconcile compare-review serves",
    "target_corrections.csv": "constituent-reconcile compare-apply",
    "repair_plan.json": "constituent-reconcile plan-split",
    "repair_receipts.json": "constituent-reconcile apply-repair --execute",
}

#: The rest of ``PII_ARTIFACTS``: files this fixture's real writers do produce,
#: but whose content a sentinel cannot appear in, because they carry cluster
#: ids, member record ids and a withhold reason and no field value at all (see
#: ``pipeline._write_withheld`` and ``compare_apply._write_cutover_withheld``,
#: whose minimization is deliberate). Planting a sentinel in a record *id*
#: would reach them, and would also reach ``provenance.jsonl``, which is never
#: destroyed, so the sweep would then fail on the one artifact destruction must
#: refuse to touch. These are held to the weaker but still falsifiable claim:
#: the writer ran, the file was on disk before the pass, and the pass deleted
#: it.
SWEPT_BY_EXISTENCE: dict[str, str] = {
    "withheld.csv": "constituent-reconcile run, on the revoked-consent record the fixture plants",
    "cutover_withheld.csv": "constituent-reconcile compare-apply, on that same revoked record",
}


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


class _CivicrmDouble:
    """A CiviCRM API v4 transport double that answers by entity and where clause.

    Queue-shaped doubles pin the exact call sequence, which is what
    ``tests/test_repair_apply.py`` wants because the sequence is the thing
    under test there. Here the sequence is incidental and the *content* is
    what matters, so this answers by what was asked instead:

    * ``Domain.get`` reports the one verified version, since a repair against
      any other version is refused before it writes;
    * ``Contact.get`` by numeric id finds the survivor, and by any other
      clause finds nothing, which is what makes the run's upsert create and
      the repair's ``split-create`` create;
    * ``Email.get`` returns the merged cluster's current primary address, so
      the ``before`` half of a field-restore receipt is a real read of a real
      (wrong) value rather than a blank.

    No socket is opened; ``Transport`` is the seam the connector takes.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._next_id = 100

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        entity, action = url.rstrip("/").split("/")[-2:]
        params = json.loads(urllib.parse.parse_qs(body.decode("utf-8"))["params"][0])
        self.calls.append(f"{entity}.{action}")
        if entity == "Domain" and action == "get":
            return self._ok([{"id": 1, "version": VERIFIED_CIVICRM_VERSION}])
        if action == "get":
            if entity == "Contact":
                for clause in params.get("where") or []:
                    if clause[0] == "id":
                        return self._ok([{"id": clause[2]}])
                return self._ok([])
            if entity == "Email":
                return self._ok([{"id": 500, "email": SENTINEL_EMAIL}])
            return self._ok([])
        if action in {"create", "update"}:
            self._next_id += 1
            return self._ok([{"id": self._next_id}])
        return self._ok([])

    @staticmethod
    def _ok(values: list[dict[str, object]]) -> tuple[int, bytes]:
        return 200, json.dumps({"values": values}).encode("utf-8")


# -- the run surface -----------------------------------------------------------


def _write_run_fixture(root: Path, *, connector: str) -> Path:
    """A recipe plus the two intake files, all sentinel-laced.

    ``E003`` is a near-duplicate of ``E001``: same surname and street, a
    different given name and a date of birth one digit apart, which scores
    between the review and auto thresholds and so lands in the review queue
    rather than merging. Without it ``review_queue.csv`` is written with a
    header and no rows, which is a file the sweep cannot prove anything about.
    """

    (root / "existing.csv").write_text(
        "id,First Name,Last Name,DOB,Email,Phone,Address,Consent\n"
        f"E001,Maria,{SENTINEL_SURNAME},1985-03-14,{SENTINEL_EMAIL},530-555-0101,"
        f"742 {SENTINEL_STREET} Springfield CA 95814,granted\n"
        f"E002,Luis,{SENTINEL_SURNAME},1982-07-02,luis@example.org,530-555-0102,"
        f"742 {SENTINEL_STREET} Springfield CA 95814,granted\n"
        f"E003,Maria,{SENTINEL_SURNAME},1985-03-19,,,,granted\n",
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

            [cache]
            enabled = true

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


def _text_sourced_record_id(out_dir: Path) -> str:
    """The id the text intake resolved to, read back off the run's own output."""

    rows = (out_dir / "resolved.csv").read_text(encoding="utf-8").splitlines()
    for row in rows[1:]:
        for member in row.split(",")[2].split("|"):
            if not member.startswith("existing:"):
                return member
    raise AssertionError("the text intake produced no record id")


def _build_run_scenario(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``constituent-reconcile run`` on three connectors, then ``ai-propose-corrections``.

    ``chdir`` is required rather than cosmetic: a text intake's source span
    records the document's bare filename, so the source text
    ``ai-propose-corrections`` verifies its quote against is only findable
    when the working directory is the one holding the intake file.
    """

    monkeypatch.chdir(root)
    out_dir = root / "out"

    for connector in ("csv", "civicrm_csv", "salesforce_csv"):
        recipe = _write_run_fixture(root, connector=connector)
        assert main(["run", "--config", str(recipe), "--out", str(out_dir)]) == 0

    monkeypatch.setattr(
        assistant, "make_provider", lambda name=None, model=None: _QuotingProvider()
    )
    code = main(
        [
            "ai-propose-corrections",
            "--config",
            str(root / "recipe-csv.toml"),
            "--out",
            str(out_dir),
            "--record",
            _text_sourced_record_id(out_dir),
        ]
    )
    assert code == 0
    return out_dir


# -- the cutover surface -------------------------------------------------------


def _write_compare_side(
    root: Path, *, name: str, source: str, mapping: dict[str, str], consent_column: str
) -> Path:
    columns = "\n".join(f'{field} = "{column}"' for field, column in mapping.items())
    path = root / name
    path.write_text(
        textwrap.dedent(f"""\
            [input]
            incoming = "{source}"

            [mapping]
            {columns}

            [consent]
            column = "{consent_column}"
            require = true

            [thresholds]
            prior = 0.01
            auto = 0.97
            review = 0.80
            """),
        encoding="utf-8",
    )
    return path


def _build_cutover_scenario(root: Path) -> Path:
    """A migration comparison, reviewed and exported.

    The four planted cases each exist to reach one artifact. Maria agrees on
    both sides. Devon disagrees on date of birth with no email or address to
    corroborate either reading, so the pair is uncertain and reaches
    ``cutover_review.csv``. Alice is on the legacy side only, so she is a
    correction row. Rosa is on the legacy side only *and* has revoked consent,
    so the consent gate withholds her and ``cutover_withheld.csv`` exists.
    """

    (root / "left.csv").write_text(
        "First,Last,Birth,Email,Street,Consent\n"
        f"Maria,{SENTINEL_SURNAME},1985-03-02,{SENTINEL_EMAIL},742 {SENTINEL_STREET},granted\n"
        f"Devon,{SENTINEL_SURNAME},1990-04-12,,,granted\n"
        f"Alice,{SENTINEL_SURNAME},1979-11-30,alice.n@example.org,88 {SENTINEL_STREET},granted\n"
        f"Rosa,{SENTINEL_SURNAME},1971-02-09,rosa@example.org,4 {SENTINEL_STREET},revoked\n",
        encoding="utf-8",
    )
    (root / "right.csv").write_text(
        "given_name,family_name,dob,email_address,street,consent\n"
        f"Maria,{SENTINEL_SURNAME},1985-03-02,{SENTINEL_EMAIL},742 {SENTINEL_STREET},granted\n"
        f"Devon,{SENTINEL_SURNAME},1990-04-21,,,granted\n",
        encoding="utf-8",
    )
    left_recipe = _write_compare_side(
        root,
        name="recipe-left.toml",
        source="left.csv",
        mapping={
            "first_name": "First",
            "last_name": "Last",
            "dob": "Birth",
            "email": "Email",
            "address": "Street",
        },
        consent_column="Consent",
    )
    right_recipe = _write_compare_side(
        root,
        name="recipe-right.toml",
        source="right.csv",
        mapping={
            "first_name": "given_name",
            "last_name": "family_name",
            "dob": "dob",
            "email": "email_address",
            "address": "street",
        },
        consent_column="consent",
    )

    out_dir = root / "cutover"
    argv = ["--left", str(left_recipe), "--right", str(right_recipe), "--out", str(out_dir)]
    assert main(["compare", *argv]) == 0

    # The same ReviewSession surface `constituent-reconcile compare-review` serves, driven
    # headlessly because serving it would need a browser. Correcting a value
    # counts as this reviewer's approval of the pair, so recording a verdict
    # afterwards would abandon the correction as "approve as is".
    left_side = compare.load_side(left_recipe, label="left")
    right_side = compare.load_side(right_recipe, label="right")
    result = compare.run_compare(left_side, right_side)
    session = ReviewSession(
        compare.as_run_result(result),
        result.fields,
        out_dir / compare_apply.COMPARE_DECISIONS_FILENAME,
        reviewer="Ana",
    )
    assert session.total >= 1, "the fixture planted no uncertain pair to review"
    session.correct(0, field="email", side="left", value=f"{SENTINEL_CORRECTION}@example.org")
    for index in range(1, session.total):
        session.record(index, APPROVED)

    assert main(["compare-apply", *argv]) == 0
    return out_dir


# -- the repair surface --------------------------------------------------------


def _build_repair_scenario(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A CiviCRM write, then the full reviewed repair path over the bad merge.

    ``E001`` has no email of its own and ``N001`` does, so survivorship fills
    the merged record from the member being split away. That is what puts a
    ``field-restore`` entry in the plan, and a field-restore is the only
    operation whose receipt carries values rather than ids: its ``before`` is
    read live from the destination at apply time. ``E002`` is revoked, so the
    same run also writes ``withheld.csv``.
    """

    (root / "repair-existing.csv").write_text(
        "id,First Name,Last Name,DOB,Email,Phone,Consent\n"
        f"E001,Maria,{SENTINEL_SURNAME},1985-03-14,,530-555-0101,granted\n"
        f"E002,Rosa,{SENTINEL_SURNAME},1971-02-09,rosa@example.org,530-555-0177,revoked\n",
        encoding="utf-8",
    )
    (root / "repair-incoming.csv").write_text(
        "id,First Name,Last Name,DOB,Email,Phone,Consent\n"
        f"N001,Maria,{SENTINEL_SURNAME},1985-03-14,{SENTINEL_EMAIL},530-555-0101,granted\n",
        encoding="utf-8",
    )
    recipe = root / "recipe-repair.toml"
    recipe.write_text(
        textwrap.dedent("""\
            [input]
            existing = "repair-existing.csv"
            incoming = "repair-incoming.csv"
            id_column = "id"

            [mapping]
            first_name = "First Name"
            last_name = "Last Name"
            dob = "DOB"
            email = "Email"
            phone = "Phone"

            [consent]
            column = "Consent"
            require = true

            [thresholds]
            prior = 0.01
            auto = 0.97
            review = 0.80

            [policy]
            pack = "default"

            [output]
            connector = "civicrm"
            endpoint = "https://civicrm.example.org/civicrm/ajax/api4"
            auth_env = "CIVICRM_API_KEY"
            """),
        encoding="utf-8",
    )

    monkeypatch.setenv("CIVICRM_API_KEY", "sweep-fixture-key")
    transport = _CivicrmDouble()
    real_build_connector = pipeline.build_connector

    def _with_double(recipe_arg: Any, out_arg: Any, **kwargs: Any) -> Any:
        kwargs["transport"] = transport
        return real_build_connector(recipe_arg, out_arg, **kwargs)

    monkeypatch.setattr(pipeline, "build_connector", _with_double)

    out_dir = root / "repair"
    manifest = out_dir / "run_manifest.json"
    assert main(["run", "--config", str(recipe), "--out", str(out_dir)]) == 0

    entries = [
        json.loads(line)
        for line in (out_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cluster_id = next(e["record_id"] for e in entries if e.get("action") in {"created", "updated"})

    assert (
        main(
            [
                "plan-split",
                "--config",
                str(recipe),
                "--manifest",
                str(manifest),
                "--cluster",
                cluster_id,
                "--reason",
                "a reviewer found two different people merged into one written record",
                "--reviewer",
                "casey",
            ]
        )
        == 0
    )
    plan_path = out_dir / "repair_plan.json"
    for reviewer in ("Alice Rivera", "Bao Nguyen"):
        assert main(["approve-repair", "--plan", str(plan_path), "--reviewer", reviewer]) == 0, (
            f"{reviewer} could not record an approval"
        )
    assert (
        main(
            [
                "apply-repair",
                "--config",
                str(recipe),
                "--manifest",
                str(manifest),
                "--execute",
            ]
        )
        == 0
    )
    return out_dir


# -- fixtures ------------------------------------------------------------------


@pytest.fixture(scope="module")
def _built_out_dirs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    """Three out directories filled by the real commands, built once.

    Module-scoped because building them runs four pipelines and a comparison;
    every test takes a copy, so the destructive ones cannot see each other's
    deletions. This is the pattern ``tests/test_repair_apply.py`` uses for the
    same reason.
    """

    root = tmp_path_factory.mktemp("destruction-sweep")
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield {
            "run": _build_run_scenario(root, monkeypatch),
            "cutover": _build_cutover_scenario(root),
            "repair": _build_repair_scenario(root, monkeypatch),
        }


@pytest.fixture
def populated_out_dirs(_built_out_dirs: dict[str, Path], tmp_path: Path) -> dict[str, Path]:
    """A private copy of each built out directory, safe to destroy."""

    copies: dict[str, Path] = {}
    for name, source in _built_out_dirs.items():
        destination = tmp_path / name
        shutil.copytree(source, destination)
        copies[name] = destination
    return copies


# -- helpers -------------------------------------------------------------------


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


def _artifacts_holding_a_sentinel(out_dirs: dict[str, Path]) -> set[str]:
    """The bare filenames, across every scenario, that carry a planted value."""

    found: set[str] = set()
    for out_dir in out_dirs.values():
        for names in _surviving_sentinels(out_dir).values():
            found.update(Path(name).name for name in names)
    return found


def _existing_artifacts(out_dirs: dict[str, Path]) -> set[str]:
    """The bare filenames present, across every scenario, before any pass."""

    return {
        path.name for out_dir in out_dirs.values() for path in out_dir.rglob("*") if path.is_file()
    }


def _destroy_everything(out_dirs: dict[str, Path]) -> dict[str, destruction.DestructionSummary]:
    """One real destruction pass per scenario, at a window that spares nothing."""

    summaries: dict[str, destruction.DestructionSummary] = {}
    for name, out_dir in out_dirs.items():
        log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
        summaries[name] = destroy(out_dir, timedelta(0), policy="0d", log=log, dry_run=False)
    return summaries


def _unclassified_artifacts() -> set[str]:
    """Destroyable artifacts this sweep neither proves nor admits it skips."""

    return set(destruction.PII_ARTIFACTS) - set(SWEPT_BY_CONTENT) - set(SWEPT_BY_EXISTENCE)


def _stale_classifications() -> set[str]:
    """Names this module claims to exercise that are no longer destroyed."""

    return (set(SWEPT_BY_CONTENT) | set(SWEPT_BY_EXISTENCE)) - set(destruction.PII_ARTIFACTS)


# -- the coverage guard --------------------------------------------------------


def test_the_sweep_exercises_every_destroyable_artifact() -> None:
    """Guard the guard: a sweep that stops covering an artifact must say so.

    The previous version of this module named three files it expected to
    find and said the rest were out of reach in a docstring. Adding a
    fifteenth artifact to ``PII_ARTIFACTS`` would have left that docstring
    reading exactly as it did the day it was true, with the sweep passing.
    Here the classification is data, checked in both directions against the
    list destruction actually uses.
    """

    unclassified = _unclassified_artifacts()
    assert not unclassified, (
        "these artifacts are destroyed by `constituent-reconcile destroy` but no scenario in "
        "this module drives their writer, so nothing here proves the destruction "
        "certificate they get is honest. Extend the fixture, or classify them in "
        f"SWEPT_BY_EXISTENCE with the reason a sentinel cannot reach them: {sorted(unclassified)}"
    )
    stale = _stale_classifications()
    assert not stale, (
        "this module claims to exercise artifacts that are no longer on "
        f"destruction.PII_ARTIFACTS: {sorted(stale)}"
    )
    overlap = set(SWEPT_BY_CONTENT) & set(SWEPT_BY_EXISTENCE)
    assert not overlap, f"classified both ways: {sorted(overlap)}"


def test_the_coverage_guard_reports_an_artifact_no_scenario_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control for the guard, in the direction that matters most.

    Simulates a later command that starts writing a new PII-bearing file and
    is added to the destruction list without anyone widening this sweep.
    """

    monkeypatch.setattr(
        destruction,
        "PII_ARTIFACTS",
        (*destruction.PII_ARTIFACTS, "case_notes_export.csv"),
    )
    assert _unclassified_artifacts() == {"case_notes_export.csv"}


def test_the_coverage_guard_reports_a_classification_left_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction: a name this module still claims, now unlisted."""

    monkeypatch.setattr(
        destruction,
        "PII_ARTIFACTS",
        tuple(name for name in destruction.PII_ARTIFACTS if name != "resolved.csv"),
    )
    assert _stale_classifications() == {"resolved.csv"}


# -- the fixture's own coverage ------------------------------------------------


def test_the_fixture_really_puts_sentinels_in_the_artifacts(
    populated_out_dirs: dict[str, Path],
) -> None:
    """Guard the guard: a sweep over an out directory with no PII in it passes trivially."""

    survivors: dict[str, list[str]] = {}
    for out_dir in populated_out_dirs.values():
        for sentinel, names in _surviving_sentinels(out_dir).items():
            survivors.setdefault(sentinel, []).extend(names)
    assert set(survivors) == set(SENTINELS), (
        f"the fixture did not place every sentinel: {sorted(survivors)}"
    )

    with_content = _artifacts_holding_a_sentinel(populated_out_dirs)
    missing = set(SWEPT_BY_CONTENT) - with_content
    assert not missing, (
        "these artifacts are classified SWEPT_BY_CONTENT but no scenario wrote one "
        f"holding a planted value, so the sweep proves nothing about them: {sorted(missing)}"
    )

    present = _existing_artifacts(populated_out_dirs)
    absent = set(SWEPT_BY_EXISTENCE) - present
    assert not absent, (
        "these artifacts are classified SWEPT_BY_EXISTENCE, which still requires the "
        f"fixture to make the real writer produce them, and it did not: {sorted(absent)}"
    )


def test_the_stage_cache_entries_hold_field_values_and_are_swept(
    populated_out_dirs: dict[str, Path],
) -> None:
    """The one directory-shaped PII artifact, which no filename list can name.

    Cache entry filenames are content digests, so ``PII_ARTIFACTS`` cannot
    hold them and ``destruction._cache_entries`` walks for them by shape
    instead. That makes them exactly the kind of artifact a filename-driven
    test cannot cover, and part of the reason this sweep reads bytes.
    """

    cache_root = populated_out_dirs["run"] / CACHE_DIR_NAME
    entries = [path for path in cache_root.rglob("*.json") if path.is_file()]
    assert entries, "the run scenario did not populate a stage cache to sweep"
    laced = [
        path
        for path in entries
        if any(s.lower() in path.read_text(encoding="utf-8").lower() for s in SENTINELS)
    ]
    assert laced, "no stage-cache entry carried a planted field value"


# -- the sweep itself ----------------------------------------------------------


def test_no_sentinel_survives_a_destruction_pass(populated_out_dirs: dict[str, Path]) -> None:
    """The whole claim of ``constituent-reconcile destroy``, checked against the bytes.

    This is the assertion that failed on the pre-fix code in August 2026: it
    reported ``ai_ocr_proposals.json`` and ``household_suggestions.csv`` still
    holding a raw field value after a pass that exited 0 and issued
    certificates.
    """

    summaries = _destroy_everything(populated_out_dirs)

    for name, out_dir in populated_out_dirs.items():
        assert summaries[name].destroyed, f"the {name} pass destroyed nothing, so it proves nothing"
        survivors = _surviving_sentinels(out_dir)
        assert not survivors, (
            "`constituent-reconcile destroy` reported success and issued destruction certificates, "
            f"but these planted field values are still readable under the {name} out "
            "directory. Each file named here is written by this package and is missing "
            f"from destruction.PII_ARTIFACTS: {survivors}"
        )
        ok, message = verify_log(out_dir / PROVENANCE_FILENAME)
        assert ok, message


def test_every_exercised_artifact_is_gone_after_the_pass(
    populated_out_dirs: dict[str, Path],
) -> None:
    """The claim a content sweep cannot make about an id-only artifact.

    ``withheld.csv`` and ``cutover_withheld.csv`` hold no field values, so no
    sentinel can vouch for them. What can be checked is that the pass deleted
    them, which is the whole of what their destruction certificate asserts.
    """

    before = _existing_artifacts(populated_out_dirs)
    exercised = set(SWEPT_BY_CONTENT) | set(SWEPT_BY_EXISTENCE)
    assert exercised <= before, f"never written: {sorted(exercised - before)}"

    _destroy_everything(populated_out_dirs)

    still_here = exercised & _existing_artifacts(populated_out_dirs)
    assert not still_here, (
        "`constituent-reconcile destroy` exited without an error and left these behind: "
        f"{sorted(still_here)}"
    )


def test_the_sweep_detects_an_artifact_dropped_from_the_list(
    populated_out_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: with a name removed from the list, the sweep must fail.

    Without this, a sweep broken into always finding nothing would report
    green forever. Dropping ``resolved.csv`` reproduces, deliberately, the
    exact shape of the defect this module exists for.
    """

    monkeypatch.setattr(
        destruction,
        "PII_ARTIFACTS",
        tuple(n for n in destruction.PII_ARTIFACTS if n != "resolved.csv"),
    )
    _destroy_everything(populated_out_dirs)

    survivors = _surviving_sentinels(populated_out_dirs["run"])
    assert survivors, "the sweep found nothing even with an artifact dropped from the list"
    assert any("resolved.csv" in names for names in survivors.values())


def test_the_sweep_detects_a_repair_receipt_dropped_from_the_list(
    populated_out_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same control on the surface this widening was written to reach.

    Dropping ``resolved.csv`` proves the sweep works on the run pipeline,
    which it already did. This proves the repair half is genuinely wired in:
    the file it names can only exist because ``apply-repair --execute`` ran.
    """

    monkeypatch.setattr(
        destruction,
        "PII_ARTIFACTS",
        tuple(n for n in destruction.PII_ARTIFACTS if n != "repair_receipts.json"),
    )
    _destroy_everything(populated_out_dirs)

    survivors = _surviving_sentinels(populated_out_dirs["repair"])
    assert survivors, "the repair scenario proved nothing: no sentinel was reachable"
    assert any("repair_receipts.json" in names for names in survivors.values())
