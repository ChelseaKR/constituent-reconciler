"""Run manifest: evidence of what produced an output directory.

The provenance log proves what was written; on its own it cannot prove which
inputs and configuration produced those writes. The manifest closes that gap.
Each non-dry-run export stamps ``out/run_manifest.json`` with BLAKE2b digests
of the recipe file and of every input file (digests only, never field values),
the package and Splink versions, the resolved thresholds, the policy pack, and
the declared schema versions. The manifest's own hash is then appended to the
provenance log as a ``run-start`` entry ahead of the run's write entries, so
every write chains back to the exact configuration that produced it. An
auditor can recompute the digests to check that a log segment corresponds to a
given input batch and recipe, and to detect a swapped input file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from constituent_reconciler import __version__
from constituent_reconciler.config import Recipe
from constituent_reconciler.schema import versions

MANIFEST_FILENAME = "run_manifest.json"

_CHUNK_SIZE = 64 * 1024


def file_digest(path: Path) -> str:
    """BLAKE2b-256 over the file's bytes, streamed so large inputs stay cheap."""

    digest = hashlib.blake2b(digest_size=32)
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _splink_version() -> str | None:
    try:
        return metadata.version("splink")
    except metadata.PackageNotFoundError:
        return None


def _input_digests(paths: Iterable[Path]) -> dict[str, str]:
    """Digest each input file. A directory contributes every regular file in it.

    Keys are file names; a directory's children are keyed ``dirname/childname``
    so two sources with the same file name stay distinct. Values are hex
    digests. No field value enters the manifest, only hashes.
    """

    digests: dict[str, str] = {}
    for path in paths:
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.is_file():
                    digests[f"{path.name}/{child.name}"] = file_digest(child)
        elif path.is_file():
            digests[path.name] = file_digest(path)
    return digests


def build_manifest(
    recipe_path: Path,
    input_paths: Iterable[Path],
    recipe: Recipe,
) -> dict[str, object]:
    """Assemble the reproducibility manifest for one run."""

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "recipe_hash": file_digest(recipe_path),
        "input_hashes": _input_digests(input_paths),
        "package_version": __version__,
        "splink_version": _splink_version(),
        "policy_pack": recipe.policy_pack,
        "thresholds": {
            "prior": recipe.prior,
            "auto": recipe.auto_threshold,
            "review": recipe.review_threshold,
        },
        "schema_versions": versions(),
    }


def manifest_hash(manifest: dict[str, object]) -> str:
    """BLAKE2b-256 over a canonical JSON encoding of the manifest."""

    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2b(canonical, digest_size=32).hexdigest()


def write_manifest(manifest: dict[str, object], out_dir: Path) -> Path:
    """Write the manifest to ``out_dir/run_manifest.json`` and return the path."""

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
