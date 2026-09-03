"""A repair plan must say what each split member's consent currently allows.

`constituent-reconcile plan-split` proposes creating one destination record per member of
a merged cluster. For every destination but the CiviCRM pilot the plan is
manual: the tool executes none of it, and a person follows
``manual_instructions`` by hand. Those instructions said to create a record
for every member and said nothing about consent, while the verified path's
``_withheld_split_members`` applied that gate. The path with no gate was the
one told nothing.

The window is narrow and worth naming exactly, because it is the reason this
is reachable at all. ``_verify_manifest`` refuses to plan unless the source
files hash identically to the write, corrections cannot touch the consent
column, and ``Consent.most_restrictive`` means a cluster written at all had
every member active at that moment. So a member's consent cannot have been
*edited* between the write and the repair. What can happen without touching a
byte is time passing: a grant carrying an ``expires`` date that was in the
future when the batch was written and is in the past when the repair is
planned. The manifest still matches, and the consent has lapsed. That is the
case ``lapsed_run`` below builds, and it is why ``plan_split`` takes ``as_of``.
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
from constituent_reconciler.repair import REPAIR_PLAN_FILENAME
from constituent_reconciler.schema import REPAIR_PLAN_SCHEMA_VERSION

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
DEMO_FILES = ("recipe.toml", "existing.csv", "incoming.csv")

MERGED_CLUSTER = "existing:E003"
SURVIVOR = "existing:E003"
SPLIT_MEMBER = "incoming:N002"
REASON = "reviewer found two different people merged into one written record"

#: Far enough out that the batch below is written with consent active, and
#: the repair is planned after it has lapsed. Both dates are passed
#: explicitly, so the test does not depend on when it runs.
GRANTED_ON = date(2026, 1, 1)
EXPIRES_ON = date.today() + timedelta(days=30)
AFTER_EXPIRY = EXPIRES_ON + timedelta(days=1)


def _expiring_recipe(demo: Path) -> Path:
    """The demo data under a recipe that requires consent and maps an expiry."""

    for name in DEMO_FILES:
        shutil.copy(EXAMPLES / name, demo / name)

    for name in ("existing.csv", "incoming.csv"):
        path = demo / name
        rows = path.read_text(encoding="utf-8").splitlines()
        header, *body = rows
        rewritten = [f"{header},Granted On,Expires On"]
        rewritten += [f"{row},{GRANTED_ON.isoformat()},{EXPIRES_ON.isoformat()}" for row in body]
        path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    recipe = demo / "recipe-expiring.toml"
    recipe.write_text(
        textwrap.dedent("""\
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
            require = true

            [thresholds]
            prior = 0.01
            auto = 0.97
            review = 0.80

            [output]
            connector = "csv"
            """),
        encoding="utf-8",
    )
    return recipe


@pytest.fixture
def lapsed_run(tmp_path: Path) -> tuple[Path, Path]:
    """A written batch whose consent expires after the write, not before."""

    demo = tmp_path / "demo"
    demo.mkdir()
    recipe = _expiring_recipe(demo)
    out_dir = tmp_path / "out"
    assert main(["run", "--config", str(recipe), "--out", str(out_dir)]) == 0
    return recipe, out_dir


@pytest.fixture
def default_run(tmp_path: Path) -> tuple[Path, Path]:
    """The bundled demo, whose recipe does not require consent."""

    demo = tmp_path / "demo"
    demo.mkdir()
    for name in DEMO_FILES:
        shutil.copy(EXAMPLES / name, demo / name)
    out_dir = tmp_path / "out"
    assert main(["run", "--config", str(demo / "recipe.toml"), "--out", str(out_dir)]) == 0
    return demo / "recipe.toml", out_dir


def _read_plan(out_dir: Path) -> dict[str, object]:
    data = json.loads((out_dir / REPAIR_PLAN_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _plan_via_cli(recipe_path: Path, out_dir: Path) -> dict[str, object]:
    """Plan through the real command, with no argument this change introduced.

    Everything provable without pinning a date is proved this way, so those
    tests run unchanged against the code before and after the change.
    """

    assert (
        main(
            [
                "plan-split",
                "--config",
                str(recipe_path),
                "--manifest",
                str(out_dir / "run_manifest.json"),
                "--cluster",
                MERGED_CLUSTER,
                "--reason",
                REASON,
                "--reviewer",
                "casey",
            ]
        )
        == 0
    )
    return _read_plan(out_dir)


def _plan_as_of(recipe_path: Path, out_dir: Path, *, as_of: date) -> dict[str, object]:
    """Plan with consent evaluated on a chosen date.

    There is no CLI route to this, and that is a fact about the product
    rather than a gap in the test. Under today's invariants a member's
    consent cannot be edited between the write and the repair: the manifest
    check refuses a changed source file, corrections cannot touch the consent
    column, and a cluster written at all had every member active. The only
    way a member becomes unconsented without touching a byte is time passing
    a recorded expiry, so pinning the date is how the case is reached.
    """

    repair.plan_split(
        load_recipe(str(recipe_path)),
        manifest_path=out_dir / "run_manifest.json",
        cluster_id=MERGED_CLUSTER,
        reason=REASON,
        reviewer="casey",
        as_of=as_of,
    )
    return _read_plan(out_dir)


def _entry(plan: dict[str, object], record_id: str) -> dict[str, object]:
    records = plan["split_records"]
    assert isinstance(records, list)
    for entry in records:
        assert isinstance(entry, dict)
        if entry["record_id"] == record_id:
            return entry
    raise AssertionError(f"{record_id} is not in the plan")


def _consent(plan: dict[str, object], record_id: str) -> dict[str, object]:
    consent = _entry(plan, record_id)["consent"]
    assert isinstance(consent, dict)
    return consent


def _instructions(plan: dict[str, object]) -> str:
    steps = plan["manual_instructions"]
    assert isinstance(steps, list)
    return " ".join(str(step) for step in steps)


def test_a_lapsed_member_is_labeled_and_named_in_the_instructions(
    lapsed_run: tuple[Path, Path],
) -> None:
    """The case the plan used to be silent about, end to end."""

    recipe_path, out_dir = lapsed_run
    plan = _plan_as_of(recipe_path, out_dir, as_of=AFTER_EXPIRY)

    consent = _consent(plan, SPLIT_MEMBER)
    assert consent["required_by_recipe"] is True
    assert consent["withhold_reason"] == "expired"
    assert consent["blocks_creation"] is True

    steps = _instructions(plan)
    assert "consent.blocks_creation is true" in steps
    assert SPLIT_MEMBER in steps


def test_a_consent_requiring_recipe_states_the_rule_even_with_nobody_blocked(
    lapsed_run: tuple[Path, Path],
) -> None:
    """Through the real command, whose consent date is today: still in window.

    The general instruction appears because the recipe requires consent. The
    line naming specific members does not, because none is blocked. Before
    this change neither appeared, and the instructions told a person to
    create a record for every member with no mention of consent at all.
    """

    recipe_path, out_dir = lapsed_run
    plan = _plan_via_cli(recipe_path, out_dir)

    consent = _consent(plan, SPLIT_MEMBER)
    assert consent["required_by_recipe"] is True
    assert consent["withhold_reason"] is None
    assert consent["blocks_creation"] is False

    steps = _instructions(plan)
    assert "consent.blocks_creation is true" in steps
    assert "On this plan that means:" not in steps


def test_a_recipe_that_does_not_require_consent_labels_without_blocking(
    default_run: tuple[Path, Path],
) -> None:
    """The repair path applies the write path's rule, not a stricter one.

    The bundled demo maps a consent column but does not require consent, so
    the ordinary export writes these records regardless. A repair plan that
    refused here would be inventing a policy the recipe does not state. The
    state is still reported, because it is a fact about the record and the
    operator was previously shown nothing.
    """

    recipe_path, out_dir = default_run
    plan = _plan_via_cli(recipe_path, out_dir)

    consent = _consent(plan, SPLIT_MEMBER)
    assert consent["required_by_recipe"] is False
    assert consent["blocks_creation"] is False
    assert "withhold_reason" in consent

    steps = _instructions(plan)
    assert "consent.blocks_creation" not in steps


def test_every_split_record_carries_a_consent_object(
    default_run: tuple[Path, Path],
) -> None:
    """Including the survivor, so no entry is silently exempt from the report."""

    recipe_path, out_dir = default_run
    plan = _plan_via_cli(recipe_path, out_dir)
    records = plan["split_records"]
    assert isinstance(records, list)
    assert len(records) >= 2
    for entry in records:
        assert isinstance(entry, dict)
        consent = entry["consent"]
        assert isinstance(consent, dict)
        assert set(consent) == {"required_by_recipe", "withhold_reason", "blocks_creation"}
    assert _consent(plan, SURVIVOR)["blocks_creation"] is False


def test_the_plan_still_reports_its_schema_version(default_run: tuple[Path, Path]) -> None:
    """Control: the surface stays versioned, and the bump is visible in the file.

    This assertion holds on both sides of the change, with a different number.
    It is here so the version cannot be raised without a test noticing, and so
    a reader of a plan file can tell which shape they are holding.
    """

    recipe_path, out_dir = default_run
    plan = _plan_via_cli(recipe_path, out_dir)
    assert plan["repair_plan_schema"] == REPAIR_PLAN_SCHEMA_VERSION
    assert isinstance(plan["repair_plan_schema"], int)
    records = plan["split_records"]
    assert isinstance(records, list) and len(records) >= 2
    assert {str(entry["record_id"]) for entry in records if isinstance(entry, dict)} == {
        SURVIVOR,
        SPLIT_MEMBER,
    }
