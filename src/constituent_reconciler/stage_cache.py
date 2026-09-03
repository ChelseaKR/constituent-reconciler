"""Content-addressed cache for the deterministic pipeline stages (UC-01).

Organizations rerun the same large existing export beside each small incoming
batch. Extraction and normalization are deterministic functions of their
inputs, so their results can be reused; candidate generation, scoring,
banding, and clustering must never be, because term frequencies and new
cross-batch candidates change pair probabilities whenever the record
population changes. This module therefore caches exactly two stages, extract
and normalize, and nothing downstream of them.

Keys are content-addressed. Every key digests the input (a file digest for
extraction, a digest of the record's mapped raw values for normalization)
together with the declared recipe schema version, the active field mapping,
the package version, the installed version of the library that does the
stage's work (pdfplumber for PDF extraction, the ``postal`` package under
the libpostal address backend; the stdlib text reader and the vendored
address ruleset ship inside the package itself, which the package version
already pins), and the stage's own configuration. Any component change,
including a dependency upgrade the package pin does not capture, produces a
different key, so a stale entry is never matched, only orphaned. When a
backing library's installed version cannot be determined, the stage is not
cached at all; an unknown version is never guessed into a key.

The cache fails closed in both directions. An entry whose stored envelope
version, key, or payload shape does not match expectations is ignored and
recomputed, never coerced. Stage and key names are validated before they
become path segments, so no lookup can escape the cache root. Extraction is
cached only for readers whose output is a pure function of the file bytes and
the recorded versions: the stdlib text reader always qualifies, the plain
pdfplumber backend qualifies while pdfplumber's installed version is known,
and the OCR and model-seam backends never do (Tesseract's version is outside
the package's pin, and a seam-refined page is not a function of the file
alone). An extraction the sandbox killed against a resource limit is
returned fail-closed but never stored, because that result depends on the
machine's load rather than the file bytes; the document is re-parsed on the
next run.

``normalize_record`` stays pure; ``normalize_via_cache`` wraps it from the
outside and replays its failure accounting on a hit, so cached and uncached
runs produce identical ingest reports.

The cache directory holds normalized and extracted field values and is a PII
artifact: ``constituent-reconcile destroy`` covers it (destruction.py), and the retention
inventory in docs/DATA-FLOW-AND-RETENTION.md lists it. Its location is the
``stage_cache`` directory under the run's output root unless the recipe's
``[cache]`` section names another local directory, which config.py validates
fail-closed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Protocol, TypeGuard

from constituent_reconciler import __version__
from constituent_reconciler.config import Recipe
from constituent_reconciler.manifest import file_digest
from constituent_reconciler.models import CacheStats, Record, SourceSpan, TextSpan
from constituent_reconciler.normalize import normalize_record, normalized_keys
from constituent_reconciler.schema import CONFIG_SCHEMA_VERSION

# The cache directory's name under the output root, and the name destruction
# uses when it certifies a cache entry's deletion.
CACHE_DIR_NAME = "stage_cache"

# The on-disk entry envelope version. Bumped when the envelope or a payload
# shape changes; an entry written under any other version is ignored.
CACHE_ENTRY_VERSION = 1

EXTRACT_STAGE = "extract"
NORMALIZE_STAGE = "normalize"

# A key is the hex digest ``cache_key`` mints, and a stage is one of the two
# stage names above. Both are validated before becoming path segments, so a
# crafted value cannot address a file outside the cache root.
_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STAGES = frozenset({EXTRACT_STAGE, NORMALIZE_STAGE})

# One extracted row: the mapped raw values and their source spans, before any
# record id is minted. Ids are minted at replay time because the duplicate-row
# counter spans every file of a source, which a per-file cache entry cannot see.
Row = tuple[dict[str, str], dict[str, SourceSpan | TextSpan]]


@dataclass(frozen=True)
class ExtractedRows:
    """One document's extraction result, plus whether it may be stored.

    ``rows`` are the kept rows and ``pages_extracted``/``pages_dropped`` the
    page accounting, so a cache hit replays the ingest report exactly as a
    fresh parse would have filled it. ``cacheable`` is False when the parse
    did not run to completion, which today means the sandbox killed it
    against a wall-clock, CPU, or address-space limit. Such a result is
    correct to return fail-closed, but it reflects the machine's load rather
    than the file bytes, so storing it would freeze a transient failure
    under the file's content digest. A clean parse that keeps zero rows
    stays cacheable: it would compute the same emptiness again.
    """

    rows: list[Row]
    pages_extracted: int
    pages_dropped: int
    cacheable: bool = True


class StageCache(Protocol):
    """Storage for stage results, keyed by stage name and content-derived key."""

    def get(self, stage: str, key: str) -> dict[str, object] | None:
        """Return the stored payload, or ``None`` for any miss or mismatch."""
        ...

    def put(self, stage: str, key: str, payload: dict[str, object]) -> None:
        """Store ``payload`` under ``(stage, key)``, replacing any entry."""
        ...


class FilesystemStageCache:
    """Filesystem cache under one operator-selected local directory.

    Each entry is one JSON file at ``<root>/<stage>/<key>.json`` wrapping the
    payload in an envelope that repeats the entry version and the key. A read
    that finds a missing file, unparseable JSON, a foreign envelope version, a
    key that does not match the filename's, or a non-object payload returns
    ``None``: the caller recomputes, and the stale entry is never used.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _entry_path(self, stage: str, key: str) -> Path:
        if stage not in _STAGES:
            raise ValueError(f"unknown cache stage {stage!r}")
        if not _KEY_PATTERN.match(key):
            raise ValueError("cache key must be a 32-character lowercase hex digest")
        return self.root / stage / f"{key}.json"

    def get(self, stage: str, key: str) -> dict[str, object] | None:
        path = self._entry_path(stage, key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("entry_version") != CACHE_ENTRY_VERSION or data.get("key") != key:
            return None
        payload = data.get("payload")
        return payload if isinstance(payload, dict) else None

    def put(self, stage: str, key: str, payload: dict[str, object]) -> None:
        path = self._entry_path(stage, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {"entry_version": CACHE_ENTRY_VERSION, "key": key, "payload": payload}
        path.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8")


class CacheStatsCollector:
    """Mutable hit/miss counters, frozen into a ``models.CacheStats`` after a run."""

    def __init__(self) -> None:
        self._hits: dict[str, int] = {}
        self._misses: dict[str, int] = {}

    def hit(self, stage: str) -> None:
        self._hits[stage] = self._hits.get(stage, 0) + 1

    def miss(self, stage: str) -> None:
        self._misses[stage] = self._misses.get(stage, 0) + 1

    def freeze(self, *, enabled: bool) -> CacheStats:
        return CacheStats(enabled=enabled, hits=dict(self._hits), misses=dict(self._misses))


@dataclass
class ActiveCache:
    """A cache plus its per-run stats, handed through the pipeline as one value."""

    cache: StageCache
    stats: CacheStatsCollector = field(default_factory=CacheStatsCollector)


def resolve_cache_dir(recipe: Recipe, out_dir: Path) -> Path | None:
    """The cache root this recipe selects, or ``None`` when the cache is off.

    Absent an explicit ``[cache] dir``, the cache lives under the run's local
    output root, which is what keeps the default DV posture intact: every
    retained artifact of a run, the cache included, sits inside one directory
    the operator already controls and ``constituent-reconcile destroy`` already reaches.
    An explicit ``dir`` is the operator's own declared local retention
    boundary; config.py refuses non-local values at load time.
    """

    if not recipe.cache.enabled:
        return None
    if recipe.cache.dir is not None:
        return recipe.cache.dir
    return out_dir / CACHE_DIR_NAME


def for_recipe(recipe: Recipe, out_dir: Path) -> FilesystemStageCache | None:
    """Construct the filesystem cache the recipe configures, or ``None``."""

    root = resolve_cache_dir(recipe, out_dir)
    return FilesystemStageCache(root) if root is not None else None


def cache_key(components: dict[str, object]) -> str:
    """BLAKE2b-128 over a canonical JSON encoding of the key components."""

    canonical = json.dumps(components, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2b(canonical, digest_size=16).hexdigest()


def _raw_digest(raw: dict[str, str]) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2b(canonical, digest_size=16).hexdigest()


def _distribution_version(name: str) -> str | None:
    """The installed version of distribution ``name``, or ``None`` if unknown."""

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _extractor_version(reader: str) -> str | None:
    """The version component the extraction key records for ``reader``.

    Text and .eml parsing is stdlib-only, so the package version already in
    every key pins it. PDF parsing is pdfplumber's, which pyproject declares
    as a floating dependency (``pdfplumber>=0.11``); an upgrade without a
    package release changes the parser, so the installed pdfplumber version
    must enter the key itself. ``None`` means the version cannot be
    determined and extraction for this reader is not cacheable.
    """

    if reader == "text":
        return "stdlib"
    return _distribution_version("pdfplumber")


def _address_backend_version(backend: str) -> str | None:
    """The version component the normalize key records for ``backend``.

    The deterministic backend is the ruleset vendored in address.py, which
    the package version already in every key pins. The libpostal backend
    reaches an external C library through the ``postal`` package, whose
    install floats outside the package pin, so the installed ``postal``
    version must enter the key itself. ``None`` means the version cannot be
    determined and normalization under this backend is not cacheable.
    """

    if backend == "deterministic":
        return "vendored"
    if backend == "libpostal":
        return _distribution_version("postal")
    return None


def normalization_cacheable(recipe: Recipe) -> bool:
    """Whether normalization output under this recipe is safe to cache.

    False when the active address backend's installed version cannot be
    determined: without the version in the key, a backend upgrade would
    silently serve stale entries, so the cache is bypassed fail-closed.
    """

    return _address_backend_version(recipe.normalize.address_backend) is not None


def normalize_cache_key(raw: dict[str, str], recipe: Recipe) -> str:
    """Key for one record's normalization result.

    Digests the record's mapped raw values with everything normalization
    depends on, the address backend's installed version included, so editing
    one source row re-keys that row alone and every other row keeps its
    entry. Raises ``ValueError`` when the backend's version is unknown;
    callers must check ``normalization_cacheable`` first.
    """

    backend_version = _address_backend_version(recipe.normalize.address_backend)
    if backend_version is None:
        raise ValueError(
            f"cannot key normalization under address backend "
            f"{recipe.normalize.address_backend!r}: its installed version is unknown"
        )
    return cache_key(
        {
            "stage": NORMALIZE_STAGE,
            "config_schema": CONFIG_SCHEMA_VERSION,
            "package_version": __version__,
            "mapping": recipe.mapping,
            "fields": list(recipe.fields),
            "address_backend": recipe.normalize.address_backend,
            "address_backend_version": backend_version,
            "input_digest": _raw_digest(raw),
        }
    )


def extraction_cache_key(input_digest: str, reader: str, recipe: Recipe) -> str:
    """Key for one document's extraction result, given its file digest.

    The key carries the installed version of the parser that does the work
    (see ``_extractor_version``). Raises ``ValueError`` when that version is
    unknown; callers must check ``extraction_cacheable`` first.
    """

    extractor_version = _extractor_version(reader)
    if extractor_version is None:
        raise ValueError(
            f"cannot key extraction for reader {reader!r}: the installed parser version is unknown"
        )
    return cache_key(
        {
            "stage": EXTRACT_STAGE,
            "config_schema": CONFIG_SCHEMA_VERSION,
            "package_version": __version__,
            "mapping": recipe.mapping,
            "reader": reader,
            "extractor_version": extractor_version,
            "extract": {
                "backend": recipe.extract.backend,
                "confidence_threshold": recipe.extract.confidence_threshold,
                "local_model_override": recipe.extract.local_model_override,
                "local_model_id": recipe.extract.local_model_id,
                "sandbox": recipe.extract.sandbox,
            },
            "input_digest": input_digest,
        }
    )


def extraction_cacheable(recipe: Recipe, *, reader: str) -> bool:
    """Whether extraction output for this reader is safe to cache.

    Text and .eml parsing is stdlib-only and always a pure function of the
    file bytes. PDF parsing qualifies only under the plain ``pdfplumber``
    backend, and only while pdfplumber's installed version can be determined
    for the key: the OCR backend depends on the installed Tesseract, whose
    version the package does not pin, and the ``bedrock`` and ``local``
    backends may route pages through a model seam whose output is not a
    function of the file. Those all bypass the cache entirely, fail-closed.
    """

    if reader == "text":
        return True
    if recipe.extract.backend != "pdfplumber":
        return False
    return _extractor_version(reader) is not None


def _span_to_json(span: SourceSpan | TextSpan) -> dict[str, object]:
    if isinstance(span, SourceSpan):
        return {
            "kind": "pdf",
            "source_file": span.source_file,
            "page": span.page,
            "x0": span.x0,
            "top": span.top,
            "x1": span.x1,
            "bottom": span.bottom,
        }
    return {
        "kind": "text",
        "source_file": span.source_file,
        "line": span.line,
        "col_start": span.col_start,
        "col_end": span.col_end,
    }


def _span_from_json(data: object) -> SourceSpan | TextSpan | None:
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    try:
        if kind == "pdf":
            return SourceSpan(
                source_file=str(data["source_file"]),
                page=int(data["page"]),
                x0=float(data["x0"]),
                top=float(data["top"]),
                x1=float(data["x1"]),
                bottom=float(data["bottom"]),
            )
        if kind == "text":
            return TextSpan(
                source_file=str(data["source_file"]),
                line=int(data["line"]),
                col_start=int(data["col_start"]),
                col_end=int(data["col_end"]),
            )
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _is_string_map(value: object) -> TypeGuard[dict[str, str]]:
    return isinstance(value, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    )


def _rows_from_payload(payload: dict[str, object]) -> ExtractedRows | None:
    """Decode and validate a cached extraction payload; ``None`` on any mismatch."""

    pages_extracted = payload.get("pages_extracted")
    pages_dropped = payload.get("pages_dropped")
    entries = payload.get("rows")
    if not isinstance(pages_extracted, int) or not isinstance(pages_dropped, int):
        return None
    if isinstance(pages_extracted, bool) or isinstance(pages_dropped, bool):
        return None
    if not isinstance(entries, list):
        return None
    rows: list[Row] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        raw = entry.get("raw")
        spans_data = entry.get("spans")
        if not _is_string_map(raw) or not isinstance(spans_data, dict):
            return None
        spans: dict[str, SourceSpan | TextSpan] = {}
        for name, span_data in spans_data.items():
            span = _span_from_json(span_data)
            if not isinstance(name, str) or span is None:
                return None
            spans[name] = span
        rows.append((dict(raw), spans))
    return ExtractedRows(rows=rows, pages_extracted=pages_extracted, pages_dropped=pages_dropped)


def extraction_via_cache(
    active: ActiveCache | None,
    path: Path,
    recipe: Recipe,
    *,
    reader: str,
    extract_fresh: Callable[[], ExtractedRows],
) -> ExtractedRows:
    """Serve one document's extraction from the cache, or run and store it.

    ``extract_fresh`` performs the real parse; it is called on a miss and
    whenever this document's reader is not cacheable (see
    ``extraction_cacheable``). A fresh result marked not cacheable (a parse
    the sandbox killed against a resource limit) is returned but never
    stored, so the document is re-attempted on the next run instead of
    replaying an environment-dependent failure as a hit. The cached payload
    holds mapped raw values, spans, and page accounting only; record ids are
    minted by the caller so the source-wide duplicate-row counter stays
    correct.
    """

    if active is None or not extraction_cacheable(recipe, reader=reader):
        return extract_fresh()
    key = extraction_cache_key(file_digest(path), reader, recipe)
    payload = active.cache.get(EXTRACT_STAGE, key)
    if payload is not None:
        cached = _rows_from_payload(payload)
        if cached is not None:
            active.stats.hit(EXTRACT_STAGE)
            return cached
    active.stats.miss(EXTRACT_STAGE)
    fresh = extract_fresh()
    if not fresh.cacheable:
        return fresh
    active.cache.put(
        EXTRACT_STAGE,
        key,
        {
            "pages_extracted": fresh.pages_extracted,
            "pages_dropped": fresh.pages_dropped,
            "rows": [
                {
                    "raw": raw,
                    "spans": {name: _span_to_json(span) for name, span in spans.items()},
                }
                for raw, spans in fresh.rows
            ],
        },
    )
    return fresh


def _normalized_from_payload(
    payload: dict[str, object], fields: tuple[str, ...]
) -> dict[str, str] | None:
    """Validate a cached normalization payload; ``None`` on any mismatch.

    The stored key set must equal exactly what ``normalize_record`` emits for
    these fields, the derived matcher keys included (``normalized_keys`` in
    normalize.py). A payload missing a derived key would feed matching with
    absent columns and silently alter scores, and one carrying extras is not
    a shape this cache ever wrote, so both are ignored, never coerced.
    """

    normalized = payload.get("normalized")
    if not _is_string_map(normalized):
        return None
    if set(normalized) != normalized_keys(fields):
        return None
    return dict(normalized)


def _replay_failure_accounting(
    record: Record,
    fields: tuple[str, ...],
    normalized: dict[str, str],
    failures: dict[str, dict[str, int]] | None,
) -> None:
    """Fill the same failure counts a fresh ``normalize_record`` call would have."""

    if failures is None:
        return
    for field_name in fields:
        raw_value = record.raw.get(field_name, "")
        if raw_value.strip() and not normalized[field_name]:
            per_source = failures.setdefault(field_name, {})
            per_source[record.source] = per_source.get(record.source, 0) + 1


def normalize_via_cache(
    active: ActiveCache | None,
    record: Record,
    recipe: Recipe,
    *,
    failures: dict[str, dict[str, int]] | None = None,
) -> Record:
    """Serve one record's normalization from the cache, or compute and store it.

    ``normalize_record`` itself stays a pure function; this wrapper sits
    outside it. On a hit the failure accounting is replayed from the cached
    values, so the ingest report is identical either way. A recipe whose
    address backend version cannot be determined bypasses the cache entirely
    (see ``normalization_cacheable``), the same fail-closed direction as the
    uncacheable extraction backends.
    """

    if active is None or not normalization_cacheable(recipe):
        return normalize_record(
            record,
            recipe.fields,
            address_backend=recipe.normalize.address_backend,
            failures=failures,
        )
    key = normalize_cache_key(record.raw, recipe)
    payload = active.cache.get(NORMALIZE_STAGE, key)
    if payload is not None:
        normalized = _normalized_from_payload(payload, recipe.fields)
        if normalized is not None:
            active.stats.hit(NORMALIZE_STAGE)
            _replay_failure_accounting(record, recipe.fields, normalized, failures)
            return Record(
                unique_id=record.unique_id,
                source=record.source,
                raw=record.raw,
                normalized=normalized,
                consent=record.consent,
                spans=record.spans,
            )
    active.stats.miss(NORMALIZE_STAGE)
    fresh = normalize_record(
        record,
        recipe.fields,
        address_backend=recipe.normalize.address_backend,
        failures=failures,
    )
    active.cache.put(NORMALIZE_STAGE, key, {"normalized": dict(fresh.normalized)})
    return fresh
