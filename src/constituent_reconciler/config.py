"""Recipe loading.

A run is configured by a small TOML file: which CSVs to read, how their columns
map onto the canonical fields, where consent lives, and the thresholds and policy
pack to apply. File paths in the recipe are resolved relative to the recipe's own
directory, so a recipe and its data can be moved together.
"""

from __future__ import annotations

import difflib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from constituent_reconciler import defaults
from constituent_reconciler.models import CANONICAL_FIELDS
from constituent_reconciler.policy import policy_for

# The declared recipe shape: every section and key a recipe may carry. This is
# the surface CONFIG_SCHEMA_VERSION in schema.py versions. Anything outside it
# is refused with the nearest valid name, fail-closed: a misspelled threshold
# must not silently run at the default.
RECIPE_SCHEMA: dict[str, frozenset[str]] = {
    "input": frozenset({"incoming", "existing", "id_column"}),
    "mapping": frozenset(CANONICAL_FIELDS),
    "consent": frozenset({"column", "require"}),
    "thresholds": frozenset({"prior", "auto", "review"}),
    "policy": frozenset({"pack"}),
    "normalize": frozenset({"address_backend"}),
    "extract": frozenset({"backend", "confidence_threshold"}),
    "output": frozenset(
        {
            "connector",
            "endpoint",
            "auth_env",
            "auth_header",
            "auth_scheme",
            "external_id_field",
            "api_version",
            "object_name",
        }
    ),
}


def _nearest(name: str, valid: frozenset[str]) -> str:
    # A generous cutoff so a compound misspelling ("auto_threshold" for "auto")
    # still earns a suggestion; the message is a hint, not a guess at intent.
    matches = difflib.get_close_matches(name, sorted(valid), n=1, cutoff=0.4)
    return f"; did you mean {matches[0]!r}?" if matches else ""


def _validate_shape(data: dict[str, Any]) -> None:
    """Reject unknown recipe sections and keys, naming the nearest valid one.

    Every other input surface raises on a typo (an unknown policy pack, an
    unknown address backend); the recipe must too, because it is the one file a
    non-technical operator edits. Silent acceptance would mean a misspelled
    section or key quietly changes what the run enforces.
    """

    for section, content in data.items():
        if section not in RECIPE_SCHEMA:
            raise ValueError(
                f"recipe has unknown section [{section}]"
                f"{_nearest(section, frozenset(RECIPE_SCHEMA))}"
            )
        if not isinstance(content, dict):
            raise ValueError(f"recipe [{section}] must be a table of keys, not a bare value")
        valid = RECIPE_SCHEMA[section]
        for key in content:
            if key not in valid:
                raise ValueError(
                    f"recipe [{section}] has unknown key {key!r}{_nearest(key, valid)}"
                )


@dataclass(frozen=True)
class NormalizeConfig:
    """Normalization settings, loaded from the recipe's [normalize] section.

    ``address_backend`` selects the address standardizer: ``"deterministic"``
    (the default vendored CASS-style ruleset) or ``"libpostal"`` (optional,
    requires the libpostal C library). It has no effect unless the recipe maps
    the ``address`` field.
    """

    address_backend: str = "deterministic"


@dataclass(frozen=True)
class ExtractConfig:
    """Document extraction settings, loaded from the recipe's [extract] section.

    ``backend`` selects which extractor runs:
      - ``"none"`` (default): no extraction; only CSV sources are read.
      - ``"pdfplumber"``: offline extraction for digitally-created PDFs.
      - ``"bedrock"``: route low-confidence pages to Claude on Bedrock (cloud
        call; forbidden under DV and HIPAA packs regardless of this setting).

    ``confidence_threshold`` is the page-level score below which a page is
    considered low-confidence and offered to the cloud seam if one is active.
    """

    backend: str = "none"
    confidence_threshold: float = 0.5


@dataclass(frozen=True)
class OutputConfig:
    """Where resolved records are written. Secrets are never stored here.

    ``auth_env`` names the environment variable that holds the credential (a
    CiviCRM API key, a Salesforce access token); the value is read at write time,
    not from the recipe. ``api_version`` and ``object_name`` apply to the
    Salesforce connector; CiviCRM ignores them.
    """

    connector: str = "csv"
    endpoint: str = ""
    auth_env: str = "CIVICRM_API_KEY"
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    external_id_field: str = "external_identifier"
    api_version: str = "v60.0"
    object_name: str = "Contact"


@dataclass(frozen=True)
class Recipe:
    incoming: Path
    mapping: dict[str, str]
    existing: Path | None = None
    id_column: str | None = None
    consent_column: str | None = None
    require_consent: bool = False
    policy_pack: str = "default"
    require_local_targets: bool = False
    aggregate_export: bool = False
    suppression_threshold: int = 11
    prior: float = defaults.DEFAULT_PRIOR
    auto_threshold: float = defaults.DEFAULT_AUTO_THRESHOLD
    review_threshold: float = defaults.DEFAULT_REVIEW_THRESHOLD
    fields: tuple[str, ...] = field(default_factory=tuple)
    normalize: NormalizeConfig = field(default_factory=NormalizeConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (base / candidate)


def load_recipe(path: str | Path, *, policy_pack: str | None = None) -> Recipe:
    """Load a recipe. ``policy_pack`` overrides the recipe's [policy] pack.

    The override exists so ``reconcile run --policy-pack dv`` can apply the DV
    posture to any recipe without editing it, which matches how the pack is
    described to users. An unknown pack raises a PolicyViolation, fail-closed,
    and an unknown section or key raises a ValueError naming the nearest valid
    one, so a typo cannot silently fall back to a default.
    """

    recipe_path = Path(path)
    base = recipe_path.parent
    with recipe_path.open("rb") as handle:
        data = tomllib.load(handle)

    _validate_shape(data)

    input_section = data.get("input", {})
    mapping_section = data.get("mapping", {})
    consent_section = data.get("consent", {})
    thresholds_section = data.get("thresholds", {})
    policy_section = data.get("policy", {})
    normalize_section = data.get("normalize", {})
    extract_section = data.get("extract", {})
    output_section = data.get("output", {})

    if "incoming" not in input_section:
        raise ValueError("recipe [input] must set 'incoming'")
    if not mapping_section:
        raise ValueError("recipe [mapping] must map at least first_name and last_name")

    mapping = {
        canonical: str(source)
        for canonical, source in mapping_section.items()
        if canonical in CANONICAL_FIELDS
    }
    active_fields = tuple(f for f in CANONICAL_FIELDS if f in mapping)
    if "first_name" not in mapping or "last_name" not in mapping:
        raise ValueError("recipe [mapping] must include first_name and last_name")

    pack = policy_pack if policy_pack is not None else str(policy_section.get("pack", "default"))
    policy = policy_for(pack)
    # A recipe may turn consent enforcement on explicitly even under a permissive
    # pack; it may not turn off a requirement the pack imposes (fail-closed).
    require_consent = policy.require_consent or bool(consent_section.get("require", False))

    existing_value = input_section.get("existing")
    existing = _resolve(base, str(existing_value)) if existing_value else None

    normalize = NormalizeConfig(
        address_backend=str(normalize_section.get("address_backend", "deterministic")),
    )

    extract = ExtractConfig(
        backend=str(extract_section.get("backend", "none")),
        confidence_threshold=float(extract_section.get("confidence_threshold", 0.5)),
    )

    output = OutputConfig(
        connector=str(output_section.get("connector", "csv")),
        endpoint=str(output_section.get("endpoint", "")),
        auth_env=str(output_section.get("auth_env", "CIVICRM_API_KEY")),
        auth_header=str(output_section.get("auth_header", "Authorization")),
        auth_scheme=str(output_section.get("auth_scheme", "Bearer")),
        external_id_field=str(output_section.get("external_id_field", "external_identifier")),
        api_version=str(output_section.get("api_version", "v60.0")),
        object_name=str(output_section.get("object_name", "Contact")),
    )

    return Recipe(
        incoming=_resolve(base, str(input_section["incoming"])),
        mapping=mapping,
        existing=existing,
        id_column=(str(input_section["id_column"]) if "id_column" in input_section else None),
        consent_column=(str(consent_section["column"]) if "column" in consent_section else None),
        require_consent=require_consent,
        policy_pack=pack,
        require_local_targets=policy.require_local_targets,
        aggregate_export=policy.aggregate_export,
        suppression_threshold=policy.suppression_threshold,
        prior=float(thresholds_section.get("prior", defaults.DEFAULT_PRIOR)),
        auto_threshold=float(thresholds_section.get("auto", defaults.DEFAULT_AUTO_THRESHOLD)),
        review_threshold=float(thresholds_section.get("review", defaults.DEFAULT_REVIEW_THRESHOLD)),
        fields=active_fields,
        normalize=normalize,
        extract=extract,
        output=output,
    )
