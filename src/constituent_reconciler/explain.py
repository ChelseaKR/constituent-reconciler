"""An auditor's trace for one resolved record (``constituent-reconcile explain``).

A funder's auditor, or a data subject's advocate, asks one question: *why is
this one record, and who decided that?* The answer already exists, spread
across five artifacts a run writes -- ``resolved.csv`` names the members,
``auto_merges.json`` says at what probability and in which band the matcher
joined them, ``decisions.json`` says which human approved the ones a human saw,
``corrections.json`` says what a reviewer fixed, and ``provenance.jsonl`` says
what was written under which consent, chained back to the manifest that names
the recipe and inputs. Answering the question meant reading all five by hand
and holding them in your head at once.

This composes them. ``ai-explain`` narrates one pair through a hosted model;
this is its deterministic, offline counterpart. It calls no model, re-scores
nothing, and adds no decision path: every number here is a byte the run already
wrote.

THREE THINGS THIS MODULE REFUSES TO DO.

**It never renders an absence as a fact.** "No human reviewed this cluster" and
"this run has no ``decisions.json`` at all" are different findings and reach the
output as different sentences. So do "the reviewer recorded no correction" and
"``corrections.json`` is missing". A trace that quietly omits a section an
auditor was looking for is worse than no trace, because it reads as evidence
that nothing happened. Every optional part of the trace is an
:class:`Absent` carrying the reason, never an empty string or an empty list
standing in for one.

**A redacted trace never loads a field value in the first place.** The
shareable rendering is not the full rendering with values stripped on the way
out -- ``_values`` returns an empty mapping at read time, so no field value ever
enters the structure the redacted renderer walks. Stripping on output is one
forgotten branch away from a leak; not reading is not.

**``--verify`` re-derives, it does not restate.** It recomputes the cited
entry's own hash from its body and compares, walks the whole chain, and
recomputes the manifest's hash from ``run_manifest.json`` to check it against
the ``run-start`` entry that opens the segment. A verifier that echoed the
stored hash back would agree with a tampered log about everything.

Two renderings, both available as Markdown or JSON:

* **full** -- ids, bands, probabilities, hashes, reasons, *and* the mapped field
  values. Local, PII, and on ``destruction.PII_ARTIFACTS`` when written.
* **redacted** -- everything except the field values. Shareable with an auditor
  who is entitled to know how a decision was made but not to hold the data.

``--write`` writes into the run's own output directory and nowhere else, by
construction rather than by check, which is what the ``dv`` pack requires of the
full rendering. Standard output stays local to the operator's own terminal, the
same posture the review UI already takes toward the values it shows.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from constituent_reconciler import manifest as manifest_module
from constituent_reconciler import provenance as provenance_module
from constituent_reconciler.schema import EXPLAIN_TRACE_SCHEMA_VERSION

MANIFEST_FILE = "run_manifest.json"
RESOLVED_FILE = "resolved.csv"
REVIEW_QUEUE_FILE = "review_queue.csv"
AUTO_MERGES_FILE = "auto_merges.json"
DECISIONS_FILE = "decisions.json"
CORRECTIONS_FILE = "corrections.json"
WITHHELD_FILE = "withheld.csv"
PROVENANCE_FILE = "provenance.jsonl"

#: The full rendering. Carries mapped field values, so its written form is a
#: local PII artifact.
FULL = "full"
#: The shareable rendering. Ids, bands, probabilities, hashes, reasons and
#: reviewer names; no mapped field value is ever read into it.
REDACTED = "redacted"
MODES: tuple[str, ...] = (FULL, REDACTED)

EXPLAIN_TRACE_MD_FILENAME = "explain_trace.md"
EXPLAIN_TRACE_JSON_FILENAME = "explain_trace.json"
EXPLAIN_REDACTED_MD_FILENAME = "explain_trace_redacted.md"
EXPLAIN_REDACTED_JSON_FILENAME = "explain_trace_redacted.json"

#: ``resolved.csv`` columns that describe the cluster rather than hold one of
#: the recipe's mapped field values. Every other column in that file is a golden
#: record's field value and is read only in :data:`FULL` mode.
RESOLVED_STRUCTURAL_COLUMNS: frozenset[str] = frozenset(
    {"cluster_id", "primary", "members", "consent"}
)

#: ``review_queue.csv`` columns that are structure. The per-field columns are
#: ``<field>_left`` / ``<field>_right`` (values) and ``<field>_left_span`` /
#: ``<field>_right_span`` (document locations). A span names a file, a page and
#: a box -- never the value found there -- so spans survive redaction and values
#: do not.
REVIEW_STRUCTURAL_COLUMNS: frozenset[str] = frozenset(
    {"left", "right", "probability", "left_source", "right_source", "note"}
)


class ExplainError(ValueError):
    """The trace could not be built, fail-closed.

    Every raise happens before any trace bytes exist, so a refused explanation
    leaves nothing behind for someone to read as an answer.
    """


@dataclass(frozen=True)
class Absent:
    """A part of the trace that is not present, and why it is not present.

    The reason is the whole point. This portfolio's most common defect is an
    absence published as a measurement, and a trace is exactly where that costs
    the most: an auditor reading a blank "reviewed by" line concludes nobody
    reviewed it, when the truth may be that the decisions file was destroyed.
    """

    reason: str

    def __str__(self) -> str:
        return f"not recorded ({self.reason})"


@dataclass(frozen=True)
class Member:
    """One record in the cluster.

    ``values`` is empty in :data:`REDACTED` mode because it was never read, not
    because the record had no values; ``values_read`` records which it was, so
    the renderer can say "withheld from this rendering" rather than leaving a
    reader to guess.
    """

    record_id: str
    source: str
    values: dict[str, str] = field(default_factory=dict)
    spans: dict[str, str] = field(default_factory=dict)
    values_read: bool = False


@dataclass(frozen=True)
class Edge:
    """One pairwise edge inside the cluster, with how it was decided.

    ``band`` is ``auto`` for an edge the matcher merged on its own and
    ``review`` for one a person saw. ``verdict`` and ``reviews`` are
    :class:`Absent` for an auto edge -- nobody decided it, which is a fact about
    the edge and not a gap in the record.

    An edge carries no field values, deliberately. It would have nowhere to get
    them: a pair still in ``review_queue.csv`` has not been merged, so its two
    records are not in one cluster and this edge does not exist; once ``apply``
    approves it the pair is re-banded to AUTO and leaves the queue. The two
    states never overlap, so a values field here would be a branch no input
    could reach. The reviewed values a member does have are attached to that
    member instead, where they are reachable.
    """

    left: str
    right: str
    probability: float | Absent
    band: str
    note: str
    verdict: str | Absent
    reviews: tuple[dict[str, str], ...] | Absent


@dataclass(frozen=True)
class ProvenanceRef:
    """The write entry for this cluster, and the chain it sits in."""

    seq: int
    action: str
    entry_hash: str
    prev_hash: str
    content_hash: str
    external_id: str | None
    consent: bool | None
    fill_policy: str
    field_sources: dict[str, str]
    time: str


@dataclass(frozen=True)
class Verification:
    """What ``--verify`` re-derived, and whether each derivation agreed.

    Three independent checks, reported separately because they fail for
    different reasons and need different follow-up.
    """

    chain_ok: bool
    chain_message: str
    entry_hash_ok: bool | Absent
    manifest_hash_ok: bool | Absent
    manifest_message: str

    @property
    def ok(self) -> bool:
        """True only when all three checks ran *and* agreed.

        A check that could not run is not a check that passed. ``--verify``
        answers "is this trace backed by evidence that still holds", and a
        missing provenance log cannot answer yes to it. So an :class:`Absent`
        outcome makes this False, and the renderer prints which of the three it
        was so nobody reads an unverifiable trace as a broken one.
        """

        return self.chain_ok and self.entry_hash_ok is True and self.manifest_hash_ok is True


@dataclass(frozen=True)
class Trace:
    """One cluster's whole story, composed from the run's own artifacts."""

    mode: str
    out_dir: Path
    cluster_id: str
    asked_for: str
    members: tuple[Member, ...]
    consent: str | Absent
    withheld_reason: str | Absent
    edges: tuple[Edge, ...]
    corrections: tuple[dict[str, str], ...] | Absent
    provenance: ProvenanceRef | Absent
    manifest: dict[str, object]
    verification: Verification | None = None

    @property
    def redacted(self) -> bool:
        return self.mode == REDACTED


# --------------------------------------------------------------------------
# reading the run's artifacts
# --------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, object]:
    """One JSON artifact, or a refusal naming the file and the failure mode.

    The three modes stay distinct because they need different fixes: never
    written, truncated, or the wrong shape.
    """

    if not path.is_file():
        raise ExplainError(
            f"{path} does not exist. A trace cannot be built without it, and an "
            "empty trace is a different answer than a trace that could not be built."
        )
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExplainError(f"{path} could not be read: {error}") from error
    if not isinstance(data, dict):
        raise ExplainError(f"{path} must be a JSON object")
    return {str(key): value for key, value in data.items()}


def _values(mode: str, mapping: dict[str, str]) -> dict[str, str]:
    """Field values for this rendering, read only in :data:`FULL` mode.

    The redacted trace's guarantee lives here and nowhere else: it returns an
    empty mapping, so no field value is ever placed in the structure the
    redacted renderer walks. Every later stage is then free of the question.
    """

    if mode == REDACTED:
        return {}
    return {key: value for key, value in mapping.items() if value != ""}


@dataclass(frozen=True)
class _ResolvedRow:
    """One line of ``resolved.csv``, with values split from structure."""

    primary: str
    members: tuple[str, ...]
    consent: str
    values: dict[str, str]


def _read_resolved(out_dir: Path, mode: str) -> dict[str, _ResolvedRow]:
    """Cluster id -> its row of ``resolved.csv``, values split from structure.

    A dry run writes no ``resolved.csv``. That is a real and explainable state,
    but it is not an empty cluster set: treating it as one would report every
    cluster as unknown, which is the same sentence a genuine typo produces.
    """

    path = out_dir / RESOLVED_FILE
    if not path.is_file():
        raise ExplainError(
            f"{path} does not exist; the run may have been a --dry-run, or the output "
            "directory may have been destroyed. Either way the cluster cannot be "
            "traced, which is not the same as its not existing."
        )
    clusters: dict[str, _ResolvedRow] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            cluster_id = (row.get("cluster_id") or "").strip()
            if not cluster_id:
                continue
            raw_values = {
                key: (value or "")
                for key, value in row.items()
                if key and key not in RESOLVED_STRUCTURAL_COLUMNS
            }
            clusters[cluster_id] = _ResolvedRow(
                primary=(row.get("primary") or "").strip(),
                members=tuple(m for m in (row.get("members") or "").split("|") if m),
                consent=(row.get("consent") or "").strip(),
                values=_values(mode, raw_values),
            )
    return clusters


def _read_auto_merges(out_dir: Path) -> tuple[dict[frozenset[str], dict[str, object]], bool]:
    """Pair -> the probability and band that decided it, and whether the file exists.

    ``auto_merges.json`` arrived after the first releases, so an older output
    directory legitimately lacks it. The boolean travels with the mapping so the
    caller can say "this run recorded no auto-merge evidence" rather than
    reporting a probability of nothing.
    """

    path = out_dir / AUTO_MERGES_FILE
    if not path.is_file():
        return {}, False
    data = _read_json(path)
    raw = data.get("pairs")
    pairs: dict[frozenset[str], dict[str, object]] = {}
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            left, right = str(entry.get("left", "")), str(entry.get("right", ""))
            if not left or not right:
                continue
            pairs[frozenset((left, right))] = {
                "probability": entry.get("probability"),
                "band": str(entry.get("band", "auto")),
                "note": str(entry.get("note", "")),
            }
    return pairs, True


def _read_review_queue(
    out_dir: Path, mode: str
) -> tuple[dict[frozenset[str], dict[str, object]], bool]:
    """Pair -> its review-queue row, values split from spans and structure.

    An empty queue is written as a header-only file, so a missing file means the
    artifact is gone rather than that no pair needed review. The two are told
    apart by the returned boolean.
    """

    path = out_dir / REVIEW_QUEUE_FILE
    if not path.is_file():
        return {}, False
    pairs: dict[frozenset[str], dict[str, object]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            left, right = (row.get("left") or "").strip(), (row.get("right") or "").strip()
            if not left or not right:
                continue
            raw_values: dict[str, str] = {}
            spans: dict[str, str] = {}
            for key, value in row.items():
                if not key or key in REVIEW_STRUCTURAL_COLUMNS:
                    continue
                if key.endswith("_span"):
                    if value:
                        spans[key] = value
                elif value:
                    raw_values[key] = value
            pairs[frozenset((left, right))] = {
                "probability": row.get("probability"),
                "band": "review",
                "note": (row.get("note") or ""),
                "values": _values(mode, raw_values),
                "spans": spans,
            }
    return pairs, True


def _read_decisions(out_dir: Path) -> tuple[dict[frozenset[str], dict[str, object]], bool]:
    """Pair -> its verdict and every recorded review, and whether the file exists.

    A run with no ``decisions.json`` and a run whose reviewer decided nothing
    are different findings, and they stay apart all the way to the rendering.
    """

    path = out_dir / DECISIONS_FILE
    if not path.is_file():
        return {}, False
    data = _read_json(path)
    out: dict[frozenset[str], dict[str, object]] = {}
    for verdict in ("approved", "rejected"):
        raw = data.get(verdict, [])
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if isinstance(entry, list) and len(entry) == 2:
                out[frozenset((str(entry[0]), str(entry[1])))] = {"verdict": verdict}
    audit = data.get("audit")
    if isinstance(audit, dict):
        for key, entries in audit.items():
            parts = str(key).split("|")
            if len(parts) != 2:
                continue
            pair = frozenset(parts)
            reviews = tuple(
                {
                    "reviewer": str(item.get("reviewer", "")),
                    "verdict": str(item.get("verdict", "")),
                    "decided_at": str(item.get("decided_at", "")),
                }
                for item in entries
                if isinstance(item, dict)
            )
            record = out.setdefault(pair, {})
            record["reviews"] = reviews
            # A pair present only in `audit` was seen and held (a lone approval
            # awaiting its second reviewer). It has reviews and no verdict, and
            # saying so is the point: "awaiting" is not "approved".
            record.setdefault("verdict", "awaiting_second_reviewer")
    return out, True


def _read_corrections(
    out_dir: Path, cluster_members: frozenset[str], mode: str
) -> tuple[dict[str, str], ...] | Absent:
    """The reviewer corrections that touched this cluster's members."""

    path = out_dir / CORRECTIONS_FILE
    if not path.is_file():
        return Absent(f"this run has no {CORRECTIONS_FILE}")
    data = _read_json(path)
    raw = data.get("corrections")
    if not isinstance(raw, list):
        return Absent(f"{CORRECTIONS_FILE} carries no corrections list")
    rows: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        left, right = str(entry.get("left", "")), str(entry.get("right", ""))
        side = str(entry.get("side", ""))
        record_id = left if side == "left" else right
        if record_id not in cluster_members:
            continue
        row = {
            "record_id": record_id,
            "field": str(entry.get("field", "")),
            "reviewer": str(entry.get("reviewer", "")),
            "corrected_at": str(entry.get("corrected_at", "")),
        }
        # The corrected value is a mapped field value like any other: the
        # reviewer typed the person's real name into it.
        row.update(_values(mode, {"value": str(entry.get("value", ""))}))
        rows.append(row)
    return tuple(rows)


def _read_withheld(out_dir: Path, cluster_id: str) -> str | Absent:
    """Why this cluster's write was withheld, if it was."""

    path = out_dir / WITHHELD_FILE
    if not path.is_file():
        return Absent(f"this run has no {WITHHELD_FILE}")
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row.get("cluster_id") or "").strip() == cluster_id:
                return (row.get("reason") or "").strip()
    return Absent("this cluster is not on the withheld list")


def _read_provenance(out_dir: Path) -> list[dict[str, object]]:
    """Every provenance entry, in log order."""

    path = out_dir / PROVENANCE_FILE
    if not path.is_file():
        return []
    entries: list[dict[str, object]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if isinstance(entry, dict):
                    entries.append(entry)
    except (OSError, json.JSONDecodeError) as error:
        raise ExplainError(f"{path} could not be read: {error}") from error
    return entries


# --------------------------------------------------------------------------
# building the trace
# --------------------------------------------------------------------------


def _member_source(record_id: str) -> str:
    """The source a member came from, read off its own id (``existing:E003``)."""

    return record_id.split(":", 1)[0] if ":" in record_id else ""


def _pairs_within(members: tuple[str, ...]) -> list[tuple[str, str]]:
    """Every unordered pair of members, id-ordered and stable."""

    ordered = sorted(members)
    return [(a, b) for i, a in enumerate(ordered) for b in ordered[i + 1 :]]


def _resolve_cluster_id(
    clusters: dict[str, _ResolvedRow], *, cluster: str | None, record: str | None
) -> tuple[str, str]:
    """The cluster to trace and how it was asked for, or a refusal naming it."""

    if cluster is not None:
        if cluster not in clusters:
            raise ExplainError(
                f"no cluster {cluster!r} in {RESOLVED_FILE}. "
                f"{len(clusters)} cluster(s) are present; an unknown id is refused "
                "rather than traced as an empty cluster."
            )
        return cluster, f"cluster {cluster}"
    if record is None:
        raise ExplainError("one of --cluster or --record is required")
    for cluster_id, row in clusters.items():
        if record in row.members:
            return cluster_id, f"record {record}"
    raise ExplainError(
        f"no record {record!r} in any cluster in {RESOLVED_FILE}. "
        "An unknown id is refused rather than traced as an empty cluster."
    )


def _build_member(
    member_id: str,
    row: _ResolvedRow,
    review_pairs: dict[frozenset[str], dict[str, object]],
    mode: str,
) -> Member:
    """One member, with the values and document spans the run actually kept.

    The golden record's values belong to the cluster, not to one member, so they
    are attached to the primary -- the record that was written. A member's own
    values survive only in ``review_queue.csv``, and only for the pairs a person
    saw; a member merged automatically has none anywhere, which the renderer
    reports rather than filling in from the golden record.
    """

    spans: dict[str, str] = {}
    reviewed: dict[str, str] = {}
    for pair_key, review in review_pairs.items():
        if member_id not in pair_key:
            continue
        # review_queue.csv writes the id-lower member of a pair as `left`.
        side = "left" if member_id == min(pair_key) else "right"
        raw_spans = review.get("spans")
        if isinstance(raw_spans, dict):
            suffix = f"_{side}_span"
            for key, span in raw_spans.items():
                if key.endswith(suffix):
                    spans[key[: -len(suffix)]] = str(span)
        raw_values = review.get("values")
        if isinstance(raw_values, dict):
            suffix = f"_{side}"
            for key, value in raw_values.items():
                if key.endswith(suffix):
                    reviewed[key[: -len(suffix)]] = str(value)
    # The golden record's values belong to the surviving record; a member that
    # is not the survivor has its own values only where a reviewer was shown
    # them, which is review_queue.csv. Both are mapped field values and both are
    # gated by `_values` at read time, so neither reaches a redacted trace.
    values = dict(row.values) if member_id == row.primary else {}
    values.update(reviewed)
    return Member(
        record_id=member_id,
        source=_member_source(member_id),
        values=values,
        spans=spans,
        values_read=mode == FULL,
    )


def _edge_probability(source: dict[str, object] | None) -> tuple[float | Absent, str, str]:
    """The probability, band and note behind one edge, or why there is none."""

    if source is None:
        # Members can share a cluster transitively without a direct scored edge
        # (A-B and B-C merge A and C). Saying so beats inventing a probability
        # for a comparison that never happened.
        return (
            Absent("no direct scored edge; these two joined transitively through another member"),
            "transitive",
            "",
        )
    raw = source.get("probability")
    try:
        probability: float | Absent = float(str(raw))
    except (TypeError, ValueError):
        # Not 0.0. Zero is the strongest possible statement that two records are
        # different people; an unreadable probability is not a low one.
        probability = Absent(f"unreadable probability {raw!r}")
    return probability, str(source.get("band", "")), str(source.get("note", ""))


def _edge_decision(
    decision: dict[str, object] | None, decisions_present: bool
) -> tuple[str | Absent, tuple[dict[str, str], ...] | Absent]:
    """Who decided this edge, or which kind of silence stands in its place."""

    if decision is None:
        reason = (
            "no reviewer decided this pair"
            if decisions_present
            else f"this run has no {DECISIONS_FILE}"
        )
        return Absent(reason), Absent(reason)
    raw_reviews = decision.get("reviews")
    reviews: tuple[dict[str, str], ...] | Absent = (
        raw_reviews
        if isinstance(raw_reviews, tuple)
        else Absent("the decisions file records no audit entry for this pair")
    )
    return str(decision.get("verdict", "")), reviews


def _build_edge(
    left: str,
    right: str,
    auto_pairs: dict[frozenset[str], dict[str, object]],
    review_pairs: dict[frozenset[str], dict[str, object]],
    decisions: dict[frozenset[str], dict[str, object]],
    decisions_present: bool,
) -> Edge:
    """One pairwise edge: how it scored, who decided it, what a reviewer saw."""

    key = frozenset((left, right))
    probability, band, note = _edge_probability(auto_pairs.get(key) or review_pairs.get(key))
    verdict, reviews = _edge_decision(decisions.get(key), decisions_present)
    return Edge(
        left=left,
        right=right,
        probability=probability,
        band=band,
        note=note,
        verdict=verdict,
        reviews=reviews,
    )


def _verify(
    out_dir: Path, entries: list[dict[str, object]], reference: ProvenanceRef | Absent
) -> Verification:
    """Re-derive the chain, the cited entry's hash, and the manifest hash.

    Nothing here is restated from the log. The entry hash is recomputed from the
    entry's own body, and the manifest hash is recomputed from
    ``run_manifest.json``'s bytes, so a log edited to be internally consistent
    still fails against the manifest it claims to describe.
    """

    chain_ok, chain_message = provenance_module.verify_log(out_dir / PROVENANCE_FILE)

    entry_hash_ok: bool | Absent
    if isinstance(reference, Absent):
        entry_hash_ok = Absent("no write entry names this cluster")
    else:
        cited = next((e for e in entries if e.get("seq") == reference.seq), None)
        if cited is None:
            entry_hash_ok = Absent(f"no entry at seq {reference.seq}")
        else:
            # Recomputed from the entry's own body. The stored hash is the thing
            # under test, so it is never an input to the derivation.
            entry_hash_ok = provenance_module.entry_hash(cited) == cited.get("entry_hash")

    manifest_hash_ok: bool | Absent
    manifest_message = ""
    run_start = next(
        (e for e in entries if e.get("action") == provenance_module.RUN_START_ACTION), None
    )
    manifest_path = out_dir / MANIFEST_FILE
    if run_start is None:
        manifest_hash_ok = Absent("the log opens with no run-start entry")
    elif not manifest_path.is_file():
        manifest_hash_ok = Absent(f"this run has no {MANIFEST_FILE} to recompute")
    else:
        stored = _read_json(manifest_path)
        recomputed_manifest = manifest_module.manifest_hash(stored)
        manifest_hash_ok = recomputed_manifest == run_start.get("content_hash")
        manifest_message = (
            f"recomputed {recomputed_manifest}; run-start carries {run_start.get('content_hash')}"
        )
    return Verification(
        chain_ok=chain_ok,
        chain_message=chain_message,
        entry_hash_ok=entry_hash_ok,
        manifest_hash_ok=manifest_hash_ok,
        manifest_message=manifest_message,
    )


def explain(
    out_dir: Path,
    *,
    cluster: str | None = None,
    record: str | None = None,
    mode: str = FULL,
    verify: bool = False,
) -> Trace:
    """Compose one cluster's trace from the artifacts in ``out_dir``.

    Raises :class:`ExplainError` for an unknown id, a missing ``resolved.csv``,
    or an unreadable artifact -- never returns an empty trace to stand in for
    one of those.
    """

    if mode not in MODES:
        raise ExplainError(f"unknown rendering mode {mode!r}; expected one of {', '.join(MODES)}")
    out_dir = Path(out_dir)
    clusters = _read_resolved(out_dir, mode)
    cluster_id, asked_for = _resolve_cluster_id(clusters, cluster=cluster, record=record)
    row = clusters[cluster_id]

    auto_pairs, auto_present = _read_auto_merges(out_dir)
    review_pairs, review_present = _read_review_queue(out_dir, mode)
    decisions, decisions_present = _read_decisions(out_dir)

    member_rows = [_build_member(member_id, row, review_pairs, mode) for member_id in row.members]
    edges = [
        _build_edge(left, right, auto_pairs, review_pairs, decisions, decisions_present)
        for left, right in _pairs_within(row.members)
    ]
    if not auto_present and not review_present:
        # Both evidence files gone: every edge above would read "transitive",
        # which would be a claim about the matcher rather than about the run.
        raise ExplainError(
            f"{out_dir} holds neither {AUTO_MERGES_FILE} nor {REVIEW_QUEUE_FILE}, so no "
            "edge in this cluster can be explained. Reporting every edge as transitive "
            "would state something about the matcher that these bytes do not support."
        )

    entries = _read_provenance(out_dir)
    reference: ProvenanceRef | Absent = Absent(
        f"no entry in {PROVENANCE_FILE} names this cluster"
        if entries
        else f"this run has no {PROVENANCE_FILE}"
    )
    for entry in entries:
        if str(entry.get("record_id", "")) != cluster_id:
            continue
        raw_sources = entry.get("field_sources")
        raw_seq = entry.get("seq")
        raw_consent = entry.get("consent")
        reference = ProvenanceRef(
            seq=raw_seq if isinstance(raw_seq, int) else -1,
            action=str(entry.get("action", "")),
            entry_hash=str(entry.get("entry_hash", "")),
            prev_hash=str(entry.get("prev_hash", "")),
            content_hash=str(entry.get("content_hash", "")),
            external_id=(
                None if entry.get("external_id") is None else str(entry.get("external_id"))
            ),
            # A run-start entry carries a null consent by design; so does any
            # entry whose consent this reader cannot make sense of. Both mean
            # "the log states no consent here", never "consent was false".
            consent=raw_consent if isinstance(raw_consent, bool) else None,
            fill_policy=str(entry.get("fill_policy", "")),
            field_sources=(
                {str(k): str(v) for k, v in raw_sources.items()}
                if isinstance(raw_sources, dict)
                else {}
            ),
            time=str(entry.get("time", "")),
        )
        break

    manifest_path = out_dir / MANIFEST_FILE
    manifest = _read_json(manifest_path) if manifest_path.is_file() else {}

    return Trace(
        mode=mode,
        out_dir=out_dir,
        cluster_id=cluster_id,
        asked_for=asked_for,
        members=tuple(member_rows),
        consent=row.consent if row.consent else Absent("resolved.csv records no consent"),
        withheld_reason=_read_withheld(out_dir, cluster_id),
        edges=tuple(edges),
        corrections=_read_corrections(out_dir, frozenset(row.members), mode),
        provenance=reference,
        manifest=manifest,
        verification=_verify(out_dir, entries, reference) if verify else None,
    )


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _absent_or(value: object) -> object:
    return str(value) if isinstance(value, Absent) else value


def trace_payload(trace: Trace) -> dict[str, object]:
    """The trace as JSON. An :class:`Absent` renders as its own sentence."""

    payload: dict[str, object] = {
        "schema_version": EXPLAIN_TRACE_SCHEMA_VERSION,
        "rendering": trace.mode,
        "asked_for": trace.asked_for,
        "cluster_id": trace.cluster_id,
        "consent": _absent_or(trace.consent),
        "withheld_reason": _absent_or(trace.withheld_reason),
        "members": [
            {
                "record_id": member.record_id,
                "source": member.source,
                "spans": member.spans,
                **(
                    {"values": member.values}
                    if member.values_read
                    else {"values": "withheld from the redacted rendering"}
                ),
            }
            for member in trace.members
        ],
        "edges": [
            {
                "left": edge.left,
                "right": edge.right,
                "probability": _absent_or(edge.probability),
                "band": edge.band,
                "note": edge.note,
                "verdict": _absent_or(edge.verdict),
                "reviews": _absent_or(edge.reviews),
            }
            for edge in trace.edges
        ],
        "corrections": _absent_or(trace.corrections),
        "manifest": {
            "recipe_hash": trace.manifest.get("recipe_hash"),
            "policy_pack": trace.manifest.get("policy_pack"),
            "thresholds": trace.manifest.get("thresholds"),
            "package_version": trace.manifest.get("package_version"),
            "input_hashes": trace.manifest.get("input_hashes"),
        },
    }
    if isinstance(trace.provenance, Absent):
        payload["provenance"] = str(trace.provenance)
    else:
        reference = trace.provenance
        payload["provenance"] = {
            "seq": reference.seq,
            "action": reference.action,
            "entry_hash": reference.entry_hash,
            "prev_hash": reference.prev_hash,
            "content_hash": reference.content_hash,
            "external_id": reference.external_id,
            "consent": reference.consent,
            "fill_policy": reference.fill_policy,
            "field_sources": reference.field_sources,
            "time": reference.time,
        }
    if trace.verification is not None:
        verification = trace.verification
        payload["verification"] = {
            "chain_ok": verification.chain_ok,
            "chain": verification.chain_message,
            "entry_hash_ok": _absent_or(verification.entry_hash_ok),
            "manifest_hash_ok": _absent_or(verification.manifest_hash_ok),
            "manifest": verification.manifest_message,
        }
    return payload


def _render_members(trace: Trace) -> list[str]:
    lines = ["## Members", ""]
    for member in trace.members:
        lines.append(f"- `{member.record_id}` (source: {member.source or 'unknown'})")
        for name in sorted(member.spans):
            lines.append(f"  - {name} found at `{member.spans[name]}`")
        if not member.values_read:
            lines.append("  - field values: withheld from the redacted rendering")
            continue
        for name in sorted(member.values):
            lines.append(f"  - {name}: {member.values[name]}")
    return lines


def _render_edges(trace: Trace) -> list[str]:
    lines = ["", "## How the members were joined", ""]
    for edge in trace.edges:
        probability = (
            f"{edge.probability:.4f}"
            if isinstance(edge.probability, float)
            else str(edge.probability)
        )
        lines.append(f"- `{edge.left}` + `{edge.right}` — {edge.band} band, p = {probability}")
        if edge.note:
            lines.append(f"  - note: {edge.note}")
        if isinstance(edge.reviews, Absent):
            lines.append(f"  - decided by: {edge.reviews}")
        else:
            for review in edge.reviews:
                lines.append(
                    f"  - {review['verdict']} by {review['reviewer']} at {review['decided_at']}"
                )
    return lines


def _render_corrections(trace: Trace) -> list[str]:
    lines = ["", "## Corrections", ""]
    if isinstance(trace.corrections, Absent):
        lines.append(f"- {trace.corrections}")
        return lines
    if not trace.corrections:
        lines.append("- none: the corrections file exists and records none for this cluster")
        return lines
    for correction in trace.corrections:
        detail = f" -> {correction['value']}" if "value" in correction else ""
        lines.append(
            f"- `{correction['record_id']}`.{correction['field']}{detail} "
            f"by {correction['reviewer']} at {correction['corrected_at']}"
        )
    return lines


def _render_provenance(trace: Trace) -> list[str]:
    lines = ["", "## What was written, and under which recipe", ""]
    if isinstance(trace.provenance, Absent):
        lines.append(f"- {trace.provenance}")
    else:
        reference = trace.provenance
        lines.append(f"- entry seq {reference.seq} ({reference.action}) at {reference.time}")
        lines.append(f"- entry hash: `{reference.entry_hash}`")
        lines.append(f"- chained to: `{reference.prev_hash}`")
        lines.append(f"- content hash: `{reference.content_hash}`")
        lines.append(f"- external id: {reference.external_id or 'none'}")
        lines.append(f"- consent recorded on the entry: {reference.consent}")
        lines.append(f"- fill policy: {reference.fill_policy or 'none'}")
        for name in sorted(reference.field_sources):
            lines.append(f"  - {name} came from `{reference.field_sources[name]}`")
    lines.append(f"- recipe hash: `{trace.manifest.get('recipe_hash')}`")
    lines.append(f"- policy pack: {trace.manifest.get('policy_pack')}")
    lines.append(f"- thresholds: {trace.manifest.get('thresholds')}")
    return lines


def _render_verification(verification: Verification) -> list[str]:
    chain = "intact" if verification.chain_ok else "BROKEN"
    entry = _absent_or(verification.entry_hash_ok)
    manifest = _absent_or(verification.manifest_hash_ok)
    lines = [
        "",
        "## Verification",
        "",
        f"- chain: {chain} — {verification.chain_message}",
        f"- cited entry's own hash recomputes: {entry}",
        f"- manifest hash matches the run-start entry: {manifest}",
    ]
    if verification.manifest_message:
        lines.append(f"  - {verification.manifest_message}")
    return lines


def render_trace(trace: Trace) -> str:
    """The trace as Markdown, for a person to read or attach to a finding."""

    caveat = (
        "  \n_The redacted rendering never reads a mapped field value; the values are "
        "absent from this document rather than removed from it._"
        if trace.redacted
        else ""
    )
    lines: list[str] = [
        f"# Trace for {trace.cluster_id}",
        "",
        f"Asked for: {trace.asked_for} · rendering: **{trace.mode}**{caveat}",
        "",
    ]
    lines += _render_members(trace)
    lines += _render_edges(trace)
    lines += ["", "## Consent at write time", ""]
    lines.append(f"- consent: {trace.consent}")
    lines.append(f"- withheld: {trace.withheld_reason}")
    lines += _render_corrections(trace)
    lines += _render_provenance(trace)
    if trace.verification is not None:
        lines += _render_verification(trace.verification)
    return "\n".join(lines) + "\n"


def trace_filename(trace: Trace, output_format: str) -> str:
    """The fixed filename this rendering is written under.

    Fixed, not derived from the cluster id, so ``destruction`` can classify all
    four names and ``tests/test_destruction_inventory.py`` can see them.
    """

    if trace.redacted:
        return (
            EXPLAIN_REDACTED_JSON_FILENAME
            if output_format == "json"
            else EXPLAIN_REDACTED_MD_FILENAME
        )
    return EXPLAIN_TRACE_JSON_FILENAME if output_format == "json" else EXPLAIN_TRACE_MD_FILENAME


def write_trace(trace: Trace, output_format: str) -> Path:
    """Write the rendering into the run's own output directory, and nowhere else.

    The path is built from ``trace.out_dir`` by construction, so the full
    rendering cannot land outside the directory ``destroy`` sweeps -- which is
    what the ``dv`` pack requires of it, enforced by there being no parameter
    that could say otherwise.
    """

    body = (
        json.dumps(trace_payload(trace), indent=2, sort_keys=True) + "\n"
        if output_format == "json"
        else render_trace(trace)
    )
    trace.out_dir.mkdir(parents=True, exist_ok=True)
    path = trace.out_dir / trace_filename(trace, output_format)
    path.write_text(body, encoding="utf-8")
    return path
