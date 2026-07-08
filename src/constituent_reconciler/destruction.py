"""Retention-policy destruction of PII-bearing output artifacts.

The out directory accumulates files that carry constituent field values: the
resolved records, the review queue, the withheld list, and the CRM import
files. The HUD comparable-database guidance cited in the research roadmap
expects individual records to be routinely destroyed once they are no longer
needed. This module executes that destruction and proves it happened: each
deleted file gets one ``destroyed`` entry in the provenance chain naming the
artifact, its SHA-256, its size, and the retention policy applied, so the log
shows what was destroyed and when without retaining any content.

Two limitations are deliberate and documented rather than papered over. No
retention window ships as a default, because how long records may live is a
decision for the adopting organization and its counsel; callers must state
the window explicitly. And deleting a file is not forensic erasure: on
journaling filesystems and SSDs the bytes can persist until overwritten, so
full-disk encryption of the machine remains the compensating control this
tool cannot provide.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from constituent_reconciler.provenance import ProvenanceLog

# The known PII-bearing artifacts the pipeline writes into the out directory.
# An explicit list, not a glob: destruction must never reach past what the
# pipeline is known to have written (decisions.json carries ids and verdicts
# only, aggregate_summary.json is non-identifying by construction, and the
# provenance log is the evidence of destruction itself).
PII_ARTIFACTS: tuple[str, ...] = (
    "resolved.csv",
    "review_queue.csv",
    "withheld.csv",
    "salesforce_import.csv",
    "civicrm_import.csv",
)

PROVENANCE_FILENAME = "provenance.jsonl"

_WINDOW_PATTERN = re.compile(r"^(\d+)([dh])$")


def parse_retention(text: str) -> timedelta:
    """Parse a retention window like ``30d`` or ``12h`` into a timedelta.

    ``0d`` is valid and means every listed artifact regardless of age. Any
    other form raises ``ValueError``. There is no default window; the caller
    must always state one.
    """

    match = _WINDOW_PATTERN.match(text.strip())
    if match is None:
        raise ValueError(
            f"invalid retention window {text!r}: use a whole number of days or "
            "hours, for example 30d or 12h"
        )
    value = int(match.group(1))
    return timedelta(days=value) if match.group(2) == "d" else timedelta(hours=value)


@dataclass(frozen=True)
class DestroyedArtifact:
    """One destruction certificate: the artifact and its pre-deletion hash."""

    name: str
    sha256: str
    size: int


@dataclass(frozen=True)
class DestructionSummary:
    """What a destruction pass found and, unless it was a dry run, did."""

    policy: str
    dry_run: bool
    candidates: tuple[str, ...]
    destroyed: tuple[DestroyedArtifact, ...]


def inventory(out_dir: Path, older_than: timedelta) -> list[Path]:
    """List the PII-bearing artifacts in ``out_dir`` older than the window.

    Only filenames on the explicit ``PII_ARTIFACTS`` list are candidates; a
    file qualifies when it exists and its mtime is at or before the cutoff.
    The provenance log is structurally excluded because it is not on the list.
    """

    cutoff = time.time() - older_than.total_seconds()
    candidates: list[Path] = []
    for name in PII_ARTIFACTS:
        path = out_dir / name
        if path.is_file() and path.stat().st_mtime <= cutoff:
            candidates.append(path)
    return candidates


def destroy(
    out_dir: Path,
    older_than: timedelta,
    *,
    policy: str,
    log: ProvenanceLog,
    dry_run: bool = False,
) -> DestructionSummary:
    """Delete eligible artifacts, appending one destruction certificate each.

    For each candidate the SHA-256 and size of the file bytes are computed
    before deletion, the file is unlinked, and a ``destroyed`` entry is
    appended to ``log`` whose payload carries the artifact name, hash, size,
    and the policy text; the hash is also stored readably as the entry's
    ``external_id``. A dry run returns the candidate list and neither deletes
    nor logs. The provenance log itself is refused, fail-closed, even if a
    future edit to ``PII_ARTIFACTS`` were to name it.
    """

    candidates = inventory(out_dir, older_than)
    names = tuple(path.name for path in candidates)
    if dry_run:
        return DestructionSummary(
            policy=policy, dry_run=True, candidates=names, destroyed=()
        )
    destroyed: list[DestroyedArtifact] = []
    for path in candidates:
        if path.name == PROVENANCE_FILENAME:
            raise ValueError(
                "refusing to destroy the provenance log: it is the evidence of destruction"
            )
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        path.unlink()
        log.append(
            action="destroyed",
            record_id=path.name,
            members=[],
            consent=True,
            payload={
                "artifact": path.name,
                "sha256": digest,
                "size": str(len(data)),
                "policy": policy,
            },
            external_id=f"sha256:{digest}",
        )
        destroyed.append(DestroyedArtifact(name=path.name, sha256=digest, size=len(data)))
    return DestructionSummary(
        policy=policy, dry_run=False, candidates=names, destroyed=tuple(destroyed)
    )
