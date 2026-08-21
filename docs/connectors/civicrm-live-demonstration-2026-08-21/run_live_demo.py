#!/usr/bin/env python3
"""End-to-end demonstration against a live CiviCRM instance (issue #67, #79).

Runs the full UC flow -- validate, dry-run, run, review, apply, write, rerun
for idempotency, then plan-split -> approve-repair -> apply-repair -- against
a disposable local civicrm/civicrm-docker Standalone instance, using only the
repo's committed synthetic demo fixtures (examples/intake-demo/*.csv). No
real client PII anywhere in this script or its inputs.

Every `reconcile` invocation goes through the actual CLI entry point
(constituent_reconciler.cli.main) so this exercises the real, released code
path, not a bespoke test harness. Live verification reads go straight to the
CiviCRM API v4 endpoint with the standard library, mirroring exactly what
connectors/civicrm.py itself sends.

Output: a content-free transcript to stdout (counts, hashes, ids -- no raw
field values) and a JSON summary written next to this script. The credential
is read from CIVICRM_API_KEY and never printed; every place it would appear
is redacted.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
REPO = DEMO_DIR.parents[2]  # docs/connectors/<this dir>/ -> repo root
RECONCILE_BIN = REPO / ".venv" / "bin" / "reconcile"
RECIPE = DEMO_DIR / "recipe-live.toml"
ENDPOINT = "http://127.0.0.1:8760/civicrm/ajax/api4"
API_KEY = os.environ["CIVICRM_API_KEY"]  # never printed

# Under "out/" (gitignored repo-wide) so re-running this script never dirties
# the docs tree it lives in.
RUN1 = DEMO_DIR / "out" / "run1"
RUN2 = DEMO_DIR / "out" / "run2"

# Run this with the repo's own venv interpreter so the in-process imports
# below (pipeline, ReviewSession, verify_log) resolve against the exact
# installed package and dependencies the CLI subprocess calls use too:
#   CIVICRM_API_KEY=... .venv/bin/python3 run_live_demo.py

TRANSCRIPT: list[str] = []


def log(line: str = "") -> None:
    print(line)
    TRANSCRIPT.append(line)


def banner(title: str) -> None:
    log()
    log("=" * 78)
    log(title)
    log("=" * 78)


def run_cli(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Invoke `reconcile` via subprocess -- the actual installed console_script."""
    cmd = [str(RECONCILE_BIN), *args]
    log(f"$ {' '.join(cmd)}")
    result = subprocess.run(  # noqa: S603
        cmd, cwd=REPO, capture_output=True, text=True, env={**os.environ}
    )
    log(result.stdout.rstrip())
    if result.stderr.strip():
        log("[stderr]")
        log(result.stderr.rstrip())
    log(f"[exit {result.returncode}]")
    if check and result.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}")
    return result


def civicrm_call(entity: str, action: str, params: dict[str, object]) -> dict[str, object]:
    """A direct, read-only live API v4 call, used only for verification."""
    url = f"{ENDPOINT}/{entity}/{action}"
    body = urllib.parse.urlencode({"params": json.dumps(params)}).encode()
    req = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    from constituent_reconciler import __version__
    from constituent_reconciler.provenance import verify_log
    from constituent_reconciler.review.session import ReviewSession

    banner("HEADER: what this run is, and against what")
    log(f"date (UTC):            {datetime.now(UTC).isoformat(timespec='seconds')}")
    log(f"reconciler package:    constituent-reconciler {__version__}")
    log(
        f"reconciler git commit: {subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True, text=True).stdout.strip()}"
    )  # noqa: S603, S607, E501
    version_read = civicrm_call("Domain", "get", {"select": ["version"], "limit": 1})
    civicrm_version = version_read["values"][0]["version"]
    log(f"CiviCRM version (live read): {civicrm_version}")
    log("CiviCRM API surface:    API v4, Standalone UF, authx bearer-key REST")
    log(f"endpoint:               {ENDPOINT}")
    log("credential:             CIVICRM_API_KEY env var (redacted in this transcript)")
    log(f"recipe:                 {RECIPE.name} (committed alongside this script)")
    log(
        "sources:                examples/intake-demo/{existing,incoming}.csv (synthetic, planted ground truth)"
    )  # noqa: E501

    banner("STEP 1: reconcile validate")
    run_cli(["validate", "--config", str(RECIPE)])

    banner("STEP 2: reconcile run --dry-run (preview, no write)")
    run_cli(["run", "--config", str(RECIPE), "--out", str(RUN1), "--dry-run"])
    assert not (RUN1 / "provenance.jsonl").exists(), "dry-run must write no provenance"

    banner("STEP 3: reconcile run (first REAL write to live CiviCRM)")
    run_cli(["run", "--config", str(RECIPE), "--out", str(RUN1)])

    banner("STEP 4: review the uncertain pairs, decided against the fixture's planted ground truth")  # noqa: E501
    ground_truth = json.loads((REPO / "examples/intake-demo/ground_truth.json").read_text())
    true_clusters = [frozenset(c) for c in ground_truth["clusters"]]

    from constituent_reconciler import pipeline
    from constituent_reconciler.config import load_recipe

    recipe = load_recipe(RECIPE)
    result = pipeline.run(recipe)
    decisions_path = RUN1 / "decisions.json"
    session = ReviewSession(result, recipe.fields, decisions_path, reviewer="demo-reviewer")
    log(f"review pairs pending: {len(result.review_pairs)}")
    for pair in session.views():
        key = frozenset((pair.left_id, pair.right_id))
        is_true_dup = any(key <= cluster for cluster in true_clusters)
        verdict = "approved" if is_true_dup else "rejected"
        session.record(pair.index, verdict)
        log(f"  pair {pair.index}: {pair.left_id} / {pair.right_id} -> {verdict} (ground truth)")
    log(f"decisions written: {decisions_path}")

    banner("STEP 5: reconcile apply (writes newly-confirmed merges to live CiviCRM)")
    apply1 = run_cli(
        ["apply", "--config", str(RECIPE), "--out", str(RUN1), "--decisions", str(decisions_path)]
    )

    banner("STEP 6: reconcile verify (provenance hash chain)")
    run_cli(["verify", "--provenance", str(RUN1 / "provenance.jsonl")])

    banner("STEP 7: live verification reads against CiviCRM")
    # A contact with email only (Wei Chen, existing:E004): confirm Email lands
    # in the dedicated entity, not a Contact join-field.
    wei = civicrm_call(
        "Contact",
        "get",
        {"where": [["external_identifier", "=", "existing:E004"]], "select": ["id"]},
    )
    wei_id = wei["values"][0]["id"]
    wei_email = civicrm_call(
        "Email",
        "get",
        {"where": [["contact_id", "=", wei_id], ["is_primary", "=", 1]], "select": ["id"]},
    )
    log(
        f"existing:E004 (email-bearing) -> Contact {wei_id}, Email entity rows: {len(wei_email['values'])}"
    )
    assert len(wei_email["values"]) == 1, "email must land in the dedicated Email entity"

    # A contact with phone only (Carlos Mendoza, existing:E006): confirm Phone
    # lands in the dedicated entity.
    carlos = civicrm_call(
        "Contact",
        "get",
        {"where": [["external_identifier", "=", "existing:E006"]], "select": ["id"]},
    )
    carlos_id = carlos["values"][0]["id"]
    carlos_phone = civicrm_call(
        "Phone",
        "get",
        {"where": [["contact_id", "=", carlos_id], ["is_primary", "=", 1]], "select": ["id"]},
    )
    log(
        f"existing:E006 (phone-bearing) -> Contact {carlos_id}, Phone entity rows: {len(carlos_phone['values'])}"
    )
    assert len(carlos_phone["values"]) == 1, "phone must land in the dedicated Phone entity"

    # Consent-withheld: incoming:N009 (Omar Said, revoked) must never reach CiviCRM.
    omar = civicrm_call(
        "Contact",
        "get",
        {"where": [["external_identifier", "=", "incoming:N009"]], "select": ["id"]},
    )
    log(f"incoming:N009 (revoked consent) -> Contact rows in CiviCRM: {len(omar['values'])}")
    assert len(omar["values"]) == 0, "a revoked-consent record must never reach CiviCRM"
    withheld_path = RUN1 / "withheld.csv"
    log(
        f"withheld.csv exists: {withheld_path.exists()}, sha256: {sha256_of(withheld_path) if withheld_path.exists() else 'n/a'}"
    )  # noqa: E501

    # The auto-merged Jonathan/Jonathon pair (existing:E003 + incoming:N002):
    # confirm one contact, not two.
    jonathan = civicrm_call(
        "Contact",
        "get",
        {"where": [["external_identifier", "=", "existing:E003"]], "select": ["id"]},
    )
    jonathon_as_own_id = civicrm_call(
        "Contact",
        "get",
        {"where": [["external_identifier", "=", "incoming:N002"]], "select": ["id"]},
    )
    log(f"existing:E003 (survivor of the auto-merge) -> Contact rows: {len(jonathan['values'])}")
    log(
        f"incoming:N002 (merged away, no own contact expected yet) -> Contact rows: {len(jonathon_as_own_id['values'])}"
    )  # noqa: E501
    assert len(jonathan["values"]) == 1
    assert len(jonathon_as_own_id["values"]) == 0, (
        "a merged-away member has no contact of its own before repair"
    )  # noqa: E501

    banner("STEP 8: RERUN the same reviewed batch (idempotency: updates, not duplicates)")
    apply2 = run_cli(
        ["apply", "--config", str(RECIPE), "--out", str(RUN2), "--decisions", str(decisions_path)]
    )
    run2_provenance = RUN2 / "provenance.jsonl"
    run_cli(["verify", "--provenance", str(run2_provenance)])

    def action_counts(stdout: str) -> str:
        for line in stdout.splitlines():
            if line.strip().startswith("connector "):
                return line.strip()
        return "(not found)"

    log(f"first run's connector summary:  {action_counts(apply1.stdout)}")
    log(f"second run's connector summary: {action_counts(apply2.stdout)}")

    contact_count_after_rerun = civicrm_call(
        "Contact",
        "get",
        {"where": [["external_identifier", "=", "existing:E004"]], "select": ["id"]},
    )
    log(
        f"existing:E004 contact rows after rerun (must stay 1, never 2): {len(contact_count_after_rerun['values'])}"
    )  # noqa: E501
    assert len(contact_count_after_rerun["values"]) == 1

    banner("STEP 9: repair path -- plan-split, approve-repair x2, apply-repair --execute")
    manifest_path = RUN1 / "run_manifest.json"
    run_cli(
        [
            "plan-split",
            "--config",
            str(RECIPE),
            "--manifest",
            str(manifest_path),
            "--cluster",
            "existing:E003",
            "--reason",
            "live demo: reviewer found E003/N002 merged into one written record",
            "--reviewer",
            "demo-reviewer-a",
        ]
    )
    plan_path = RUN1 / "repair_plan.json"
    approvals_path = RUN1 / "repair_approvals.json"
    run_cli(
        [
            "approve-repair",
            "--plan",
            str(plan_path),
            "--reviewer",
            "demo-reviewer-a",
            "--approvals",
            str(approvals_path),
        ]
    )
    run_cli(
        [
            "approve-repair",
            "--plan",
            str(plan_path),
            "--reviewer",
            "demo-reviewer-b",
            "--approvals",
            str(approvals_path),
        ]
    )
    log("dry-run preview first (no network call, no credential needed):")
    run_cli(
        [
            "apply-repair",
            "--config",
            str(RECIPE),
            "--manifest",
            str(manifest_path),
            "--plan",
            str(plan_path),
            "--approvals",
            str(approvals_path),
        ]
    )
    log("now --execute, against the live instance, gated by the two approvals above:")
    run_cli(
        [
            "apply-repair",
            "--config",
            str(RECIPE),
            "--manifest",
            str(manifest_path),
            "--plan",
            str(plan_path),
            "--approvals",
            str(approvals_path),
            "--execute",
        ]
    )
    receipts_path = RUN1 / "repair_receipts.json"
    log(
        f"receipts written: {receipts_path.exists()}, sha256: {sha256_of(receipts_path) if receipts_path.exists() else 'n/a'}"
    )  # noqa: E501

    n002_after_repair = civicrm_call(
        "Contact",
        "get",
        {
            "where": [["external_identifier", "=", "incoming:N002"]],
            "select": ["id", "first_name", "last_name"],
        },  # noqa: E501
    )
    log(
        f"incoming:N002 after repair -> Contact rows: {len(n002_after_repair['values'])} (expect 1: split-create ran)"
    )  # noqa: E501
    assert len(n002_after_repair["values"]) == 1

    banner("STEP 10: rerun apply-repair --execute (idempotency: no duplicate contact)")
    apply_repair_2 = run_cli(
        [
            "apply-repair",
            "--config",
            str(RECIPE),
            "--manifest",
            str(manifest_path),
            "--plan",
            str(plan_path),
            "--approvals",
            str(approvals_path),
            "--execute",
        ]
    )
    n002_after_rerun = civicrm_call(
        "Contact",
        "get",
        {"where": [["external_identifier", "=", "incoming:N002"]], "select": ["id"]},
    )
    log(
        f"incoming:N002 after repair rerun -> Contact rows (must stay 1): {len(n002_after_rerun['values'])}"
    )
    assert len(n002_after_rerun["values"]) == 1
    assert "already-exists" in apply_repair_2.stdout, (
        "the rerun must report already-exists, not a second create"
    )

    banner("STEP 11: final provenance verification, both run directories")
    ok1, message1 = verify_log(RUN1 / "provenance.jsonl")
    ok2, message2 = verify_log(RUN2 / "provenance.jsonl")
    log(f"out1 provenance.jsonl: {message1}")
    log(f"out2 provenance.jsonl: {message2}")
    assert ok1 and ok2

    banner("SUMMARY")
    summary = {
        "date_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "reconciler_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            capture_output=True,
            text=True,  # noqa: S603, S607
        ).stdout.strip(),
        "civicrm_version": civicrm_version,
        "civicrm_api": "v4",
        "endpoint_host": "127.0.0.1:8760 (disposable local Docker instance)",
        "checks": {
            "email_in_dedicated_entity": True,
            "phone_in_dedicated_entity": True,
            "revoked_consent_withheld": True,
            "rerun_updates_not_duplicates": True,
            "repair_split_create_landed": True,
            "repair_rerun_idempotent": True,
            "provenance_verifies_both_runs": True,
        },
        "artifact_hashes": {
            "out1/repair_plan.json": sha256_of(plan_path),
            "out1/repair_receipts.json": sha256_of(receipts_path),
            "out1/provenance.jsonl": sha256_of(RUN1 / "provenance.jsonl"),
            "out2/provenance.jsonl": sha256_of(RUN2 / "provenance.jsonl"),
        },
    }
    log(json.dumps(summary, indent=2))

    (DEMO_DIR / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (DEMO_DIR / "transcript.txt").write_text("\n".join(TRANSCRIPT) + "\n", encoding="utf-8")
    log("\nAll assertions passed. summary.json and transcript.txt written beside this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
