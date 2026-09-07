"""Split one review queue across several reviewers, then put it back together.

A volunteer-run queue has three reviewers and four hundred pairs, and this
project's offline posture already assumes files travel by USB or shared drive
rather than a multi-user server. Until now there was one decisions file and one
reviewer at a time, so two-person review was sequential on that file and
reviewer throughput was the practical ceiling on adoption.

``review --shard 2/3`` presents only the pairs whose stable pair id hashes into
shard 2 and writes ``decisions-2of3.json``. ``merge-decisions`` combines the
shard files into one, keeping every reviewer attribution.

THE THING THAT MAKES SHARDING DANGEROUS, and what stops it.

Shards are disjoint by construction, so a pair appears in exactly one of them.
That is the point, and it is also the risk: under a pack requiring two distinct
approvers, a merger that simply unioned three files could satisfy the
two-approver rule with **one** human, by reading one "approved" from one shard
and another from a second. Three guards, all fail-closed:

1. **A pair recorded in the wrong shard file is refused.** Every merged pair's
   shard is recomputed from its own id. A pair that does not belong to the file
   it was found in means the sharding was bypassed, and its verdicts cannot be
   trusted to be one reviewer's independent work.
2. **Conflicting verdicts are refused**, named, for a supervisor. Two people
   deciding one pair differently is a finding, not something to resolve by
   picking a side.
3. **The merged file records which shards it covers**, with each source file's
   digest, and ``apply`` refuses a merged file whose sources do not cover every
   shard. Without this, a file assembled from two of three shards reports a
   queue as fully reviewed, which is an absence rendered as completeness.

Assignment is BLAKE2b of the canonical pair id modulo n, so it is a pure
function of the pair: stable across resumes, across machines, and across
reviewers, with no coordination and no state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from constituent_reconciler.schema import DECISIONS_SCHEMA_VERSION

#: The ``audit`` sibling a merged decisions file carries: the shard count it
#: claims to cover and one entry per source file, each with its digest. Read by
#: ``apply`` before a merged file is trusted.
SOURCES_KEY = "sources"
SHARD_KEY = "shard"

APPROVED = "approved"
REJECTED = "rejected"


class ShardError(ValueError):
    """A shard specification, a shard file, or a merge was refused, fail-closed."""


@dataclass(frozen=True)
class Shard:
    """One shard of a review queue: ``index`` of ``count``, 1-based."""

    index: int
    count: int

    def __str__(self) -> str:
        return f"{self.index}/{self.count}"

    @property
    def filename(self) -> str:
        return f"decisions-{self.index}of{self.count}.json"


def parse_shard(spec: str) -> Shard:
    """``"2/3"`` -> ``Shard(2, 3)``, or a refusal naming what is wrong.

    A shard index outside ``1..count`` is refused rather than clamped: a
    reviewer who typed ``4/3`` would otherwise be handed shard 3's pairs and
    believe they had reviewed a fourth of the queue that does not exist.
    """

    text = spec.strip()
    if text.count("/") != 1:
        raise ShardError(f"--shard must look like 2/3; got {spec!r}")
    left, right = (part.strip() for part in text.split("/"))
    try:
        index, count = int(left), int(right)
    except ValueError as error:
        raise ShardError(f"--shard must be two integers separated by /; got {spec!r}") from error
    if count < 1:
        raise ShardError(f"--shard needs at least one shard; got {spec!r}")
    if not 1 <= index <= count:
        raise ShardError(
            f"--shard index must be between 1 and {count}; got {index} in {spec!r}. "
            f"A queue split {count} ways has no shard {index}."
        )
    return Shard(index=index, count=count)


def pair_id(left: str, right: str) -> str:
    """The canonical, order-independent id of a pair.

    Sorted so ``(a, b)`` and ``(b, a)`` land in the same shard. Every hash below
    is taken over this string and nothing else, so assignment does not depend on
    queue order, probability, or which run produced the pair.
    """

    return "|".join(sorted((left, right)))


def shard_of(left: str, right: str, count: int) -> int:
    """Which 1-based shard of ``count`` this pair belongs to.

    BLAKE2b-256 of the canonical pair id, modulo ``count``. A pure function: the
    same pair lands in the same shard on every machine, on every resume, and
    without any reviewer knowing what the others were given.
    """

    if count < 1:
        raise ShardError(f"shard count must be at least 1; got {count}")
    digest = hashlib.blake2b(pair_id(left, right).encode("utf-8"), digest_size=32).digest()
    return int.from_bytes(digest, "big") % count + 1


def in_shard(left: str, right: str, shard: Shard) -> bool:
    return shard_of(left, right, shard.count) == shard.index


def file_digest(path: Path) -> str:
    """BLAKE2b-256 over a shard file's exact bytes, recorded in the merge."""

    return hashlib.blake2b(path.read_bytes(), digest_size=32).hexdigest()


@dataclass(frozen=True)
class MergedDecisions:
    """The result of one merge. Ids and counts only."""

    payload: dict[str, object]
    shard_count: int
    covered: tuple[int, ...]
    pairs: int
    sources: tuple[tuple[str, str], ...]


def _load(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ShardError(f"shard decisions file not found: {path}")
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ShardError(f"shard decisions file could not be read ({path}): {error}") from error
    if not isinstance(data, dict):
        raise ShardError(f"shard decisions file must be a JSON object: {path}")
    return {str(key): value for key, value in data.items()}


def _declared_shard(data: dict[str, object], path: Path) -> Shard:
    """The shard a file says it is, or a refusal.

    A shard file that does not declare its own identity cannot be checked for
    coverage or for misplaced pairs, and merging it would produce a file
    claiming a completeness nothing verified.
    """

    raw = data.get(SHARD_KEY)
    if not isinstance(raw, dict):
        raise ShardError(
            f"{path} carries no {SHARD_KEY!r} section, so it does not say which shard it "
            "is. Only a file written by `review --shard` can be merged; a whole-queue "
            "decisions file is already complete and needs no merge."
        )
    index, count = raw.get("index"), raw.get("count")
    if not isinstance(index, int) or not isinstance(count, int) or isinstance(index, bool):
        raise ShardError(f"{path} declares a malformed shard ({raw!r})")
    if not 1 <= index <= count:
        raise ShardError(f"{path} declares shard {index} of {count}, which cannot exist")
    return Shard(index=index, count=count)


def _verdict_pairs(data: dict[str, object], verdict: str) -> list[tuple[str, str]]:
    raw = data.get(verdict, [])
    if not isinstance(raw, list):
        raise ShardError(f"the {verdict!r} section is not a list")
    return [
        (str(entry[0]), str(entry[1]))
        for entry in raw
        if isinstance(entry, list) and len(entry) == 2
    ]


@dataclass
class _Accumulator:
    """Mutable state shared across the shard files being merged."""

    shard_count: int
    seen_shards: dict[int, Path]
    verdicts: dict[str, tuple[str, Path, tuple[str, str]]]
    audit: dict[str, list[dict[str, str]]]
    conflicts: list[str]
    misplaced: list[str]


def _collect(path: Path, data: dict[str, object], shard: Shard, acc: _Accumulator) -> None:
    """Fold one shard file into the accumulator, recording every disagreement."""

    if shard.index in acc.seen_shards:
        raise ShardError(
            f"{path} and {acc.seen_shards[shard.index]} both claim shard {shard.index} of "
            f"{acc.shard_count}. Merging them would count one reviewer's work twice."
        )
    acc.seen_shards[shard.index] = path
    for verdict in (APPROVED, REJECTED):
        try:
            pairs = _verdict_pairs(data, verdict)
        except ShardError as error:
            raise ShardError(f"{path}: {error}") from error
        for left, right in pairs:
            key = pair_id(left, right)
            if not in_shard(left, right, shard):
                acc.misplaced.append(
                    f"{key} is in {path.name} but hashes to shard "
                    f"{shard_of(left, right, acc.shard_count)} of {acc.shard_count}"
                )
                continue
            previous = acc.verdicts.get(key)
            if previous is not None and previous[0] != verdict:
                acc.conflicts.append(
                    f"{key}: {previous[1].name} says {previous[0]}, {path.name} says {verdict}"
                )
            acc.verdicts[key] = (verdict, path, (min(left, right), max(left, right)))
    raw_audit = data.get("audit")
    if isinstance(raw_audit, dict):
        for key_text, entries in raw_audit.items():
            if isinstance(entries, list):
                acc.audit.setdefault(str(key_text), []).extend(
                    entry for entry in entries if isinstance(entry, dict)
                )


def _shard_count_of(loaded: list[tuple[Path, dict[str, object], Shard]]) -> int:
    counts = {shard.count for _, _, shard in loaded}
    if len(counts) > 1:
        raise ShardError(
            "these files were produced by different splits of the queue "
            f"({sorted(counts)} shards); pairs assigned under one split are not the "
            "pairs assigned under another, so merging them would leave some reviewed "
            "twice and others not at all"
        )
    return counts.pop()


def merge_decisions(paths: list[Path]) -> MergedDecisions:
    """Combine shard decisions files into one whole-queue decisions payload.

    Refuses, fail-closed, on: a file that is missing, unreadable, or declares no
    shard; shard files that disagree about how many shards there are; two files
    claiming the same shard; a pair recorded in a file it does not hash into;
    and any pair two files decided differently.

    Coverage is recorded, not required. A merge of two of three shards is a
    legitimate intermediate artifact -- the third reviewer has not finished --
    and it is ``apply`` that must refuse to act on it. Recording which shards
    are present, with each source's digest, is what lets ``apply`` tell a
    complete merge from a partial one.
    """

    if not paths:
        raise ShardError("merge-decisions needs at least one shard file")

    loaded = [
        (path, data, _declared_shard(data, path)) for path in paths for data in (_load(path),)
    ]
    acc = _Accumulator(
        shard_count=_shard_count_of(loaded),
        seen_shards={},
        verdicts={},
        audit={},
        conflicts=[],
        misplaced=[],
    )
    for path, data, shard in loaded:
        _collect(path, data, shard, acc)

    if acc.misplaced:
        raise ShardError(
            f"{len(acc.misplaced)} pair(s) were recorded in the wrong shard file, so the "
            "sharding was bypassed and these verdicts cannot be treated as one "
            "reviewer's independent work: " + "; ".join(sorted(acc.misplaced)[:5])
        )
    if acc.conflicts:
        raise ShardError(
            f"{len(acc.conflicts)} pair(s) were decided differently by different reviewers. "
            "A supervisor decides these; this tool will not pick a side: "
            + "; ".join(sorted(acc.conflicts))
        )

    approved = sorted(list(e[2]) for e in acc.verdicts.values() if e[0] == APPROVED)
    rejected = sorted(list(e[2]) for e in acc.verdicts.values() if e[0] == REJECTED)
    sources = tuple(
        (acc.seen_shards[index].name, file_digest(acc.seen_shards[index]))
        for index in sorted(acc.seen_shards)
    )
    payload: dict[str, object] = {
        "decisions_schema": DECISIONS_SCHEMA_VERSION,
        "approved": approved,
        "rejected": rejected,
        "audit": {
            key: sorted(entries, key=lambda e: json.dumps(e, sort_keys=True))
            for key, entries in acc.audit.items()
        },
        SOURCES_KEY: {
            "shard_count": acc.shard_count,
            "covered": sorted(acc.seen_shards),
            "files": [{"name": name, "digest": digest} for name, digest in sources],
        },
    }
    return MergedDecisions(
        payload=payload,
        shard_count=acc.shard_count,
        covered=tuple(sorted(acc.seen_shards)),
        pairs=len(acc.verdicts),
        sources=sources,
    )


def incomplete_merge(data: dict[str, object]) -> str | None:
    """Why this decisions file is a partial merge, or ``None`` when it is not one.

    Returns ``None`` for a file with no ``sources`` section at all: that is a
    whole-queue decisions file, which was never sharded and is complete by
    construction. Only a file that *says* it was assembled from shards is held
    to covering all of them -- and a file that says so while missing one is the
    exact shape ``apply`` must refuse, because it reports a queue as fully
    reviewed when a third of it was never opened.
    """

    raw = data.get(SOURCES_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return f"the {SOURCES_KEY!r} section is not an object, so its coverage cannot be read"
    count = raw.get("shard_count")
    covered = raw.get("covered")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return f"the {SOURCES_KEY!r} section declares a malformed shard count ({count!r})"
    if not isinstance(covered, list):
        return f"the {SOURCES_KEY!r} section declares a malformed covered list ({covered!r})"
    present = {item for item in covered if isinstance(item, int) and not isinstance(item, bool)}
    missing = sorted(set(range(1, count + 1)) - present)
    if missing:
        named = ", ".join(str(index) for index in missing)
        return (
            f"it was merged from {len(present)} of {count} shards; shard(s) {named} are "
            "missing. Every pair in a missing shard is unreviewed, and applying this file "
            "would treat the queue as finished when it is not."
        )
    return None
