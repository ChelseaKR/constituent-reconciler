"""Tests for read-only split repair planning (UC-03 PR 2, ADR 0012).

The fixture triad the plan requires: a passing case (a really-written merged
cluster becomes a grounded local plan), an ambiguous case (a cluster the
manifest and batch cannot fully reconstruct refuses rather than guessing), and
fail-closed cases (blank reason, blank reviewer, unknown cluster id, manifest
mismatch, and their relatives all refuse with no plan written). Planning's own
invariants are asserted too: repeatability to the byte, no remote or local
mutation beyond the plan, the provenance digest, the binding cannot-links that
keep the bad cluster from re-forming, and the manual-instructions path for
destinations with no verified repair declaration.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from constituent_reconciler import decisions as decisions_mod
from constituent_reconciler import pipeline
from constituent_reconciler.cli import main
from constituent_reconciler.config import load_recipe
from constituent_reconciler.connectors.repair import (
    REPAIR_DECLARATIONS,
    RepairDeclaration,
    RepairDeclarationError,
    RepairOperation,
    declare_repair,
    repair_declaration,
    supported_operations,
)
from constituent_reconciler.manifest import manifest_hash
from constituent_reconciler.models import Cluster, Correction
from constituent_reconciler.provenance import ProvenanceLog, verify_log
from constituent_reconciler.repair import REPAIR_PLAN_FILENAME

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"
DEMO_FILES = ("recipe.toml", "existing.csv", "incoming.csv")

# A cluster the demo run auto-merges: existing:E003 with incoming:N002
# ("Jonathon Reyes", dob written as 04/12/1990).
MERGED_CLUSTER = "existing:E003"
MERGED_MEMBERS = ("existing:E003", "incoming:N002")

REASON = "reviewer found two different people merged into one written record"


@pytest.fixture(scope="module")
def base_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """One real demo run; tests copy its out directory instead of recomputing."""

    root = tmp_path_factory.mktemp("plan-split-base")
    demo = root / "demo"
    demo.mkdir()
    for name in DEMO_FILES:
        shutil.copy(EXAMPLES / name, demo / name)
    out_dir = root / "out"
    assert main(["run", "--config", str(demo / "recipe.toml"), "--out", str(out_dir)]) == 0
    return demo, out_dir


@pytest.fixture()
def run_dir(base_run: tuple[Path, Path], tmp_path: Path) -> tuple[Path, Path]:
    demo, base_out = base_run
    out_dir = tmp_path / "out"
    shutil.copytree(base_out, out_dir)
    return demo, out_dir


def _plan_args(demo: Path, out_dir: Path, cluster: str, **overrides: str) -> list[str]:
    values = {
        "--config": str(demo / "recipe.toml"),
        "--manifest": str(out_dir / "run_manifest.json"),
        "--cluster": cluster,
        "--reason": REASON,
        "--reviewer": "casey",
    }
    values.update(overrides)
    args = ["plan-split"]
    for key, value in values.items():
        args += [key, value]
    return args


def _plan(out_dir: Path) -> dict[str, object]:
    data = json.loads((out_dir / REPAIR_PLAN_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _synthetic_out(
    run_dir: tuple[Path, Path],
    tmp_path: Path,
    *,
    record_id: str,
    members: list[str],
    field_sources: dict[str, str],
    external_id: str | None = None,
) -> Path:
    """An out directory whose provenance records one crafted write entry.

    The manifest is the real one (so the recipe and input checks pass) and the
    chain is built through the real ProvenanceLog, so only the write entry's
    content is synthetic. This is how the reconstruction boundary is exercised
    without needing a source batch that produces the drift organically.
    """

    _, out_dir = run_dir
    synthetic = tmp_path / "synthetic-out"
    synthetic.mkdir()
    shutil.copy(out_dir / "run_manifest.json", synthetic / "run_manifest.json")
    manifest = json.loads((synthetic / "run_manifest.json").read_text(encoding="utf-8"))
    log = ProvenanceLog(synthetic / "provenance.jsonl")
    log.append_run_start(manifest_hash(manifest))
    log.append(
        action="written",
        record_id=record_id,
        members=members,
        consent=True,
        payload={},
        external_id=external_id if external_id is not None else record_id,
        field_sources=field_sources,
        fill_policy="survivor-then-lowest-id",
    )
    return synthetic


# -- the passing fixture ------------------------------------------------------


def test_plan_split_writes_a_grounded_local_plan(run_dir: tuple[Path, Path]) -> None:
    demo, out_dir = run_dir
    resolved_before = (out_dir / "resolved.csv").read_bytes()

    assert main(_plan_args(demo, out_dir, MERGED_CLUSTER)) == 0

    plan = _plan(out_dir)
    assert plan["repair_plan_schema"] == 1
    assert plan["cluster_id"] == MERGED_CLUSTER
    assert plan["old_external_id"] == "existing:E003"
    assert plan["survivor"] == "existing:E003"
    assert plan["reason"] == REASON
    assert plan["reviewer"] == "casey"
    split_records = plan["split_records"]
    assert isinstance(split_records, list)
    split = {entry["record_id"]: entry for entry in split_records}
    assert set(split) == set(MERGED_MEMBERS)
    # The proposed split record carries the member's own values, so the person
    # split out keeps their identity as the source batch states it.
    assert split["incoming:N002"]["fields"]["first_name"] == "jonathon"
    assert split["incoming:N002"]["fields"]["dob"] == "1990-04-12"
    assert plan["cannot_links"] == [list(MERGED_MEMBERS)]

    # The written run itself is untouched: planning is read-only.
    assert (out_dir / "resolved.csv").read_bytes() == resolved_before


def test_plan_digest_lands_in_provenance_and_values_do_not(
    run_dir: tuple[Path, Path],
) -> None:
    demo, out_dir = run_dir
    assert main(_plan_args(demo, out_dir, MERGED_CLUSTER)) == 0

    plan_bytes = (out_dir / REPAIR_PLAN_FILENAME).read_bytes()
    digest = hashlib.blake2b(plan_bytes, digest_size=32).hexdigest()
    log_text = (out_dir / "provenance.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(line) for line in log_text.splitlines() if line.strip()]
    last = entries[-1]
    assert last["action"] == "repair-plan"
    assert last["record_id"] == MERGED_CLUSTER
    assert last["members"] == list(MERGED_MEMBERS)
    assert last["content_hash"] == digest
    assert last["external_id"] == "existing:E003"
    assert last["consent"] is None
    ok, message = verify_log(out_dir / "provenance.jsonl")
    assert ok, message
    # Raw field values live only in the plan file, never in the log.
    for value in ("jonathon", "reyes", "1990-04-12"):
        assert value in plan_bytes.decode("utf-8")
        assert value not in log_text


def test_planning_twice_is_equivalent_and_mutates_nothing_further(
    run_dir: tuple[Path, Path],
) -> None:
    demo, out_dir = run_dir
    assert main(_plan_args(demo, out_dir, MERGED_CLUSTER)) == 0
    plan_first = (out_dir / REPAIR_PLAN_FILENAME).read_bytes()
    decisions_first = (out_dir / "decisions.json").read_bytes()
    resolved_first = (out_dir / "resolved.csv").read_bytes()

    assert main(_plan_args(demo, out_dir, MERGED_CLUSTER)) == 0

    # Equivalent to the byte: the plan carries no timestamp and the binding is
    # idempotent, so a second reviewer can regenerate and diff-compare plans.
    assert (out_dir / REPAIR_PLAN_FILENAME).read_bytes() == plan_first
    assert (out_dir / "decisions.json").read_bytes() == decisions_first
    assert (out_dir / "resolved.csv").read_bytes() == resolved_first
    ok, message = verify_log(out_dir / "provenance.jsonl")
    assert ok, message


def test_cannot_links_keep_the_bad_cluster_from_re_forming(
    run_dir: tuple[Path, Path],
) -> None:
    """Re-run reconciliation after planning: the split cluster must not return.

    The decisions file's rejected pairs are exactly what ``reconcile apply``
    loads as ``force_drop``, so this test replays them the way the next run
    would and asserts the two split members never share a cluster again.
    """

    demo, out_dir = run_dir
    assert main(_plan_args(demo, out_dir, MERGED_CLUSTER)) == 0
    data = json.loads((out_dir / "decisions.json").read_text(encoding="utf-8"))
    assert [sorted(pair) for pair in data["rejected"]] == [sorted(MERGED_MEMBERS)]
    force_drop = [frozenset(pair) for pair in data["rejected"]]

    recipe = load_recipe(demo / "recipe.toml")
    result = pipeline.run(recipe, force_drop=force_drop)

    for cluster in result.clusters:
        assert not set(MERGED_MEMBERS) <= set(cluster.members), cluster
    audit = data["audit"]["|".join(sorted(MERGED_MEMBERS))]
    assert audit[0]["reviewer"] == "casey"
    assert audit[0]["verdict"] == "rejected"


def test_binding_removes_a_conflicting_approval(run_dir: tuple[Path, Path]) -> None:
    demo, out_dir = run_dir
    decisions_path = out_dir / "decisions.json"
    decisions_path.write_text(
        json.dumps({"approved": [list(MERGED_MEMBERS)], "rejected": []}), encoding="utf-8"
    )

    assert main(_plan_args(demo, out_dir, MERGED_CLUSTER)) == 0

    data = json.loads(decisions_path.read_text(encoding="utf-8"))
    assert data["approved"] == []
    assert [sorted(pair) for pair in data["rejected"]] == [sorted(MERGED_MEMBERS)]


def test_restore_fields_name_the_member_that_supplied_the_value(
    run_dir: tuple[Path, Path], tmp_path: Path
) -> None:
    """A golden field filled from a non-surviving member becomes a restoration.

    The demo's organic merges are survivor-complete, so the written cluster
    here is a crafted two-member entry whose lineage is computed exactly the
    way the pipeline computes it: existing:E001 (no email) merged with
    existing:E004 (has email) fills email from E004.
    """

    demo, _ = run_dir
    recipe = load_recipe(demo / "recipe.toml")
    records = pipeline.ingest_normalized_records(recipe)
    members = ("existing:E001", "existing:E004")
    [golden] = decisions_mod.golden_records(
        [Cluster(cluster_id="existing:E001", members=members)], records, recipe.fields
    )
    assert golden.field_sources["email"] == "existing:E004"
    synthetic = _synthetic_out(
        run_dir,
        tmp_path,
        record_id="existing:E001",
        members=list(members),
        field_sources=golden.field_sources,
    )

    assert main(_plan_args(demo, synthetic, "existing:E001")) == 0

    plan = _plan(synthetic)
    assert plan["restore_fields"] == [
        {
            "field": "email",
            "written_value": "wei.chen@example.org",
            "supplied_by": "existing:E004",
            "restore_to": "",
        }
    ]


def test_corrections_replay_restores_the_written_lineage(
    run_dir: tuple[Path, Path], tmp_path: Path
) -> None:
    """A run written with corrections refuses until the corrections are replayed.

    Without the correction, reconstruction has no email lineage and the plan
    is refused rather than guessed; with the corrections file the written run
    applied, the plan reproduces the reviewed value.
    """

    demo, _ = run_dir
    recipe = load_recipe(demo / "recipe.toml")
    correction = Correction(
        record_id="incoming:N004",
        field="email",
        value="james.carter@example.org",
        reviewer="casey",
        corrected_at="2026-08-01T00:00:00+00:00",
        pair=frozenset(("existing:E002", "incoming:N004")),
    )
    records = pipeline.ingest_normalized_records(recipe, corrections=[correction])
    members = ("existing:E002", "incoming:N004")
    [golden] = decisions_mod.golden_records(
        [Cluster(cluster_id="existing:E002", members=members)], records, recipe.fields
    )
    assert golden.field_sources["email"] == "incoming:N004"
    synthetic = _synthetic_out(
        run_dir,
        tmp_path,
        record_id="existing:E002",
        members=list(members),
        field_sources=golden.field_sources,
    )

    assert main(_plan_args(demo, synthetic, "existing:E002")) == 2
    assert not (synthetic / REPAIR_PLAN_FILENAME).exists()

    (synthetic / "corrections.json").write_text(
        json.dumps(
            {
                "corrections": [
                    {
                        "left": "existing:E002",
                        "right": "incoming:N004",
                        "side": "right",
                        "field": "email",
                        "value": "james.carter@example.org",
                        "reviewer": "casey",
                        "corrected_at": "2026-08-01T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(_plan_args(demo, synthetic, "existing:E002")) == 0
    plan = _plan(synthetic)
    split_records = plan["split_records"]
    assert isinstance(split_records, list)
    split = {entry["record_id"]: entry for entry in split_records}
    assert split["incoming:N004"]["fields"]["email"] == "james.carter@example.org"


# -- the ambiguous fixture ----------------------------------------------------


def test_a_cluster_the_batch_cannot_reconstruct_refuses(
    run_dir: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    demo, _ = run_dir
    synthetic = _synthetic_out(
        run_dir,
        tmp_path,
        record_id="existing:E001",
        members=["existing:E001", "ghost:404"],
        field_sources={"first_name": "existing:E001"},
    )

    assert main(_plan_args(demo, synthetic, "existing:E001")) == 2

    assert not (synthetic / REPAIR_PLAN_FILENAME).exists()
    assert not (synthetic / "decisions.json").exists()
    err = capsys.readouterr().err
    assert "ghost:404" in err
    assert "refusing to guess" in err


def test_lineage_that_no_longer_matches_the_write_refuses(
    run_dir: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    demo, _ = run_dir
    # Real members, but a lineage claim the batch does not reproduce.
    synthetic = _synthetic_out(
        run_dir,
        tmp_path,
        record_id="existing:E001",
        members=["existing:E001", "existing:E004"],
        field_sources={"email": "existing:E001"},
    )

    assert main(_plan_args(demo, synthetic, "existing:E001")) == 2

    assert not (synthetic / REPAIR_PLAN_FILENAME).exists()
    assert "lineage" in capsys.readouterr().err


# -- the fail-closed fixtures -------------------------------------------------


def test_blank_reason_refuses(
    run_dir: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    demo, out_dir = run_dir
    code = main(_plan_args(demo, out_dir, MERGED_CLUSTER, **{"--reason": "   "}))
    assert code == 2
    assert "a reason" in capsys.readouterr().err
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


def test_blank_reviewer_refuses(
    run_dir: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    demo, out_dir = run_dir
    code = main(_plan_args(demo, out_dir, MERGED_CLUSTER, **{"--reviewer": ""}))
    assert code == 2
    assert "reviewer identity" in capsys.readouterr().err
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


def test_omitting_reason_or_reviewer_is_a_usage_error(run_dir: tuple[Path, Path]) -> None:
    demo, out_dir = run_dir
    args = [
        "plan-split",
        "--config",
        str(demo / "recipe.toml"),
        "--manifest",
        str(out_dir / "run_manifest.json"),
        "--cluster",
        MERGED_CLUSTER,
        "--reviewer",
        "casey",
    ]
    with pytest.raises(SystemExit) as excinfo:
        main(args)
    assert excinfo.value.code == 2
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


def test_unknown_cluster_id_refuses(
    run_dir: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    demo, out_dir = run_dir
    assert main(_plan_args(demo, out_dir, "no-such-cluster")) == 2
    assert "no write is recorded" in capsys.readouterr().err
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


def test_single_record_cluster_refuses(
    run_dir: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    demo, out_dir = run_dir
    assert main(_plan_args(demo, out_dir, "existing:E001")) == 2
    assert "no merge to split" in capsys.readouterr().err
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


def test_manifest_mismatch_refuses(
    run_dir: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    demo, out_dir = run_dir
    drifted = tmp_path / "drifted-demo"
    shutil.copytree(demo, drifted)
    with (drifted / "incoming.csv").open("a", encoding="utf-8") as handle:
        handle.write("N999,New,Person,2000-01-01,,,granted\n")

    code = main(_plan_args(drifted, out_dir, MERGED_CLUSTER))

    assert code == 2
    err = capsys.readouterr().err
    assert "incoming.csv" in err
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


def test_policy_pack_mismatch_refuses(
    run_dir: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    demo, out_dir = run_dir
    code = main(_plan_args(demo, out_dir, MERGED_CLUSTER, **{"--policy-pack": "dv"}))
    assert code == 2
    assert "policy pack" in capsys.readouterr().err
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


def test_missing_manifest_refuses(
    run_dir: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    demo, out_dir = run_dir
    code = main(
        _plan_args(demo, out_dir, MERGED_CLUSTER, **{"--manifest": str(out_dir / "nowhere.json")})
    )
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_tampered_provenance_refuses(
    run_dir: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    demo, out_dir = run_dir
    log_path = out_dir / "provenance.jsonl"
    log_path.write_text(
        log_path.read_text(encoding="utf-8").replace('"consent": true', '"consent": false', 1),
        encoding="utf-8",
    )
    assert main(_plan_args(demo, out_dir, MERGED_CLUSTER)) == 2
    assert "cannot anchor" in capsys.readouterr().err
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


def test_invalid_corrections_file_refuses(
    run_dir: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    demo, out_dir = run_dir
    bad = out_dir / "corrections.json"
    bad.write_text("not json", encoding="utf-8")
    assert main(_plan_args(demo, out_dir, MERGED_CLUSTER)) == 2
    assert "corrections" in capsys.readouterr().err
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


# -- unsupported destinations cannot be forced --------------------------------


def test_unsupported_destination_gets_manual_instructions(
    run_dir: tuple[Path, Path],
) -> None:
    demo, out_dir = run_dir
    assert main(_plan_args(demo, out_dir, MERGED_CLUSTER)) == 0
    plan = _plan(out_dir)
    assert plan["destination"] == "csv"
    assert plan["mode"] == "manual"
    assert plan["supported_operations"] == []
    instructions = plan["manual_instructions"]
    assert isinstance(instructions, list) and instructions
    joined = " ".join(str(step) for step in instructions)
    assert "existing:E003" in joined
    assert "No verified repair operations" in joined


@pytest.mark.parametrize("flag", [["--operation", "delete"], ["--force"]])
def test_no_flag_can_force_a_generic_operation(run_dir: tuple[Path, Path], flag: list[str]) -> None:
    """The CLI exposes no override: an unverified operation cannot be requested."""

    demo, out_dir = run_dir
    with pytest.raises(SystemExit) as excinfo:
        main(_plan_args(demo, out_dir, MERGED_CLUSTER) + flag)
    assert excinfo.value.code == 2
    assert not (out_dir / REPAIR_PLAN_FILENAME).exists()


# -- the capability declaration surface ---------------------------------------


def _declaration(**overrides: object) -> RepairDeclaration:
    values: dict[str, object] = {
        "connector": "scratch",
        "destination": "Scratch API v1",
        "verified_versions": ("5.81.0",),
        "operations": (
            RepairOperation(name="split-create", destructive=False),
            RepairOperation(name="field-restore", destructive=True),
        ),
        "vendor_documentation": "https://example.org/docs/repair",
        "checked_on": "2026-08-01",
        "live_instance": "disposable local container, exercised 2026-08-01",
    }
    values.update(overrides)
    return RepairDeclaration(**values)  # type: ignore[arg-type]


def test_declaration_gates_operations_on_the_exact_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declaration = _declaration()
    monkeypatch.setitem(REPAIR_DECLARATIONS, "scratch", declaration)
    assert repair_declaration("scratch") is declaration
    assert [op.name for op in supported_operations("scratch", "5.81.0")] == [
        "split-create",
        "field-restore",
    ]
    # A version the declaration does not enumerate is unsupported, even for
    # the same adapter; so is the blank version offline planning carries.
    assert supported_operations("scratch", "5.82.0") == ()
    assert supported_operations("scratch", "") == ()


@pytest.mark.parametrize(
    "versions",
    [(), ("",), ("5.*",), ("latest",), (">=5.81",), ("5.81 - 6.0",), ("5.81,5.82",)],
)
def test_declaration_refuses_non_enumerated_versions(versions: tuple[str, ...]) -> None:
    with pytest.raises(RepairDeclarationError):
        _declaration(verified_versions=versions)


@pytest.mark.parametrize(
    "overrides",
    [
        {"operations": ()},
        {
            "operations": (
                RepairOperation(name="merge", destructive=True),
                RepairOperation(name="merge", destructive=False),
            )
        },
        {"vendor_documentation": "  "},
        {"live_instance": ""},
        {"checked_on": "recently"},
        {"destination": ""},
    ],
)
def test_declaration_refuses_missing_evidence(overrides: dict[str, object]) -> None:
    with pytest.raises(RepairDeclarationError):
        _declaration(**overrides)


def test_operation_requires_a_name() -> None:
    with pytest.raises(RepairDeclarationError):
        RepairOperation(name=" ", destructive=True)


def test_declaring_a_connector_twice_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    declaration = _declaration()
    monkeypatch.setitem(REPAIR_DECLARATIONS, "scratch", declaration)
    with pytest.raises(RepairDeclarationError):
        declare_repair(_declaration())


def test_declare_repair_publishes_when_unclaimed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(REPAIR_DECLARATIONS, "scratch", raising=False)
    declaration = _declaration()
    declare_repair(declaration)
    try:
        assert repair_declaration("scratch") is declaration
    finally:
        REPAIR_DECLARATIONS.pop("scratch", None)
