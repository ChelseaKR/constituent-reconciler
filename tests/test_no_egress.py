"""Merge-blocking DV-pack invariants: client PII must not egress.

These tests encode the confidentiality posture a victim-service provider needs
under VAWA and FVPSA, as enforced behavior. If any of them fails, the pack is no
longer safe to claim, so they gate the merge. The legal grounding for each
invariant is recorded in docs/RESPONSIBLE-TECH-AUDITS.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.config import load_recipe
from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.extract.seam import LocalSeam, NoOpSeam, make_seam
from constituent_reconciler.policy import PolicyViolation

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "intake-demo"


def test_dv_pack_refuses_a_non_local_write_target(tmp_path: Path) -> None:
    # CiviCRM is a network target; under the DV pack the export must refuse it
    # rather than send client records off the machine.
    recipe = load_recipe(EXAMPLES / "recipe-civicrm.toml", policy_pack="dv")
    result = pipeline.run(recipe)
    with pytest.raises(PolicyViolation, match="non-local write target"):
        pipeline.export(result, recipe, out_dir=tmp_path)
    # Nothing was written: the refusal happens before any connector write.
    assert not (tmp_path / "resolved.csv").exists()


def test_dv_pack_refuses_the_salesforce_target_too(tmp_path: Path) -> None:
    from dataclasses import replace

    from constituent_reconciler.config import OutputConfig

    base = load_recipe(EXAMPLES / "recipe.toml", policy_pack="dv")
    recipe = replace(
        base,
        output=OutputConfig(connector="salesforce", endpoint="https://x.my.salesforce.com"),
    )
    result = pipeline.run(recipe)
    with pytest.raises(PolicyViolation, match="non-local write target"):
        pipeline.export(result, recipe, out_dir=tmp_path)
    assert not (tmp_path / "resolved.csv").exists()


def test_dv_pack_refuses_the_webhook_target_too(tmp_path: Path) -> None:
    from dataclasses import replace

    from constituent_reconciler.config import OutputConfig

    base = load_recipe(EXAMPLES / "recipe.toml", policy_pack="dv")
    recipe = replace(
        base,
        output=OutputConfig(connector="webhook", endpoint="https://example.org/hooks/reconciler"),
    )
    result = pipeline.run(recipe)
    with pytest.raises(PolicyViolation, match="non-local write target"):
        pipeline.export(result, recipe, out_dir=tmp_path)
    # Nothing was written: the refusal happens before any connector write, so no
    # network POST was even attempted, let alone one carrying client PII.
    assert not (tmp_path / "resolved.csv").exists()


def test_dv_pack_allows_the_local_csv_target(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    # The local CSV is permitted; the org keeps client data in its own database.
    assert (tmp_path / "resolved.csv").exists()
    assert summary.aggregate is not None


def test_dv_pack_refuses_a_network_timestamp_authority(tmp_path: Path) -> None:
    from dataclasses import replace

    # The RFC 3161 request sends a hash derived from written client fields to
    # the TSA. VAWA/FVPSA guidance treats hashed client information as still
    # protected, so the DV pack refuses the network authority before any write.
    base = load_recipe(EXAMPLES / "recipe-dv.toml")
    recipe = replace(base, tsa_url="https://tsa.example/tsr")
    result = pipeline.run(recipe)
    with pytest.raises(PolicyViolation, match="timestamp"):
        pipeline.export(result, recipe, out_dir=tmp_path)
    assert not (tmp_path / "resolved.csv").exists()
    assert not (tmp_path / "provenance.jsonl").exists()


def test_dv_pack_fuses_the_cloud_extraction_seam_off() -> None:
    # Even if a recipe asks for the Bedrock backend, the DV pack returns a NoOp
    # seam: no page image, no field value, leaves the machine.
    seam = make_seam("dv", backend="bedrock")
    assert isinstance(seam, NoOpSeam)
    assert seam.is_enabled() is False


def test_dv_pack_fuses_the_local_seam_off_by_default() -> None:
    # A local model does not egress PII, but that is a separate question from
    # whether model-assisted extraction is acceptable at all under a given
    # org's VAWA reading (docs/adr/0010-local-model-seam.md). The dv
    # pack has not recorded that analysis, so backend="local" alone still
    # produces a NoOp seam, same as backend="bedrock".
    seam = make_seam("dv", backend="local")
    assert isinstance(seam, NoOpSeam)
    assert seam.is_enabled() is False


def test_dv_pack_local_seam_still_cannot_egress_even_when_explicitly_enabled() -> None:
    # A deployer whose counsel has cleared model-assisted extraction can opt
    # in via local_model_override. Even then, the resulting seam is forced
    # to loopback: this proves that turning on model-assisted extraction
    # under dv never opens a path to a non-local host, regardless of any
    # OLLAMA_HOST a deployer's environment might set to something else.
    seam = make_seam("dv", backend="local", local_model_override=True)
    assert isinstance(seam, LocalSeam)
    with pytest.raises(ValueError, match="loopback"):
        LocalSeam(host="http://not-loopback.example.com:11434")


def test_dv_pack_withholds_non_consented_records_without_field_values(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    result = pipeline.run(recipe)
    exportable, withheld = partition_by_consent(
        result.golden, require_consent=recipe.require_consent
    )
    # N009 (consent revoked) is withheld.
    withheld_members = {m for entry in withheld for m in entry.members}
    assert "incoming:N009" in withheld_members

    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    assert summary.withheld_path is not None
    withheld_text = summary.withheld_path.read_text(encoding="utf-8")
    # The withheld record is recorded by id and reason only; no field value of a
    # non-consented person appears in the artifact. The reason distinguishes an
    # explicit revocation from a merely absent consent.
    assert "incoming:N009" in withheld_text or any(
        "incoming:N009" in w.members for w in summary.withheld
    )
    assert "revoked" in withheld_text


def test_dv_aggregate_summary_carries_no_field_values(tmp_path: Path) -> None:
    recipe = load_recipe(EXAMPLES / "recipe-dv.toml")
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path)

    assert summary.aggregate_path is not None
    payload = json.loads(summary.aggregate_path.read_text(encoding="utf-8"))
    assert "total_resolved" in payload
    assert "breakdowns" in payload

    # No resolved record's name, email, or member id may appear in the shareable
    # aggregate. Check against the actual exportable field values.
    exportable, _ = partition_by_consent(result.golden, require_consent=recipe.require_consent)
    blob = summary.aggregate_path.read_text(encoding="utf-8")
    for record in exportable:
        for value in record.fields.values():
            if value:
                assert value not in blob
        assert record.cluster_id not in blob


def test_default_pack_writes_no_aggregate_summary(tmp_path: Path) -> None:
    # The aggregate artifact is a DV-pack obligation, not a default behavior.
    recipe = load_recipe(EXAMPLES / "recipe.toml")
    result = pipeline.run(recipe)
    summary = pipeline.export(result, recipe, out_dir=tmp_path)
    assert summary.aggregate is None
    assert summary.aggregate_path is None
    assert not (tmp_path / "aggregate_summary.json").exists()
