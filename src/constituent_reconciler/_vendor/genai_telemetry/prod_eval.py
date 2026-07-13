"""Minimal production-eval loop — low-traffic profile (metrics plan Phase 2.3).

The research finding this closes: 93% of LLM-agent evaluation is pre-deployment
only, and only ~29% of teams feed eval results back into improvements
("checkpoint-only" is the named anti-pattern). This is the smallest loop that
isn't checkpoint-only, sized for apps with little traffic.

Design (framework-agnostic — you pass callables, this owns the loop and the
feedback routing):
  1. SAMPLE 100%. At low traffic, sampling percentages exist to control cost at
     scale you don't have — score everything and batch it weekly.
  2. JUDGE each trace with the repo's ALREADY-CALIBRATED judge (the §3 judge:
     agreement >=0.80, kappa >=0.60, freshness <=30d). Not a new judge.
  3. ROUTE every failure to exactly one of: a new benchmark case
     (tests/eval/benchmark/*.jsonl), a prompt/threshold change, or a dated
     waiver. A failure that changes nothing is the anti-pattern.

This module does steps 1 and 3 and the bookkeeping; you supply the judge (2),
the trace source, and an explicit de-identification boundary for durable cases.
Raw production prompts, outputs, and user metadata are never copied by default.
Local-only repos declare N/A (there is no production traffic).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

MAX_TRACE_ID_LENGTH = 128
MAX_BENCHMARK_CASE_BYTES = 65_536
_ALLOWED_CASE_FIELDS = frozenset(
    {"deidentified", "input", "expected", "metadata", "tags"}
)
_REQUIRED_CASE_FIELDS = frozenset({"deidentified", "input", "expected"})


@dataclass
class Verdict:
    passed: bool
    score: float | None = None
    reason: str = ""


@dataclass
class EvalResult:
    scored: int = 0
    failed: int = 0
    new_benchmark_cases: int = 0
    waived: int = 0
    score_distribution: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)


def run_batch(
    traces: Iterable[dict],
    judge: Callable[[dict], Verdict],
    *,
    benchmark_path: Path,
    waived_trace_ids: set[str] | None = None,
    trace_id_key: str = "trace_id",
    case_builder: Callable[[dict, Verdict], dict] | None = None,
    benchmark_case_key: str = "benchmark_case",
) -> EvalResult:
    """Score a batch of production traces and route failures.

    - `traces`: production trace dicts with a unique, opaque, non-PII trace id.
      Raw trace content remains in the source system and is never persisted by
      this module.
    - `judge`: your calibrated judge. Returns a Verdict. This module never judges.
    - Failures NOT in `waived_trace_ids` are appended to `benchmark_path`
      (JSONL) so they become permanent regression cases. Supply `case_builder`
      as the explicit sanitizer, or put an already-deidentified case under
      `benchmark_case_key`. Cases must contain `deidentified=True`, `input`,
      and `expected`; only the documented minimal fields are accepted.
    - Existing JSONL trace ids are not appended again, making retries idempotent.

    Growing the benchmark from real failures is the low-traffic substitute for
    A/B testing (which is powerless at this traffic).
    """
    trace_list = list(traces)
    trace_ids = _validate_batch_trace_ids(trace_list, trace_id_key)
    waived = {
        _validate_trace_id(tid, "waived trace id")
        for tid in (waived_trace_ids or set())
    }
    existing = _existing_trace_ids(benchmark_path)
    res = EvalResult()
    buckets: dict[str, int] = {}
    new_cases = []
    for t, tid in zip(trace_list, trace_ids, strict=True):
        v = judge(t)
        _validate_verdict(v, tid)
        res.scored += 1
        bucket = "pass" if v.passed else "fail"
        if v.score is not None:
            bucket = _score_bucket(v.score)
        buckets[bucket] = buckets.get(bucket, 0) + 1
        if v.passed:
            continue
        res.failed += 1
        if tid in waived:
            res.waived += 1
            continue
        res.failures.append({"trace_id": tid, "reason": v.reason, "score": v.score})
        if tid in existing:
            continue
        raw_case = (
            case_builder(t, v)
            if case_builder is not None
            else t.get(benchmark_case_key)
        )
        case = _validate_benchmark_case(raw_case, tid)
        new_cases.append({"source": "prod-eval", "trace_id": tid, **case})
        existing.add(tid)
    if new_cases:
        _append_jsonl(benchmark_path, new_cases)
        res.new_benchmark_cases = len(new_cases)
    res.score_distribution = buckets
    return res


def _score_bucket(score: float) -> str:
    for lo in (0.9, 0.8, 0.7, 0.6, 0.5):
        if score >= lo:
            return f">={lo}"
    return "<0.5"


def _validate_trace_id(value: object, context: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{context} must be a string")
    if not value or value != value.strip() or len(value) > MAX_TRACE_ID_LENGTH:
        raise ValueError(
            f"{context} must be nonempty, trimmed, and at most {MAX_TRACE_ID_LENGTH} characters"
        )
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError(f"{context} must not contain whitespace or control characters")
    return value


def _validate_batch_trace_ids(traces: list[dict], trace_id_key: str) -> list[str]:
    if type(trace_id_key) is not str or not trace_id_key:
        raise ValueError("trace_id_key must be a nonempty string")
    ids: list[str] = []
    seen: set[str] = set()
    for index, trace in enumerate(traces):
        if type(trace) is not dict:
            raise ValueError(f"trace {index} must be a dict")
        tid = _validate_trace_id(trace.get(trace_id_key), f"trace {index} id")
        if tid in seen:
            raise ValueError(f"duplicate trace id in batch: {tid}")
        seen.add(tid)
        ids.append(tid)
    return ids


def _validate_verdict(verdict: Verdict, trace_id: str) -> None:
    if not isinstance(verdict, Verdict):
        raise ValueError(f"judge returned a non-Verdict for trace {trace_id}")
    if type(verdict.passed) is not bool:
        raise ValueError(f"judge passed flag must be bool for trace {trace_id}")
    if type(verdict.reason) is not str:
        raise ValueError(f"judge reason must be a string for trace {trace_id}")
    if verdict.score is None:
        return
    if type(verdict.score) not in (int, float):
        raise ValueError(f"judge score must be numeric for trace {trace_id}")
    if not math.isfinite(verdict.score) or not 0 <= verdict.score <= 1:
        raise ValueError(
            f"judge score must be finite and within [0, 1] for trace {trace_id}"
        )


def _validate_benchmark_case(value: object, trace_id: str) -> dict:
    if type(value) is not dict:
        raise ValueError(
            f"failure {trace_id} requires case_builder or an already-deidentified benchmark case"
        )
    missing = _REQUIRED_CASE_FIELDS - value.keys()
    extra = value.keys() - _ALLOWED_CASE_FIELDS
    if missing or extra:
        raise ValueError(
            f"failure {trace_id} benchmark case has invalid fields; "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    if value["deidentified"] is not True:
        raise ValueError(
            f"failure {trace_id} benchmark case must assert deidentified=True"
        )
    if value.get("metadata") is not None and type(value["metadata"]) is not dict:
        raise ValueError(f"failure {trace_id} benchmark metadata must be a dict")
    tags = value.get("tags")
    if tags is not None and (
        type(tags) is not list or any(type(tag) is not str for tag in tags)
    ):
        raise ValueError(f"failure {trace_id} benchmark tags must be a list of strings")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"failure {trace_id} benchmark case must be strict JSON"
        ) from exc
    if len(encoded.encode("utf-8")) > MAX_BENCHMARK_CASE_BYTES:
        raise ValueError(
            f"failure {trace_id} benchmark case exceeds {MAX_BENCHMARK_CASE_BYTES} bytes"
        )
    return dict(value)


def _existing_trace_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if type(row) is not dict:
                raise ValueError(
                    f"benchmark row at {path}:{line_number} must be an object"
                )
            tid = _validate_trace_id(
                row.get("trace_id"), f"benchmark trace id at {path}:{line_number}"
            )
            if tid in ids:
                raise ValueError(
                    f"duplicate benchmark trace id at {path}:{line_number}: {tid}"
                )
            ids.add(tid)
    return ids


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---- RAG corpus drift watch -------------------------------------------------
def corpus_drift(corpus_dir: Path, max_age_days: int = 90) -> dict:
    """Cheapest useful drift signal for a RAG corpus: per-source content hash +
    age, flagging sources older than `max_age_days`. Run before investing in
    embedding-drift-fancy — a stale source doc is the common, checkable failure.
    Returns a manifest; a caller diffs it against the prior run to see churn."""
    import datetime as dt
    import hashlib

    manifest = {}
    stale = []
    now = dt.datetime.now(dt.UTC).timestamp()
    for p in sorted(corpus_dir.rglob("*")):
        if not p.is_file() or any(
            part.startswith(".") for part in p.relative_to(corpus_dir).parts
        ):
            continue
        data = p.read_bytes()
        age_days = (now - p.stat().st_mtime) / 86400
        rel = str(p.relative_to(corpus_dir))
        manifest[rel] = {
            "sha256": hashlib.sha256(data).hexdigest()[:16],
            "bytes": len(data),
            "age_days": round(age_days, 1),
        }
        if age_days > max_age_days:
            stale.append(rel)
    return {"sources": len(manifest), "stale": stale, "manifest": manifest}
