"""Integrity checks for the public GenAI telemetry projection."""

from __future__ import annotations

import hashlib
from pathlib import Path

PROJECTION_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "constituent_reconciler"
    / "_vendor"
    / "genai_telemetry"
)


def test_projection_uses_public_label_not_vcs_object_id() -> None:
    version = (PROJECTION_DIR / ".standards-version").read_text(encoding="utf-8").strip()
    assert version == "genai-telemetry-v1.1.0-public-projection"


def test_projection_manifest_binds_every_public_file() -> None:
    manifest_path = PROJECTION_DIR / ".standards-manifest.sha256"
    entries: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        digest, filename = line.split("  ", maxsplit=1)
        assert len(digest) == 64
        assert filename not in entries
        entries[filename] = digest

    projected_files = {
        path.name
        for path in PROJECTION_DIR.iterdir()
        if path.is_file() and path.name not in {manifest_path.name, ".standards-version"}
    }
    assert set(entries) == projected_files

    for filename, expected_digest in entries.items():
        actual_digest = hashlib.sha256((PROJECTION_DIR / filename).read_bytes()).hexdigest()
        assert actual_digest == expected_digest
