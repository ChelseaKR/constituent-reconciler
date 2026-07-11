"""Recipe loading.

A run is configured by a small TOML file: which CSVs to read, how their columns
map onto the canonical fields, where consent lives, and the thresholds and policy
pack to apply. File paths in the recipe are resolved relative to the recipe's own
directory, so a recipe and its data can be moved together.

Loading is fail-closed on the shape of the file itself (FIX-01): an unknown
section or an unknown key inside a known section raises, naming the nearest
valid spelling, rather than being silently ignored by ``dict.get``. This is
the one input surface that used to fail open; a misspelled ``auto_threshold``
or a typo'd ``[consnet]`` section used to run at a default instead of raising,
which is exactly backwards for the one file a non-technical operator hand-edits
(``CLAUDE.md``'s personas B1 and A4).
"""

from __future__ import annotations

import difflib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from constituent_reconciler import defaults
from constituent_reconciler.decisions import DEFAULT_FILL_POLICY, FILL_POLICIES
from constituent_reconciler.models import CANONICAL_FIELDS
from constituent_reconciler.policy import policy_for
from constituent_reconciler.suppression import ensure_non_identifying

# The declared recipe shape (what schema.CONFIG_SCHEMA_VERSION versions):
# section name to the set of keys that section accepts. "mapping" is not
# listed here because its keys are the canonical field names themselves,
# checked separately against CANONICAL_FIELDS.
_SECTION_KEYS: dict[str, frozenset[str]] = {
    "input": frozenset({"incoming", "existing", "id_column"}),
    "mapping": frozenset(CANONICAL_FIELDS),
    "consent": frozenset({"column", "date", "expires", "scope", "require"}),
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
    "household": frozenset({"enabled"}),
    "comparable": frozenset({"export", "breakdown_fields", "period"}),
    "provenance": frozenset({"tsa_url"}),
    "review": frozenset({"require_second_reviewer"}),
}

_KNOWN_SECTIONS = frozenset(_SECTION_KEYS)


class RecipeError(ValueError):
    """A recipe failed validation: an unknown section, an unknown key, or a
    missing required value. Fail-closed, named after the exact spelling.
    """


def _nearest(name: str, candidates: frozenset[str]) -> str:
    """A "did you mean" suffix naming the closest valid spelling, if any."""

    matches = difflib.get_close_matches(name, sorted(candidates), n=1)
    return f" (did you mean {matches[0]!r}?)" if matches else ""


def _validate_shape(data: dict[str, Any]) -> None:
    """Raise on any section or key the declared recipe schema does not know.

    Runs before any section is read, so a typo anywhere in the file is
    reported once, by name, rather than silently taking a default.
    """

    for section_name, section_body in data.items():
        if section_name not in _KNOWN_SECTIONS:
            raise RecipeError(
                f"recipe has an unknown section [{section_name}]"
                f"{_nearest(section_name, _KNOWN_SECTIONS)}"
            )
        if not isinstance(section_body, dict):
            raise RecipeError(f"recipe section [{section_name}] must be a table")
        known_keys = _SECTION_KEYS[section_name]
        for key in section_body:
            if key not in known_keys:
                kind = "canonical field" if section_name == "mapping" else "key"
                raise RecipeError(
                    f"recipe [{section_name}] has an unknown {kind} {key!r}"
                    f"{_nearest(key, known_keys)}"
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
      - ``"pdfplumber+ocr"``: offline extraction that also OCRs (via
        Tesseract, the optional ``ocr`` extra) any page with no embedded text
        layer, so an image-only scanned page yields fields instead of an
        empty record.
      - ``"bedrock"``: route low-confidence pages to Claude on Bedrock (cloud
        call; forbidden under DV and HIPAA packs regardless of this setting).

    ``confidence_threshold`` is the page-level score below which a page is
    considered low-confidence and offered to the cloud seam if one is active.
    """

    backend: str = "none"
    confidence_threshold: float = 0.5


@dataclass(frozen=True)
class HouseholdConfig:
    """Household-grouping settings, loaded from the recipe's [household] section.

    ``enabled`` defaults to False under every policy pack, including ``dv``: the
    grouping step (household.py) never runs unless a recipe turns it on
    explicitly. See docs/decisions for the DV interaction; the off-by-default is
    an invariant, not a convenience default, because inferring co-residence from
    a shared address is itself sensitive (shelter residents share an address
    without being a household).
    """

    enabled: bool = False


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
    """A loaded run configuration. See ``load_recipe`` for how each field is set.

    The four ``consent_*_column`` fields name optional source columns that,
    together, build each record's ``models.Consent``: ``consent_column`` is the
    raw status token, ``consent_date_column`` and ``consent_expires_column`` are
    ISO-8601 (YYYY-MM-DD) dates, and ``consent_scope_column`` is a
    comma-separated list of destination (connector) names the consent covers. A
    recipe may map any subset; an unmapped column leaves that part of the
    lifecycle unset (no expiry ceiling, no scope restriction) rather than
    inventing a default.
    """

    incoming: Path
    mapping: dict[str, str]
    existing: Path | None = None
    id_column: str | None = None
    consent_column: str | None = None
    consent_date_column: str | None = None
    consent_expires_column: str | None = None
    consent_scope_column: str | None = None
    require_consent: bool = False
    policy_pack: str = "default"
    require_local_targets: bool = False
    aggregate_export: bool = False
    require_second_reviewer: bool = False
    suppression_threshold: int = 11
    # The comparable-database export profile is explicit opt-in: no breakdown
    # beyond the base consent/resolution counts is emitted unless the recipe
    # names non-identifying fields, and identifying canonical fields are
    # refused, fail-closed, at load time (see ``load_recipe``).
    comparable_export: bool = False
    comparable_breakdown_fields: tuple[str, ...] = ()
    comparable_period: str = ""
    fill_policy: str = DEFAULT_FILL_POLICY
    prior: float = defaults.DEFAULT_PRIOR
    auto_threshold: float = defaults.DEFAULT_AUTO_THRESHOLD
    review_threshold: float = defaults.DEFAULT_REVIEW_THRESHOLD
    fields: tuple[str, ...] = field(default_factory=tuple)
    normalize: NormalizeConfig = field(default_factory=NormalizeConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    household: HouseholdConfig = field(default_factory=HouseholdConfig)
    tsa_url: str = ""


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (base / candidate)


def load_recipe(
    path: str | Path,
    *,
    policy_pack: str | None = None,
    tsa_url: str | None = None,
) -> Recipe:
    """Load a recipe. ``policy_pack`` overrides the recipe's [policy] pack.

    The override exists so ``reconcile run --policy-pack dv`` can apply the DV
    posture to any recipe without editing it, which matches how the pack is
    described to users. An unknown pack raises a PolicyViolation, fail-closed.
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
    household_section = data.get("household", {})
    comparable_section = data.get("comparable", {})
    review_section = data.get("review", {})
    provenance_section = data.get("provenance", {})

    if "incoming" not in input_section:
        raise RecipeError("recipe [input] must set 'incoming'")
    if not mapping_section:
        raise RecipeError("recipe [mapping] must map at least first_name and last_name")

    # _validate_shape already rejected any mapping key outside CANONICAL_FIELDS,
    # so every key here is a real canonical field.
    mapping = {canonical: str(source) for canonical, source in mapping_section.items()}
    active_fields = tuple(f for f in CANONICAL_FIELDS if f in mapping)
    if "first_name" not in mapping or "last_name" not in mapping:
        raise RecipeError("recipe [mapping] must include first_name and last_name")

    pack = policy_pack if policy_pack is not None else str(policy_section.get("pack", "default"))
    policy = policy_for(pack)
    # The survivorship fill policy is named explicitly in the recipe ([policy]
    # key "fill", or "fill_policy") and validated here, so a typo raises at load
    # time instead of silently changing how golden records are merged.
    fill_value = policy_section.get("fill", policy_section.get("fill_policy"))
    fill_policy = str(fill_value) if fill_value is not None else DEFAULT_FILL_POLICY
    if fill_policy not in FILL_POLICIES:
        raise ValueError(
            f"recipe [policy] fill must be one of {', '.join(FILL_POLICIES)}; got {fill_policy!r}"
        )
    # A recipe may turn consent enforcement on explicitly even under a permissive
    # pack; it may not turn off a requirement the pack imposes (fail-closed).
    require_consent = policy.require_consent or bool(consent_section.get("require", False))
    # Same rule for two-person review: the [review] section may turn it on under
    # any pack, and may not turn off the DV pack's default.
    require_second_reviewer = policy.require_second_reviewer or bool(
        review_section.get("require_second_reviewer", False)
    )

    existing_value = input_section.get("existing")
    existing = _resolve(base, str(existing_value)) if existing_value else None

    normalize = NormalizeConfig(
        address_backend=str(normalize_section.get("address_backend", "deterministic")),
    )

    extract = ExtractConfig(
        backend=str(extract_section.get("backend", "none")),
        confidence_threshold=float(extract_section.get("confidence_threshold", 0.5)),
    )

    # Off by default under every policy pack; a recipe must opt in explicitly.
    # The dv pack does not force this off (a shelter provider may still want a
    # reviewed suggestion list on its own machine), but it never turns it on:
    # the only source of "enabled" is the recipe itself.
    household = HouseholdConfig(enabled=bool(household_section.get("enabled", False)))

    # Comparable-export settings. The identifying-field check runs here, at
    # load time, so a bad recipe fails before any record is read, not only
    # when the report is actually built (comparable_summary re-checks too, as
    # defense in depth for any caller that builds a Recipe outside this
    # loader).
    comparable_breakdown_fields = tuple(
        str(f) for f in comparable_section.get("breakdown_fields", [])
    )
    ensure_non_identifying(comparable_breakdown_fields)

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
        consent_date_column=(str(consent_section["date"]) if "date" in consent_section else None),
        consent_expires_column=(
            str(consent_section["expires"]) if "expires" in consent_section else None
        ),
        consent_scope_column=(
            str(consent_section["scope"]) if "scope" in consent_section else None
        ),
        require_consent=require_consent,
        policy_pack=pack,
        require_local_targets=policy.require_local_targets,
        aggregate_export=policy.aggregate_export,
        require_second_reviewer=require_second_reviewer,
        suppression_threshold=policy.suppression_threshold,
        comparable_export=bool(comparable_section.get("export", False)),
        comparable_breakdown_fields=comparable_breakdown_fields,
        comparable_period=str(comparable_section.get("period", "")),
        fill_policy=fill_policy,
        prior=float(thresholds_section.get("prior", defaults.DEFAULT_PRIOR)),
        auto_threshold=float(thresholds_section.get("auto", defaults.DEFAULT_AUTO_THRESHOLD)),
        review_threshold=float(thresholds_section.get("review", defaults.DEFAULT_REVIEW_THRESHOLD)),
        fields=active_fields,
        normalize=normalize,
        extract=extract,
        output=output,
        household=household,
        tsa_url=tsa_url if tsa_url is not None else str(provenance_section.get("tsa_url", "")),
    )
