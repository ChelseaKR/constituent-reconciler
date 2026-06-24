"""Recipe loading.

A run is configured by a small TOML file: which CSVs to read, how their columns
map onto the canonical fields, where consent lives, and the thresholds and policy
pack to apply. File paths in the recipe are resolved relative to the recipe's own
directory, so a recipe and its data can be moved together.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from constituent_reconciler import defaults
from constituent_reconciler.models import CANONICAL_FIELDS

# Policy packs that flip consent enforcement on by default. The DV pack also
# forbids the cloud extraction seam, which v0.1 does not ship, so here it acts
# through consent enforcement; the seam restriction lands with extraction.
_CONSENT_REQUIRED_PACKS: frozenset[str] = frozenset({"dv", "hipaa"})


@dataclass(frozen=True)
class Recipe:
    incoming: Path
    mapping: dict[str, str]
    existing: Path | None = None
    id_column: str | None = None
    consent_column: str | None = None
    require_consent: bool = False
    policy_pack: str = "default"
    prior: float = defaults.DEFAULT_PRIOR
    auto_threshold: float = defaults.DEFAULT_AUTO_THRESHOLD
    review_threshold: float = defaults.DEFAULT_REVIEW_THRESHOLD
    fields: tuple[str, ...] = field(default_factory=tuple)


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (base / candidate)


def load_recipe(path: str | Path) -> Recipe:
    recipe_path = Path(path)
    base = recipe_path.parent
    with recipe_path.open("rb") as handle:
        data = tomllib.load(handle)

    input_section = data.get("input", {})
    mapping_section = data.get("mapping", {})
    consent_section = data.get("consent", {})
    thresholds_section = data.get("thresholds", {})
    policy_section = data.get("policy", {})

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

    pack = str(policy_section.get("pack", "default"))
    require_consent = bool(
        consent_section.get("require", pack in _CONSENT_REQUIRED_PACKS)
    )

    existing_value = input_section.get("existing")
    existing = _resolve(base, str(existing_value)) if existing_value else None

    return Recipe(
        incoming=_resolve(base, str(input_section["incoming"])),
        mapping=mapping,
        existing=existing,
        id_column=(str(input_section["id_column"]) if "id_column" in input_section else None),
        consent_column=(str(consent_section["column"]) if "column" in consent_section else None),
        require_consent=require_consent,
        policy_pack=pack,
        prior=float(thresholds_section.get("prior", defaults.DEFAULT_PRIOR)),
        auto_threshold=float(thresholds_section.get("auto", defaults.DEFAULT_AUTO_THRESHOLD)),
        review_threshold=float(
            thresholds_section.get("review", defaults.DEFAULT_REVIEW_THRESHOLD)
        ),
        fields=active_fields,
    )
