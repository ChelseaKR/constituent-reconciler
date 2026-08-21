"""Tests for the reviewed, gated apply path (UC-03 PR 3, ADR 0012).

The break-the-gate triad this module proves, mirroring the task's own bar:
a single (or zero) reviewer approval cannot reach the connector at all --
proven with a connector double that raises if any of its methods are
called, not just by checking an exception message; the same plan applied
twice writes nothing the second time (idempotent by construction in
``connectors.civicrm.CivicrmConnector.apply_repair``); and a plan file
edited after planning is refused because its bytes no longer match the
digest the provenance log recorded at plan time (ADR 0012's T7).

The fixture is real, not fabricated: ``base_run`` runs the actual demo
recipe's CiviCRM output (``examples/intake-demo/recipe-civicrm.toml``)
through ``pipeline.run``/``pipeline.export`` with a routing fake transport,
so ``existing:E003`` + ``incoming:N002`` (the same auto-merged "Jonathan"/
"Jonathon" pair ``tests/test_repair.py`` plans) is genuinely written before
any test plans or applies its repair.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from constituent_reconciler import pipeline, repair
from constituent_reconciler.cli import main
from constituent_reconciler.config import Recipe, load_recipe
from constituent_reconciler.connectors.base import WriteResult
from constituent_reconciler.connectors.civicrm import CivicrmConfig, CivicrmConnector
from constituent_reconciler.models import GoldenRecord
from constituent_reconciler.provenance import verify_log
from tests.conftest import FakeCivicrmTransport

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
CIVICRM_RECIPE = EXAMPLES / "recipe-civicrm.toml"

MERGED_CLUSTER = "existing:E003"
MERGED_MEMBERS = ("existing:E003", "incoming:N002")
REASON = "reviewer found two different people merged into one written record"


class _RoutingTransport:
    """Fake CiviCRM transport: contacts never pre-exist, so every write creates.

    Used only to produce the base fixture's real write, mirroring
    ``tests/test_pipeline.py``'s ``_RoutingTransport``.
    """

    def __init__(self) -> None:
        self.created = 0

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        if url.endswith("/get"):
            return 200, json.dumps({"values": []}).encode("utf-8")
        if url.endswith("/create"):
            self.created += 1
            return 200, json.dumps({"values": [{"id": self.created}]}).encode("utf-8")
        return 200, json.dumps({"values": [{"id": 0}]}).encode("utf-8")


class _PoisonConnector:
    """A connector double that fails the test if any repair method is called.

    Passed to ``apply_repair_plan`` in place of a real connector so a
    break-the-gate test proves not just that the call raised, but that it
    raised *before touching the connector at all* -- the assertion errors
    below would surface as the test failure if the gate were bypassed.
    """

    name = "civicrm"
    is_local = False

    def apply_repair(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("apply_repair must not be reachable without 2 distinct approvals")

    def inspect_repair(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("inspect_repair must not be reachable without 2 distinct approvals")

    def write_all(
        self, records: Sequence[GoldenRecord], fields: tuple[str, ...], *, dry_run: bool
    ) -> list[WriteResult]:
        raise AssertionError("write_all must not be reached by apply_repair_plan")


def _civicrm_connector(
    responses: list[tuple[int, dict[str, object]]],
) -> tuple[CivicrmConnector, FakeCivicrmTransport]:
    transport = FakeCivicrmTransport(responses)
    connector = CivicrmConnector(
        CivicrmConfig(endpoint="https://civicrm.example.org/civicrm/ajax/api4", api_key="key"),
        transport=transport,
    )
    return connector, transport


@pytest.fixture(scope="module")
def base_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Recipe, Path]:
    """One real write of the demo batch through the CiviCRM connector.

    Every test in this module copies this ``out`` directory rather than
    recomputing it, the same pattern ``tests/test_repair.py`` uses.
    """

    import os

    recipe = load_recipe(CIVICRM_RECIPE)
    out_dir = tmp_path_factory.mktemp("apply-repair-base") / "out"
    result = pipeline.run(recipe)
    os.environ["CIVICRM_API_KEY"] = "base-run-key"
    try:
        pipeline.export(result, recipe, out_dir=out_dir, transport=_RoutingTransport())
    finally:
        del os.environ["CIVICRM_API_KEY"]
    return recipe, out_dir


@pytest.fixture()
def run_dir(base_run: tuple[Recipe, Path], tmp_path: Path) -> tuple[Recipe, Path]:
    recipe, base_out = base_run
    out_dir = tmp_path / "out"
    shutil.copytree(base_out, out_dir)
    return recipe, out_dir


def _plan(
    recipe: Recipe, out_dir: Path, *, reviewer: str = "casey"
) -> tuple[Path, repair.PlannedSplit]:
    manifest_path = out_dir / "run_manifest.json"
    planned = repair.plan_split(
        recipe,
        manifest_path=manifest_path,
        cluster_id=MERGED_CLUSTER,
        reason=REASON,
        reviewer=reviewer,
    )
    return manifest_path, planned


# -- the second-reviewer gate: break-the-gate ----------------------------------


def test_zero_approvals_never_reach_the_connector(run_dir: tuple[Recipe, Path]) -> None:
    recipe, out_dir = run_dir
    manifest_path, _ = _plan(recipe, out_dir)

    with pytest.raises(repair.RepairApplyError, match="requires 2 distinct"):
        repair.apply_repair_plan(
            recipe, manifest_path=manifest_path, connector=_PoisonConnector(), dry_run=False
        )


def test_single_approval_never_reaches_the_connector(run_dir: tuple[Recipe, Path]) -> None:
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="alice")

    with pytest.raises(repair.RepairApplyError, match="requires 2 distinct"):
        repair.apply_repair_plan(
            recipe, manifest_path=manifest_path, connector=_PoisonConnector(), dry_run=False
        )


def test_the_same_reviewer_identity_twice_does_not_satisfy_the_gate(
    run_dir: tuple[Recipe, Path],
) -> None:
    """Case/whitespace variants of one name are one reviewer, not two (#97's rule)."""
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Alice Rivera")
    _, approvers = repair.record_repair_approval(
        planned.plan_path, approvals_path, reviewer="alice  rivera"
    )
    assert len(approvers) == 1

    with pytest.raises(repair.RepairApplyError, match="requires 2 distinct"):
        repair.apply_repair_plan(
            recipe, manifest_path=manifest_path, connector=_PoisonConnector(), dry_run=False
        )


def test_two_distinct_approvers_permit_execution(run_dir: tuple[Recipe, Path]) -> None:
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Alice Rivera")
    _, approvers = repair.record_repair_approval(
        planned.plan_path, approvals_path, reviewer="Bao Nguyen"
    )
    assert len(approvers) == 2

    connector, transport = _civicrm_connector(
        [
            (200, {"values": [{"id": 1, "version": "6.17.2"}]}),  # inspect_repair
            (200, {"values": [{"id": 100}]}),  # find old_external_id (survivor)
            (200, {"values": []}),  # find incoming:N002: no match
            (200, {"values": [{"id": 200}]}),  # Contact.create
        ]
    )

    applied = repair.apply_repair_plan(
        recipe, manifest_path=manifest_path, connector=connector, dry_run=False
    )

    assert applied.dry_run is False
    assert applied.destination_version == "6.17.2"
    assert sorted(applied.approvers) == ["Alice Rivera", "Bao Nguyen"]
    (op,) = [o for o in applied.operations if o.operation == "split-create"]
    assert op.record_id == "incoming:N002"
    assert op.action == "created"
    assert op.external_id == "200"
    assert transport.calls  # a real call was made this time

    assert applied.receipts_path is not None
    receipt = json.loads(applied.receipts_path.read_text(encoding="utf-8"))
    assert receipt["plan_digest"] == applied.plan_digest
    assert receipt["approvers"] == ["Alice Rivera", "Bao Nguyen"]
    # The receipt is the local PII-bearing artifact; the log gets a digest.
    provenance_text = (out_dir / "provenance.jsonl").read_text(encoding="utf-8")
    assert "jonathon" not in provenance_text
    ok, message = verify_log(out_dir / "provenance.jsonl")
    assert ok, message
    entries = [json.loads(line) for line in provenance_text.splitlines() if line.strip()]
    apply_entries = [e for e in entries if e["action"] == "repair-apply"]
    assert len(apply_entries) == 1
    assert apply_entries[0]["operation"] == "split-create"
    assert sorted(apply_entries[0]["approvers"]) == ["Alice Rivera", "Bao Nguyen"]
    assert apply_entries[0]["record_id"] == MERGED_CLUSTER


def test_rerun_after_execute_applies_nothing_twice(run_dir: tuple[Recipe, Path]) -> None:
    """The break-the-gate idempotency proof: a second execute makes no create call."""
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Alice Rivera")
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Bao Nguyen")

    first_connector, _ = _civicrm_connector(
        [
            (200, {"values": [{"id": 1, "version": "6.17.2"}]}),
            (200, {"values": [{"id": 100}]}),
            (200, {"values": []}),
            (200, {"values": [{"id": 200}]}),
        ]
    )
    first = repair.apply_repair_plan(
        recipe, manifest_path=manifest_path, connector=first_connector, dry_run=False
    )
    assert [o.action for o in first.operations] == ["created"]

    # Second run: the transport queue has no create response at all, so a
    # rerun that issued a second create would raise IndexError popping an
    # empty queue rather than silently duplicating anything.
    second_connector, second_transport = _civicrm_connector(
        [
            (200, {"values": [{"id": 1, "version": "6.17.2"}]}),
            (200, {"values": [{"id": 100}]}),
            (200, {"values": [{"id": 200}]}),  # incoming:N002 now exists
        ]
    )
    second = repair.apply_repair_plan(
        recipe, manifest_path=manifest_path, connector=second_connector, dry_run=False
    )

    assert [o.action for o in second.operations] == ["already-exists"]
    assert len(second_transport.calls) == 3  # no fourth (create) call


def test_dry_run_needs_no_approvals_and_makes_no_network_call(
    run_dir: tuple[Recipe, Path],
) -> None:
    recipe, out_dir = run_dir
    manifest_path, _ = _plan(recipe, out_dir)
    connector, transport = _civicrm_connector([])  # any call pops an empty queue and raises

    applied = repair.apply_repair_plan(
        recipe, manifest_path=manifest_path, connector=connector, dry_run=True
    )

    assert applied.dry_run is True
    assert applied.approvers == ()
    assert applied.receipts_path is None
    assert transport.calls == []
    actions = {op.action for op in applied.operations}
    assert actions == {"would-create"}  # this cluster's restore_fields is empty


def test_tampered_plan_is_refused_before_the_gate(run_dir: tuple[Recipe, Path]) -> None:
    """ADR 0012 T7: a plan edited after planning fails its digest binding."""
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Alice Rivera")
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Bao Nguyen")

    data = json.loads(planned.plan_path.read_text(encoding="utf-8"))
    data["split_records"][1]["fields"]["last_name"] = "tampered"
    tampered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    planned.plan_path.write_text(tampered, encoding="utf-8")

    with pytest.raises(repair.RepairApplyError, match="edited or replaced"):
        repair.apply_repair_plan(
            recipe, manifest_path=manifest_path, connector=_PoisonConnector(), dry_run=False
        )


def test_unsupported_live_version_refuses_execution(run_dir: tuple[Recipe, Path]) -> None:
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Alice Rivera")
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Bao Nguyen")

    connector, transport = _civicrm_connector(
        [(200, {"values": [{"id": 1, "version": "6.18.0"}]})]  # not the verified version
    )

    with pytest.raises(repair.RepairApplyError, match="not in .* verified repair-operation list"):
        repair.apply_repair_plan(
            recipe, manifest_path=manifest_path, connector=connector, dry_run=False
        )
    assert len(transport.calls) == 1  # inspect_repair ran; nothing further was attempted


def test_no_repair_plan_provenance_entry_refuses(run_dir: tuple[Recipe, Path]) -> None:
    """A plan file present without a matching provenance entry is not trusted."""
    recipe, out_dir = run_dir
    manifest_path = out_dir / "run_manifest.json"
    _, planned = _plan(recipe, out_dir)
    # Hand-write a second, unrelated plan file at the same path without ever
    # calling plan_split for it, so no repair-plan provenance entry exists
    # for its digest.
    forged = dict(json.loads(planned.plan_path.read_text(encoding="utf-8")))
    forged["reason"] = "a different reason never recorded to provenance"
    planned.plan_path.write_text(
        json.dumps(forged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(repair.RepairApplyError, match="edited or replaced|no repair-plan"):
        repair.apply_repair_plan(
            recipe, manifest_path=manifest_path, connector=_PoisonConnector(), dry_run=False
        )


def test_connector_without_repair_capability_gets_manual_instructions_message(
    run_dir: tuple[Recipe, Path],
) -> None:
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Alice Rivera")
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Bao Nguyen")

    class _NoRepairConnector:
        name = "civicrm"
        is_local = False

        def write_all(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("write_all must not be reached")

    with pytest.raises(repair.RepairApplyError, match="manual_instructions"):
        repair.apply_repair_plan(
            recipe,
            manifest_path=manifest_path,
            connector=_NoRepairConnector(),  # type: ignore[arg-type]
            dry_run=False,
        )


def test_mismatched_destination_is_refused(run_dir: tuple[Recipe, Path]) -> None:
    """A connector whose name does not match the plan's destination is refused."""
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Alice Rivera")
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Bao Nguyen")

    class _WrongNameConnector(_PoisonConnector):
        name = "not-civicrm"

    with pytest.raises(repair.RepairApplyError, match="destination"):
        repair.apply_repair_plan(
            recipe,
            manifest_path=manifest_path,
            connector=_WrongNameConnector(),
            dry_run=False,
        )


# -- record_repair_approval ----------------------------------------------------


def test_record_repair_approval_refuses_a_blank_reviewer(run_dir: tuple[Recipe, Path]) -> None:
    recipe, out_dir = run_dir
    _, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    with pytest.raises(repair.RepairApplyError):
        repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="   ")


def test_record_repair_approval_replan_starts_a_fresh_digest_at_zero(
    run_dir: tuple[Recipe, Path],
) -> None:
    """Replanning changes the digest, so old approvals do not carry forward."""
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Alice Rivera")
    repair.record_repair_approval(planned.plan_path, approvals_path, reviewer="Bao Nguyen")
    first_digest = planned.digest

    # Replan with a different reviewer: the plan payload changes, so the digest changes.
    _, replanned = _plan(recipe, out_dir, reviewer="dana")
    assert replanned.digest != first_digest

    approvers = repair._approvers_for_digest(approvals_path, replanned.digest)
    assert approvers == {}


# -- consent withholding (safety-net; see repair._withheld_split_members) -----


def test_withheld_split_members_is_empty_when_consent_is_not_required(
    run_dir: tuple[Recipe, Path],
) -> None:
    recipe, out_dir = run_dir
    _, planned = _plan(recipe, out_dir)
    plan_data = json.loads(planned.plan_path.read_text(encoding="utf-8"))
    assert (
        repair._withheld_split_members(
            recipe, plan_data, survivor="existing:E003", destination="civicrm", corrections=()
        )
        == frozenset()
    )


def test_withheld_split_members_withholds_a_revoked_member(tmp_path: Path) -> None:
    """Direct test of the safety net's own logic (see its docstring for why a
    plan produced by plan_split can never exercise this in practice)."""
    (tmp_path / "existing.csv").write_text(
        "id,First Name,Last Name,DOB,Email,Phone,Consent\nS001,Amy,Chen,1985-02-02,,,granted\n",
        encoding="utf-8",
    )
    (tmp_path / "incoming.csv").write_text(
        "id,First Name,Last Name,DOB,Email,Phone,Consent\nS002,Amy,Chen,1985-02-02,,,revoked\n",
        encoding="utf-8",
    )
    (tmp_path / "recipe.toml").write_text(
        """
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
""",
        encoding="utf-8",
    )
    recipe = load_recipe(tmp_path / "recipe.toml")
    assert recipe.require_consent is True
    plan_data: dict[str, Any] = {
        "split_records": [
            {"record_id": "existing:S001", "fields": {}},
            {"record_id": "incoming:S002", "fields": {}},
        ]
    }

    withheld = repair._withheld_split_members(
        recipe, plan_data, survivor="existing:S001", destination="civicrm", corrections=()
    )

    assert withheld == frozenset({"incoming:S002"})


# -- CLI wiring -----------------------------------------------------------------


def test_cli_approve_repair_and_dry_run_apply_repair(
    run_dir: tuple[Recipe, Path],
) -> None:
    recipe, out_dir = run_dir
    manifest_path, planned = _plan(recipe, out_dir)
    approvals_path = out_dir / repair.REPAIR_APPROVALS_FILENAME

    assert (
        main(
            [
                "approve-repair",
                "--plan",
                str(planned.plan_path),
                "--reviewer",
                "Alice Rivera",
                "--approvals",
                str(approvals_path),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "approve-repair",
                "--plan",
                str(planned.plan_path),
                "--reviewer",
                "Bao Nguyen",
                "--approvals",
                str(approvals_path),
            ]
        )
        == 0
    )
    approvers = repair._approvers_for_digest(approvals_path, planned.digest)
    assert len(approvers) == 2

    # Dry run: no --execute, so this makes no network call and needs no key.
    assert (
        main(
            [
                "apply-repair",
                "--config",
                str(CIVICRM_RECIPE),
                "--manifest",
                str(manifest_path),
            ]
        )
        == 0
    )
    # A dry run writes no receipts file.
    assert not (out_dir / repair.REPAIR_RECEIPTS_FILENAME).exists()


def test_cli_approve_repair_error_on_missing_plan(tmp_path: Path) -> None:
    code = main(
        [
            "approve-repair",
            "--plan",
            str(tmp_path / "no_such_plan.json"),
            "--reviewer",
            "Alice",
        ]
    )
    assert code == 2
