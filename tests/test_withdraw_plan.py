"""Consent that lapses after the write has to become an artifact, not a gap.

ADR 0013 makes a merged identity take its most restrictive member's consent at
write time. Nothing re-evaluated it afterwards, so a record written to a
destination kept sitting there once its ``expires`` date passed or a later
intake file revoked it, and no artifact in this repository said which records
those were. The system's own consent rule stopped being enforced the moment a
run ended.

``constituent-reconcile plan-withdraw`` is that artifact. These tests are written
against the two failure shapes it could have instead of the feature:

1. **An empty plan that means nothing.** An unreadable manifest, a log that
   records no run, a write entry with no external id, or a member the current
   batch cannot account for would each produce a plan with fewer lapsed records
   than the truth, and print as "0 lapsed". Every one of them refuses by name.
2. **An empty operation list read as "nothing to do".** No connector declares
   ``consent-withdraw``, so every plan is manual. The plan says that, with the
   operation named and the reason it is undeclared, rather than shipping ``[]``.
"""

from __future__ import annotations

import json
import shutil
import textwrap
from datetime import date, timedelta
from pathlib import Path

import pytest

from constituent_reconciler import repair
from constituent_reconciler.cli import main
from constituent_reconciler.config import load_recipe
from constituent_reconciler.destruction import PII_ARTIFACTS
from constituent_reconciler.provenance import WITHDRAW_PLAN_ACTION, verify_log
from constituent_reconciler.repair import WITHDRAW_PLAN_FILENAME

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
DEMO_FILES = ("recipe.toml", "existing.csv", "incoming.csv")

#: The demo row whose consent this suite expires. It is written as its own
#: single-record cluster, which matters: ``plan-split`` refuses a one-member
#: cluster outright, so a withdrawal planner that reused that rule would be
#: blind to the overwhelmingly common case, a record that matched nothing.
LAPSING_ROW = "E001"

GRANTED_ON = date(2026, 1, 1)
EXPIRES_ON = date.today() + timedelta(days=30)
AFTER_EXPIRY = EXPIRES_ON + timedelta(days=1)

_RECIPE_BODY = """\
[input]
existing = "existing.csv"
incoming = "incoming.csv"
id_column = "id"

[mapping]
first_name = "First Name"
last_name = "Last Name"
dob = "DOB"
email = "Email"
phone = "Phone"

[consent]
column = "Consent"
date = "Granted On"
expires = "Expires On"
require = {require}

[thresholds]
prior = 0.01
auto = 0.97
review = 0.80

[output]
connector = "csv"
"""


def _write_sources(demo: Path, *, expiring_ids: frozenset[str]) -> None:
    """Copy the demo batch, adding consent dates; ``expiring_ids`` get a ceiling."""

    for name in DEMO_FILES:
        shutil.copy(EXAMPLES / name, demo / name)
    for name in ("existing.csv", "incoming.csv"):
        path = demo / name
        header, *body = path.read_text(encoding="utf-8").splitlines()
        rewritten = [f"{header},Granted On,Expires On"]
        for row in body:
            row_id = row.split(",", 1)[0]
            expires = EXPIRES_ON.isoformat() if row_id in expiring_ids else ""
            rewritten.append(f"{row},{GRANTED_ON.isoformat()},{expires}")
        path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def _recipe(demo: Path, *, require: bool) -> Path:
    path = demo / f"recipe-consent-{'required' if require else 'optional'}.toml"
    path.write_text(
        textwrap.dedent(_RECIPE_BODY.format(require="true" if require else "false")),
        encoding="utf-8",
    )
    return path


def _build_run(tmp_path: Path, *, require: bool) -> tuple[Path, Path]:
    label = "required" if require else "optional"
    demo = tmp_path / f"demo-{label}"
    demo.mkdir()
    _write_sources(demo, expiring_ids=frozenset({LAPSING_ROW}))
    recipe = _recipe(demo, require=require)
    out_dir = tmp_path / f"out-{label}"
    assert main(["run", "--config", str(recipe), "--out", str(out_dir)]) == 0
    return recipe, out_dir


@pytest.fixture
def written_run(tmp_path: Path) -> tuple[Path, Path]:
    """A batch written under a consent-requiring recipe, with one expiry."""

    return _build_run(tmp_path, require=True)


@pytest.fixture
def unrequired_run(tmp_path: Path) -> tuple[Path, Path]:
    """The same batch under a recipe that records consent but does not require it."""

    return _build_run(tmp_path, require=False)


def _plan(out_dir: Path) -> dict[str, object]:
    data = json.loads((out_dir / WITHDRAW_PLAN_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _plan_via_cli(recipe_path: Path, out_dir: Path, *, as_of: date | str) -> int:
    return main(
        [
            "plan-withdraw",
            "--config",
            str(recipe_path),
            "--manifest",
            str(out_dir / "run_manifest.json"),
            "--as-of",
            as_of if isinstance(as_of, str) else as_of.isoformat(),
        ]
    )


def _lapsed(plan: dict[str, object]) -> list[dict[str, object]]:
    records = plan["lapsed_records"]
    assert isinstance(records, list)
    return [entry for entry in records if isinstance(entry, dict)]


def _written_cluster_ids(out_dir: Path) -> set[str]:
    ids: set[str] = set()
    for line in (out_dir / "provenance.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("action") in {"written", "created", "updated"}:
            ids.add(str(entry["record_id"]))
    return ids


# -- the finding the verb exists to produce -----------------------------------


def test_an_expiry_crossed_after_the_write_names_exactly_that_record(
    written_run: tuple[Path, Path],
) -> None:
    """The case ADR 0013 leaves unenforced, end to end through the real command."""

    recipe_path, out_dir = written_run
    assert _plan_via_cli(recipe_path, out_dir, as_of=AFTER_EXPIRY) == 0

    plan = _plan(out_dir)
    # Pinned as literals, not read from the constants, so a rename or a
    # re-spelling of either state has to be deliberate. A test that compares
    # the plan against the same constant the code writes cannot see a wrong
    # constant at all: both sides move together.
    assert plan["repair_plan_schema"] == 3
    assert plan["plan_kind"] == "withdraw"
    assert plan["applicability"] == "checked"

    lapsed = _lapsed(plan)
    assert [entry["cluster_id"] for entry in lapsed] == [f"existing:{LAPSING_ROW}"]
    assert lapsed[0]["external_id"] == f"existing:{LAPSING_ROW}"
    assert lapsed[0]["reason"] == "expired"
    assert plan["as_of"] == AFTER_EXPIRY.isoformat()
    assert plan["applicability"] == repair.APPLICABILITY_CHECKED

    # The denominator travels with the finding, so one lapsed record can be
    # read against how many were examined rather than on its own.
    assert plan["written_records"] == len(_written_cluster_ids(out_dir))
    assert plan["written_records"] > 1


def test_the_same_expiry_before_its_date_lapses_nothing(
    written_run: tuple[Path, Path],
) -> None:
    """The control for the test above: same batch, same record, earlier date."""

    recipe_path, out_dir = written_run
    assert _plan_via_cli(recipe_path, out_dir, as_of=EXPIRES_ON) == 0

    plan = _plan(out_dir)
    assert _lapsed(plan) == []
    # An empty list here is a measurement, and the plan says so.
    assert plan["applicability"] == repair.APPLICABILITY_CHECKED
    assert plan["written_records"] > 0


def test_a_revocation_arriving_in_a_later_intake_file_is_visible(
    written_run: tuple[Path, Path],
) -> None:
    """The other half of the issue's motivation, and the reason drift is allowed.

    A revocation cannot arrive without changing a source file, so a planner
    that demanded the inputs still hash to the manifest could never see one.
    The drift is recorded as evidence instead: the plan names the changed file.
    """

    recipe_path, out_dir = written_run
    existing = recipe_path.parent / "existing.csv"
    header, *body = existing.read_text(encoding="utf-8").splitlines()
    consent_column = header.split(",").index("Consent")
    rewritten = [header]
    for row in body:
        cells = row.split(",")
        if cells[0] == "E002":
            cells[consent_column] = "revoked"
        rewritten.append(",".join(cells))
    existing.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    assert _plan_via_cli(recipe_path, out_dir, as_of=date.today()) == 0
    plan = _plan(out_dir)
    reasons = {entry["cluster_id"]: entry["reason"] for entry in _lapsed(plan)}
    assert reasons.get("existing:E002") == "revoked"
    assert plan["inputs_changed"] == ["existing.csv"]


# -- absence must never render as a value -------------------------------------


def test_a_recipe_that_does_not_require_consent_says_so_instead_of_zero(
    unrequired_run: tuple[Path, Path],
) -> None:
    """ "Nothing was checked" and "nobody lapsed" must not print the same way.

    The write path applied no consent gate, so no written record can be out of
    consent against a rule the recipe never stated. Reporting "0 lapsed" would
    answer a question nobody asked, on the artifact an operator would use to
    conclude no follow-up is needed.
    """

    recipe_path, out_dir = unrequired_run
    assert _plan_via_cli(recipe_path, out_dir, as_of=AFTER_EXPIRY) == 0

    plan = _plan(out_dir)
    assert _lapsed(plan) == []
    assert plan["applicability"] == repair.APPLICABILITY_NOT_REQUIRED
    # Pinned as a literal for the same reason as above: this string is what a
    # consumer of the plan file branches on, so its spelling is the contract.
    assert plan["applicability"] == "not-applicable-consent-not-required"
    assert plan["require_consent"] is False


def test_the_cli_distinguishes_not_checked_from_none_lapsed(
    unrequired_run: tuple[Path, Path],
    written_run: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The distinction has to survive to the operator's terminal, not just the JSON."""

    unrequired_recipe, unrequired_out = unrequired_run
    assert _plan_via_cli(unrequired_recipe, unrequired_out, as_of=AFTER_EXPIRY) == 0
    not_checked = capsys.readouterr().out
    assert "not checked" in not_checked
    assert "0 record(s) now out of consent" not in not_checked

    required_recipe, required_out = written_run
    assert _plan_via_cli(required_recipe, required_out, as_of=EXPIRES_ON) == 0
    checked = capsys.readouterr().out
    assert "0 record(s) now out of consent" in checked
    assert "not checked" not in checked


def test_an_undeclared_withdrawal_operation_is_named_not_left_empty(
    written_run: tuple[Path, Path],
) -> None:
    """An empty operations list is stated as a fact with a reason.

    No ``RepairDeclaration`` enumerates ``consent-withdraw``, and declaring one
    would assert vendor semantics this repository has not verified. So the plan
    is manual for every destination, and it says which operation is missing.
    """

    recipe_path, out_dir = written_run
    assert _plan_via_cli(recipe_path, out_dir, as_of=AFTER_EXPIRY) == 0

    plan = _plan(out_dir)
    assert plan["mode"] == "manual"
    assert plan["supported_operations"] == []
    steps = " ".join(str(step) for step in plan["manual_instructions"])  # type: ignore[union-attr]
    assert "consent-withdraw" in steps
    assert "not the same as there being nothing to do" in steps
    assert f"existing:{LAPSING_ROW}" in steps
    assert _lapsed(plan)[0]["operations_status"] == "none-declared"


def test_a_member_the_batch_cannot_account_for_refuses_by_name(
    written_run: tuple[Path, Path],
) -> None:
    """A written record whose consent cannot be read is not a record that is fine.

    Dropping it would shorten the lapsed list, and a person whose consent had
    lapsed would be absent from the list of people whose consent had lapsed.
    """

    recipe_path, out_dir = written_run
    existing = recipe_path.parent / "existing.csv"
    header, *body = existing.read_text(encoding="utf-8").splitlines()
    kept = [row for row in body if not row.startswith(f"{LAPSING_ROW},")]
    existing.write_text("\n".join([header, *kept]) + "\n", encoding="utf-8")

    with pytest.raises(repair.WithdrawPlanError) as caught:
        repair.plan_withdraw(
            load_recipe(str(recipe_path)),
            manifest_path=out_dir / "run_manifest.json",
            as_of=AFTER_EXPIRY,
        )
    message = str(caught.value)
    assert f"existing:{LAPSING_ROW}" in message
    assert "not the same as consent being active" in message
    assert not (out_dir / WITHDRAW_PLAN_FILENAME).exists()


def test_an_unreadable_manifest_refuses_rather_than_planning_nothing(
    written_run: tuple[Path, Path], tmp_path: Path
) -> None:
    recipe_path, out_dir = written_run
    missing = tmp_path / "nowhere" / "run_manifest.json"
    with pytest.raises(repair.WithdrawPlanError, match="run manifest not found"):
        repair.plan_withdraw(
            load_recipe(str(recipe_path)), manifest_path=missing, as_of=AFTER_EXPIRY
        )

    broken = out_dir / "run_manifest.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(repair.WithdrawPlanError, match="not valid JSON"):
        repair.plan_withdraw(
            load_recipe(str(recipe_path)), manifest_path=broken, as_of=AFTER_EXPIRY
        )
    assert not (out_dir / WITHDRAW_PLAN_FILENAME).exists()


def test_a_write_entry_with_no_external_id_refuses_by_cluster(
    written_run: tuple[Path, Path],
) -> None:
    """The destination record cannot be named, so it cannot be planned for."""

    recipe_path, out_dir = written_run
    log = out_dir / "provenance.jsonl"
    rewritten: list[dict[str, object]] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("record_id") == f"existing:{LAPSING_ROW}" and entry.get("external_id"):
            entry["external_id"] = ""
        rewritten.append(entry)
    # Re-seal the whole chain, so the refusal under test is the missing
    # external id rather than a log that no longer verifies.
    log.write_text("\n".join(_relink(rewritten)) + "\n", encoding="utf-8")

    ok, _ = verify_log(log)
    assert ok, "the doctored log must still verify or this test proves nothing"
    with pytest.raises(repair.WithdrawPlanError, match="records no external id"):
        repair.plan_withdraw(
            load_recipe(str(recipe_path)),
            manifest_path=out_dir / "run_manifest.json",
            as_of=AFTER_EXPIRY,
        )


def test_a_broken_provenance_log_refuses(written_run: tuple[Path, Path]) -> None:
    recipe_path, out_dir = written_run
    log = out_dir / "provenance.jsonl"
    lines = log.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    entry["content_hash"] = "0" * 64
    lines[-1] = json.dumps(entry, sort_keys=True)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(repair.WithdrawPlanError, match="cannot anchor a withdrawal plan"):
        repair.plan_withdraw(
            load_recipe(str(recipe_path)),
            manifest_path=out_dir / "run_manifest.json",
            as_of=AFTER_EXPIRY,
        )


def test_a_log_from_a_different_run_refuses(written_run: tuple[Path, Path], tmp_path: Path) -> None:
    """A manifest with no matching run-start entry yields no written records.

    Zero written records prints as zero lapsed records, which is why this
    refuses instead.
    """

    recipe_path, out_dir = written_run
    other = tmp_path / "other"
    assert main(["run", "--config", str(recipe_path), "--out", str(other)]) == 0
    shutil.copy(other / "provenance.jsonl", out_dir / "provenance.jsonl")

    with pytest.raises(repair.WithdrawPlanError, match="records no run under this manifest"):
        repair.plan_withdraw(
            load_recipe(str(recipe_path)),
            manifest_path=out_dir / "run_manifest.json",
            as_of=AFTER_EXPIRY,
        )


def test_an_unparseable_as_of_is_refused_not_replaced_with_today(
    written_run: tuple[Path, Path],
) -> None:
    """Falling back to today would print a result for a date nobody asked for."""

    recipe_path, out_dir = written_run
    assert _plan_via_cli(recipe_path, out_dir, as_of="2026-13-45") == 2
    assert not (out_dir / WITHDRAW_PLAN_FILENAME).exists()


# -- the plan's place in the existing machinery -------------------------------


def test_the_plan_digest_lands_in_provenance_and_the_ids_do_not(
    written_run: tuple[Path, Path],
) -> None:
    """The log keeps the digest and a count; the names live only in the plan file.

    ``destroy`` refuses to delete the provenance log, so anything recorded
    there outlives a destruction pass. A permanent list of lapsed constituents
    is exactly what a destruction pass is for removing.
    """

    recipe_path, out_dir = written_run
    assert _plan_via_cli(recipe_path, out_dir, as_of=AFTER_EXPIRY) == 0
    plan_digest = repair.plan_withdraw(
        load_recipe(str(recipe_path)),
        manifest_path=out_dir / "run_manifest.json",
        as_of=AFTER_EXPIRY,
    ).digest

    log_text = (out_dir / "provenance.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(line) for line in log_text.splitlines() if line.strip()]
    withdraw_entries = [e for e in entries if e.get("action") == WITHDRAW_PLAN_ACTION]
    assert len(withdraw_entries) == 2
    assert withdraw_entries[-1]["content_hash"] == plan_digest
    assert withdraw_entries[-1]["lapsed"] == 1

    # The finding is *which* records lapsed, and that is what must not outlive a
    # destruction pass. The log already names every record the run wrote, which
    # is the write's own evidence; a withdraw-plan entry adds no id to it.
    for entry in withdraw_entries:
        assert f"existing:{LAPSING_ROW}" not in json.dumps(entry, sort_keys=True)
        assert entry["record_id"] == ""
        assert entry["members"] == []
        assert entry["external_id"] is None

    ok, message = verify_log(out_dir / "provenance.jsonl")
    assert ok, message


def test_apply_repair_refuses_a_withdrawal_plan_by_kind(
    written_run: tuple[Path, Path],
) -> None:
    """Nothing declares a withdrawal operation, so nothing may execute one."""

    recipe_path, out_dir = written_run
    assert _plan_via_cli(recipe_path, out_dir, as_of=AFTER_EXPIRY) == 0

    exit_code = main(
        [
            "apply-repair",
            "--config",
            str(recipe_path),
            "--manifest",
            str(out_dir / "run_manifest.json"),
            "--plan",
            str(out_dir / WITHDRAW_PLAN_FILENAME),
        ]
    )
    assert exit_code == 2


def test_the_plan_is_a_pii_artifact_that_destroy_removes(
    written_run: tuple[Path, Path],
) -> None:
    recipe_path, out_dir = written_run
    assert _plan_via_cli(recipe_path, out_dir, as_of=AFTER_EXPIRY) == 0
    assert WITHDRAW_PLAN_FILENAME in PII_ARTIFACTS

    assert main(["destroy", "--out", str(out_dir), "--older-than", "0d"]) == 0
    assert not (out_dir / WITHDRAW_PLAN_FILENAME).exists()
    assert (out_dir / "provenance.jsonl").exists()


def test_planning_twice_on_one_date_is_byte_identical(
    written_run: tuple[Path, Path],
) -> None:
    """Only ``as_of`` may make two plans of one run differ."""

    recipe_path, out_dir = written_run
    assert _plan_via_cli(recipe_path, out_dir, as_of=AFTER_EXPIRY) == 0
    first = (out_dir / WITHDRAW_PLAN_FILENAME).read_bytes()
    assert _plan_via_cli(recipe_path, out_dir, as_of=AFTER_EXPIRY) == 0
    assert (out_dir / WITHDRAW_PLAN_FILENAME).read_bytes() == first


# -- helper for the doctored-log test -----------------------------------------


def _relink(entries: list[dict[str, object]]) -> list[str]:
    """Recompute prev_hash and entry_hash down the chain after an edit.

    Without this the edited log fails ``verify_log`` first and the test would
    pass on the wrong refusal.
    """

    from constituent_reconciler.provenance import GENESIS_HASH, _entry_hash

    prev = GENESIS_HASH
    out: list[str] = []
    for entry in entries:
        entry["prev_hash"] = prev
        entry.pop("entry_hash", None)
        entry["entry_hash"] = _entry_hash(entry)
        prev = str(entry["entry_hash"])
        out.append(json.dumps(entry, sort_keys=True))
    return out
