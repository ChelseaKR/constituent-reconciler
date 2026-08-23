"""Retention-policy destruction of PII-bearing output artifacts.

The out directory accumulates files that carry constituent field values: the
resolved records, the review queue, the withheld list, the CRM import
files, and the stage cache's entry files (extracted and normalized field
values, stage_cache.py). The HUD comparable-database guidance cited in the
research roadmap expects individual records to be routinely destroyed once
they are no longer
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

import contextlib
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from constituent_reconciler.provenance import ProvenanceLog
from constituent_reconciler.stage_cache import CACHE_DIR_NAME, EXTRACT_STAGE, NORMALIZE_STAGE

# The known PII-bearing artifacts the pipeline writes into the out directory.
# An explicit list, not a glob: destruction must never reach past what the
# pipeline is known to have written (decisions.json and compare_decisions.json
# carry ids and verdicts only, aggregate_summary.json is non-identifying by
# construction, run_manifest.json and compare_manifest.json carry file digests
# and configuration only, migration_summary.json carries counts only, and the
# provenance log is the evidence of destruction itself). The cutover artifacts
# come from ``reconcile compare`` and ``reconcile compare-apply`` (compare.py,
# compare_apply.py) and hold field values, ids, or both, so they sort with the
# record-bearing files. corrections.json holds reviewer-supplied replacement
# values (review/session.py), which are field values, so it is listed too.
# The stage cache is the one directory-shaped PII artifact; its files are
# inventoried by ``_cache_entries`` below, bounded to the cache root the
# pipeline wrote and to the exact entry shape the cache writes.
# repair_plan.json is the split-repair plan ``reconcile plan-split`` writes:
# it holds raw field values for the members of one written cluster, so it is
# destroyed here and the provenance log keeps only its digest. Its sibling
# repair_receipts.json, written by ``reconcile apply-repair`` only on a real
# (non-dry-run) apply, holds the before/after raw values each operation
# actually changed, so it is listed for the same reason; the provenance log
# again keeps only each operation's receipt digest. repair_approvals.json is
# deliberately absent here: it carries reviewer names, verdicts, and
# timestamps only, the same content class as decisions.json's own audit
# section, which this list has never covered.
# ai_ocr_proposals.json is written by ``reconcile ai-propose-corrections``:
# it holds real raw field values, proposed values, and verbatim quoted source
# text, so it is destroyed here under the same retention policies.
PII_ARTIFACTS: tuple[str, ...] = (
    "resolved.csv",
    "review_queue.csv",
    "withheld.csv",
    "corrections.json",
    "salesforce_import.csv",
    "civicrm_import.csv",
    "cutover_report.csv",
    "cutover_review.csv",
    "target_corrections.csv",
    "cutover_withheld.csv",
    "repair_plan.json",
    "repair_receipts.json",
    "ai_ocr_proposals.json",
)

PROVENANCE_FILENAME = "provenance.jsonl"

_WINDOW_PATTERN = re.compile(r"^(\d+)([dh])$")

# The exact on-disk shape of a stage-cache entry: a 32-hex content key with a
# .json suffix, directly inside one of the two stage directories. The walk in
# ``_cache_entries`` and the refusal in ``_require_cache_shape`` both hold to
# this shape, so destruction can never name a file the cache did not write.
_CACHE_STAGES = frozenset({EXTRACT_STAGE, NORMALIZE_STAGE})
_CACHE_ENTRY_PATTERN = re.compile(r"^[0-9a-f]{32}\.json$")


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


def _cache_roots(out_dir: Path, cache_dir: Path | None) -> list[Path]:
    """The cache directories one destruction pass covers.

    The ``stage_cache`` directory under the out root is always covered, since
    it is where the cache lives unless a recipe names another boundary. A
    recipe's explicit ``[cache] dir`` boundary is covered when the caller
    passes it (the ``--cache-dir`` option on ``reconcile destroy``).
    """

    default_root = out_dir / CACHE_DIR_NAME
    roots = [default_root]
    if cache_dir is not None and cache_dir.resolve() != default_root.resolve():
        roots.append(cache_dir)
    return roots


def _is_cache_entry(root: Path, path: Path) -> bool:
    """Whether ``path`` sits exactly at ``root/<stage>/<32-hex>.json``."""

    return (
        path.parent.parent == root
        and path.parent.name in _CACHE_STAGES
        and _CACHE_ENTRY_PATTERN.fullmatch(path.name) is not None
    )


def _require_cache_shape(root: Path) -> None:
    """Refuse an operator-supplied cache directory that is not a stage cache.

    A stage cache contains at most two stage directories, ``extract`` and
    ``normalize``, and inside them nothing but ``<32-hex>.json`` entry
    files. Anything else under ``root`` means the operator pointed
    ``--cache-dir`` somewhere it must not reach (the out directory, for
    example), so the whole pass is refused before anything is deleted. A
    directory that does not exist is fine: there is nothing to destroy in
    it.
    """

    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            if path.parent == root and path.name in _CACHE_STAGES:
                continue
        elif _is_cache_entry(root, path):
            continue
        raise ValueError(
            f"refusing --cache-dir {root}: {path.relative_to(root)} is not a "
            f"stage-cache entry (a stage cache holds only {EXTRACT_STAGE}/ and "
            f"{NORMALIZE_STAGE}/ directories of 32-hex .json files); check the "
            "path, nothing was deleted"
        )


def _cache_entries(root: Path, cutoff: float) -> list[Path]:
    """Every cache entry file under ``root`` at or older than the cutoff.

    The stage cache holds extracted and normalized field values, so its
    files are PII artifacts the same as ``resolved.csv``; unlike the flat
    artifact list they live in a directory tree the pipeline owns outright.
    Only files with the exact entry shape (``_is_cache_entry``) are listed,
    so the walk can never put a foreign file on the destruction list even
    when one has been placed inside a cache root.
    """

    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and _is_cache_entry(root, path) and path.stat().st_mtime <= cutoff
    )


def _artifact_name(path: Path, out_dir: Path, cache_dir: Path | None) -> str:
    """The content-free name a destruction certificate records for ``path``.

    Cache entries are named relative to their cache root under the
    ``stage_cache/`` prefix (entry filenames are hex digests, so the name
    carries no content and no operator path); everything else is its bare
    filename, as before.
    """

    for root in _cache_roots(out_dir, cache_dir):
        if path.is_relative_to(root):
            return f"{CACHE_DIR_NAME}/{path.relative_to(root).as_posix()}"
    return path.name


def _prune_empty_dirs(root: Path) -> None:
    """Remove now-empty directories under (and including) a cache root."""

    if not root.is_dir():
        return
    for child in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir():
            with contextlib.suppress(OSError):
                child.rmdir()
    with contextlib.suppress(OSError):
        root.rmdir()


def inventory(out_dir: Path, older_than: timedelta, *, cache_dir: Path | None = None) -> list[Path]:
    """List the PII-bearing artifacts in ``out_dir`` older than the window.

    Only filenames on the explicit ``PII_ARTIFACTS`` list are candidates,
    plus every stage-cache entry file under the covered cache roots (see
    ``_cache_roots``); a file qualifies when it exists and its mtime is at or
    before the cutoff. An explicitly passed ``cache_dir`` must have the
    stage-cache shape or the whole call is refused (``ValueError``), since a
    mistyped boundary must not put foreign files on a destruction list. Each
    path is listed at most once, and the provenance log is structurally
    excluded because it is not on the list and never matches the cache entry
    shape.
    """

    if cache_dir is not None:
        _require_cache_shape(cache_dir)
    cutoff = time.time() - older_than.total_seconds()
    candidates: list[Path] = []
    seen: set[Path] = set()
    for name in PII_ARTIFACTS:
        path = out_dir / name
        if path.is_file() and path.stat().st_mtime <= cutoff:
            candidates.append(path)
            seen.add(path.resolve())
    for root in _cache_roots(out_dir, cache_dir):
        for path in _cache_entries(root, cutoff):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                candidates.append(path)
    return candidates


def destroy(
    out_dir: Path,
    older_than: timedelta,
    *,
    policy: str,
    log: ProvenanceLog,
    dry_run: bool = False,
    cache_dir: Path | None = None,
) -> DestructionSummary:
    """Delete eligible artifacts, appending one destruction certificate each.

    For each candidate the SHA-256 and size of the file bytes are computed
    before deletion, the file is unlinked, and a ``destroyed`` entry is
    appended to ``log`` whose payload carries the artifact name, hash, size,
    and the policy text; the hash is also stored readably as the entry's
    ``external_id``. A dry run returns the candidate list and neither deletes
    nor logs. The provenance log itself is refused, fail-closed, even if a
    future edit to ``PII_ARTIFACTS`` were to name it; that refusal, like the
    cache-shape refusal in ``inventory``, is a pre-flight check, so it can
    never interrupt a pass after partial destruction.

    ``cache_dir`` extends the pass to an explicitly configured stage-cache
    boundary outside the out root, refused whole unless the directory has
    the stage-cache shape; the default cache location under the out root is
    covered on every pass. Cache directories left empty by the deletions are
    pruned.
    """

    candidates = inventory(out_dir, older_than, cache_dir=cache_dir)
    if any(path.name == PROVENANCE_FILENAME for path in candidates):
        raise ValueError(
            "refusing to destroy the provenance log: it is the evidence of destruction"
        )
    names = tuple(_artifact_name(path, out_dir, cache_dir) for path in candidates)
    if dry_run:
        return DestructionSummary(policy=policy, dry_run=True, candidates=names, destroyed=())
    destroyed: list[DestroyedArtifact] = []
    for path in candidates:
        name = _artifact_name(path, out_dir, cache_dir)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        path.unlink()
        log.append(
            action="destroyed",
            record_id=name,
            members=[],
            consent=True,
            payload={
                "artifact": name,
                "sha256": digest,
                "size": str(len(data)),
                "policy": policy,
            },
            external_id=f"sha256:{digest}",
        )
        destroyed.append(DestroyedArtifact(name=name, sha256=digest, size=len(data)))
    for root in _cache_roots(out_dir, cache_dir):
        _prune_empty_dirs(root)
    return DestructionSummary(
        policy=policy, dry_run=False, candidates=names, destroyed=tuple(destroyed)
    )
