"""Tests for the content-addressed stage cache (UC-01).

The invariants, from docs/NOVEL-USE-CASES-PLAN.md: only extraction and
normalization are ever cached, and scoring stays fresh on every run; editing
one source row re-keys that row's entry alone; cached and uncached runs
produce byte-identical decision inputs and golden records; a mismatched or
tampered entry is ignored, never coerced; the destruction command removes
planted field values from the cache; and the run summary and manifest record
cache policy and counts without paths or field values.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from constituent_reconciler import pipeline, stage_cache
from constituent_reconciler.cli import main
from constituent_reconciler.config import ExtractConfig, NormalizeConfig, RecipeError, load_recipe
from constituent_reconciler.provenance import verify_log
from constituent_reconciler.testing import make_pdf

KEY = "ab" * 16

HEADER = "id,First Name,Last Name,DOB,Email,Phone,Consent"
ROWS = (
    "A1,Maria,Garcia,1990-01-01,maria@example.org,555-111-2222,granted",
    "A2,Robert,Smith,1985-05-05,rob@example.org,555-333-4444,granted",
    "A3,Mariah,Garcia,1990-01-01,maria@example.org,555-111-2223,granted",
    "A4,Dana,Lee,1970-07-07,dana@example.org,555-555-6666,granted",
)

RECIPE_BODY = """
[input]
incoming = "incoming.csv"
id_column = "id"

[mapping]
first_name = "First Name"
last_name = "Last Name"
dob = "DOB"
email = "Email"
phone = "Phone"

[consent]
column = "Consent"
"""


def _write_corpus(base: Path, rows: tuple[str, ...] = ROWS) -> Path:
    path = base / "incoming.csv"
    path.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _write_recipe(base: Path, *, extra: str = "\n[cache]\nenabled = true\n") -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / "recipe.toml"
    path.write_text(RECIPE_BODY + extra, encoding="utf-8")
    return path


def _entry_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.json") if p.is_file())


# ---------------------------------------------------------------------------
# The filesystem cache itself: storage, mismatch handling, key validation.
# ---------------------------------------------------------------------------


def test_put_get_roundtrip_and_missing_is_a_miss(tmp_path: Path) -> None:
    cache = stage_cache.FilesystemStageCache(tmp_path / "cache")
    assert cache.get("normalize", KEY) is None
    cache.put("normalize", KEY, {"normalized": {"first_name": "maria"}})
    assert cache.get("normalize", KEY) == {"normalized": {"first_name": "maria"}}


def test_mismatched_or_corrupt_entries_are_ignored_never_coerced(tmp_path: Path) -> None:
    cache = stage_cache.FilesystemStageCache(tmp_path / "cache")
    cache.put("normalize", KEY, {"normalized": {"first_name": "maria"}})
    entry_path = tmp_path / "cache" / "normalize" / f"{KEY}.json"
    original = json.loads(entry_path.read_text(encoding="utf-8"))

    # A foreign envelope version is a miss, not a coercion target.
    tampered = dict(original, entry_version=999)
    entry_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert cache.get("normalize", KEY) is None

    # An entry whose stored key disagrees with its filename is a miss.
    tampered = dict(original, key="cd" * 16)
    entry_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert cache.get("normalize", KEY) is None

    # A payload that is not an object is a miss.
    tampered = dict(original, payload=["not", "an", "object"])
    entry_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert cache.get("normalize", KEY) is None

    # Unparseable bytes are a miss.
    entry_path.write_text("{not json", encoding="utf-8")
    assert cache.get("normalize", KEY) is None


def test_stage_and_key_are_validated_before_touching_paths(tmp_path: Path) -> None:
    cache = stage_cache.FilesystemStageCache(tmp_path / "cache")
    with pytest.raises(ValueError, match="hex digest"):
        cache.get("normalize", "../../etc/passwd")
    with pytest.raises(ValueError, match="hex digest"):
        cache.put("normalize", "A" * 32, {})
    with pytest.raises(ValueError, match="stage"):
        cache.put("scores", KEY, {})
    assert not (tmp_path / "cache").exists()


def test_every_key_component_changes_the_key(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    recipe = load_recipe(_write_recipe(tmp_path))
    raw = {"first_name": "Maria", "last_name": "Garcia"}
    keys = [
        stage_cache.normalize_cache_key(raw, recipe),
        stage_cache.normalize_cache_key({**raw, "first_name": "Mariah"}, recipe),
        stage_cache.normalize_cache_key(
            raw, replace(recipe, mapping={**recipe.mapping, "email": "E-mail"})
        ),
        stage_cache.normalize_cache_key(
            raw, replace(recipe, normalize=NormalizeConfig(address_backend="libpostal"))
        ),
        stage_cache.normalize_cache_key(raw, replace(recipe, fields=("first_name", "last_name"))),
        stage_cache.extraction_cache_key("0" * 64, "pdf", recipe),
        stage_cache.extraction_cache_key("0" * 64, "text", recipe),
        stage_cache.extraction_cache_key("1" * 64, "pdf", recipe),
        stage_cache.extraction_cache_key(
            "0" * 64,
            "pdf",
            replace(recipe, extract=ExtractConfig(backend="pdfplumber", confidence_threshold=0.7)),
        ),
    ]
    assert len(set(keys)) == len(keys)


def test_for_recipe_resolves_off_default_and_explicit_roots(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    out_dir = tmp_path / "out"

    plain = load_recipe(_write_recipe(tmp_path / "plain", extra=""))
    assert stage_cache.for_recipe(plain, out_dir) is None

    default = load_recipe(_write_recipe(tmp_path / "default"))
    default_cache = stage_cache.for_recipe(default, out_dir)
    assert default_cache is not None
    assert default_cache.root == out_dir / "stage_cache"

    boundary = tmp_path / "boundary"
    explicit = load_recipe(
        _write_recipe(
            tmp_path / "explicit",
            extra=f'\n[cache]\nenabled = true\ndir = "{boundary}"\n',
        )
    )
    explicit_cache = stage_cache.for_recipe(explicit, out_dir)
    assert explicit_cache is not None
    assert explicit_cache.root == boundary


# ---------------------------------------------------------------------------
# Recipe validation: the [cache] section is fail-closed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "section",
    [
        "[cache]\nenable = true\n",  # unknown key
        '[cache]\nenabled = "yes"\n',  # non-boolean enabled
        "[cache]\nenabled = true\ndir = 7\n",  # non-string dir
        '[cache]\nenabled = true\ndir = ""\n',  # empty dir
        '[cache]\ndir = "elsewhere"\n',  # dir without enabling the cache
        '[cache]\nenabled = false\ndir = "elsewhere"\n',  # dir on a disabled cache
    ],
)
def test_cache_section_shapes_are_refused(tmp_path: Path, section: str) -> None:
    _write_corpus(tmp_path)
    path = _write_recipe(tmp_path, extra="\n" + section)
    with pytest.raises(RecipeError):
        load_recipe(path)


def test_absent_cache_section_means_cache_off(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    recipe = load_recipe(_write_recipe(tmp_path, extra=""))
    assert recipe.cache.enabled is False
    assert recipe.cache.dir is None
    result = pipeline.run(recipe)
    assert result.cache.enabled is False
    assert result.cache.hits == {}
    assert result.cache.misses == {}


# ---------------------------------------------------------------------------
# Pipeline behavior: hit/miss accounting, per-row invalidation, fresh scoring.
# ---------------------------------------------------------------------------


def test_editing_one_row_invalidates_only_that_rows_entry(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    recipe_path = _write_recipe(tmp_path)
    out_dir = tmp_path / "out"
    recipe = load_recipe(recipe_path)
    cache = stage_cache.for_recipe(recipe, out_dir)
    assert cache is not None

    cold = pipeline.run(recipe, cache=cache)
    assert cold.cache.enabled is True
    assert cold.cache.misses == {"normalize": 4}
    assert cold.cache.hits == {}
    assert len(_entry_files(cache.root)) == 4

    warm = pipeline.run(recipe, cache=cache)
    assert warm.cache.hits == {"normalize": 4}
    assert warm.cache.misses == {}

    # Edit one row's email. Only that row's deterministic entry re-keys; the
    # other three rows still hit, and the stale entry is orphaned, not reused.
    edited = tuple(
        row.replace("rob@example.org", "robert.smith@example.org") if "A2" in row else row
        for row in ROWS
    )
    _write_corpus(tmp_path, edited)
    third = pipeline.run(load_recipe(recipe_path), cache=cache)
    assert third.cache.hits == {"normalize": 3}
    assert third.cache.misses == {"normalize": 1}
    assert len(_entry_files(cache.root)) == 5


def test_cached_and_uncached_runs_are_byte_identical(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    uncached_recipe = load_recipe(_write_recipe(tmp_path, extra=""))
    cached_recipe = load_recipe(_write_recipe(tmp_path / "cached-recipe"))
    _write_corpus(tmp_path / "cached-recipe")

    out_uncached = tmp_path / "out-uncached"
    pipeline.export(pipeline.run(uncached_recipe), uncached_recipe, out_dir=out_uncached)

    cache = stage_cache.for_recipe(cached_recipe, tmp_path / "out-cold")
    out_cold = tmp_path / "out-cold"
    pipeline.export(pipeline.run(cached_recipe, cache=cache), cached_recipe, out_dir=out_cold)
    out_warm = tmp_path / "out-warm"
    warm = pipeline.run(cached_recipe, cache=cache)
    assert warm.cache.hits == {"normalize": 4}
    pipeline.export(warm, cached_recipe, out_dir=out_warm)

    for name in ("resolved.csv", "review_queue.csv"):
        uncached_bytes = (out_uncached / name).read_bytes()
        assert (out_cold / name).read_bytes() == uncached_bytes
        assert (out_warm / name).read_bytes() == uncached_bytes


def test_adding_a_record_rescored_fresh_and_identical_to_an_uncached_run(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    recipe_path = _write_recipe(tmp_path)
    cache = stage_cache.for_recipe(load_recipe(recipe_path), tmp_path / "out")
    assert cache is not None
    pipeline.run(load_recipe(recipe_path), cache=cache)

    # A new near-duplicate arrives. Nothing about scoring may be reused: the
    # cached run must produce exactly what a from-scratch run produces.
    grown = ROWS + ("A5,Danna,Lee,1970-07-07,dana@example.org,555-555-6667,granted",)
    _write_corpus(tmp_path, grown)
    recipe = load_recipe(recipe_path)

    cached_result = pipeline.run(recipe, cache=cache)
    assert cached_result.cache.hits == {"normalize": 4}
    assert cached_result.cache.misses == {"normalize": 1}
    out_cached = tmp_path / "out-cached"
    pipeline.export(cached_result, recipe, out_dir=out_cached)

    out_fresh = tmp_path / "out-fresh"
    pipeline.export(pipeline.run(recipe), recipe, out_dir=out_fresh)

    for name in ("resolved.csv", "review_queue.csv"):
        assert (out_cached / name).read_bytes() == (out_fresh / name).read_bytes()
    assert "incoming:A5" in (out_cached / "resolved.csv").read_text(encoding="utf-8")

    # Only the deterministic stages ever land in the cache: no scores, bands,
    # or clusters are stored anywhere under the cache root.
    stage_dirs = {p.name for p in cache.root.iterdir() if p.is_dir()}
    assert stage_dirs <= {"extract", "normalize"}


def test_normalize_cache_hit_replays_failure_accounting(tmp_path: Path) -> None:
    rows = ROWS[:1] + ("A9,Pat,Doe,not-a-date,pat@example.org,555-000-1111,granted",)
    _write_corpus(tmp_path, rows)
    recipe = load_recipe(_write_recipe(tmp_path))
    cache = stage_cache.for_recipe(recipe, tmp_path / "out")

    cold = pipeline.run(recipe, cache=cache)
    warm = pipeline.run(recipe, cache=cache)
    assert warm.cache.hits == {"normalize": 2}
    assert cold.ingest.normalization_failures == {"dob": {"incoming": 1}}
    assert warm.ingest.normalization_failures == cold.ingest.normalization_failures
    assert warm.records == cold.records


def test_stage_durations_are_recorded_for_every_run(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    recipe = load_recipe(_write_recipe(tmp_path, extra=""))
    result = pipeline.run(recipe)
    assert set(result.stage_durations) == {"ingest", "normalize", "score", "resolve"}
    assert all(value >= 0.0 for value in result.stage_durations.values())


# ---------------------------------------------------------------------------
# Extraction caching: text sources cache; seam-capable backends bypass.
# ---------------------------------------------------------------------------


def _write_text_intake(base: Path) -> Path:
    docs = base / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "intake.txt").write_text(
        "First Name: Alice\nLast Name: Walker\nDOB: 1970-05-12\n"
        "Email: alice@example.org\nPhone: 555-123-4567\n",
        encoding="utf-8",
    )
    return docs


def test_text_extraction_is_served_from_the_cache(tmp_path: Path) -> None:
    _write_text_intake(tmp_path)
    recipe_path = tmp_path / "recipe.toml"
    recipe_path.write_text(
        """
[input]
incoming = "docs"

[mapping]
first_name = "First Name"
last_name = "Last Name"
dob = "DOB"
email = "Email"
phone = "Phone"

[extract]
backend = "pdfplumber"

[cache]
enabled = true
""",
        encoding="utf-8",
    )
    recipe = load_recipe(recipe_path)
    cache = stage_cache.for_recipe(recipe, tmp_path / "out")

    cold = pipeline.run(recipe, cache=cache)
    assert cold.cache.misses["extract"] == 1
    warm = pipeline.run(recipe, cache=cache)
    assert warm.cache.hits["extract"] == 1
    assert warm.records == cold.records
    assert warm.golden == cold.golden
    assert warm.ingest == cold.ingest


@pytest.mark.parametrize(
    ("backend", "reader", "cacheable"),
    [
        ("pdfplumber", "pdf", True),
        ("pdfplumber+ocr", "pdf", False),
        ("bedrock", "pdf", False),
        ("local", "pdf", False),
        ("bedrock", "text", True),
        ("pdfplumber", "text", True),
    ],
)
def test_extraction_cacheable_only_for_deterministic_readers(
    tmp_path: Path, backend: str, reader: str, cacheable: bool
) -> None:
    _write_corpus(tmp_path)
    base = load_recipe(_write_recipe(tmp_path))
    recipe = replace(base, extract=ExtractConfig(backend=backend))
    assert stage_cache.extraction_cacheable(recipe, reader=reader) is cacheable


def test_seam_capable_backend_bypasses_the_extraction_cache(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "form.pdf").write_bytes(
        make_pdf(
            [
                "Intake Form",
                "First Name: Alice",
                "Last Name: Walker",
                "DOB: 1970-05-12",
            ]
        )
    )
    recipe_path = tmp_path / "recipe.toml"
    recipe_path.write_text(
        """
[input]
incoming = "docs"

[mapping]
first_name = "First Name"
last_name = "Last Name"
dob = "DOB"

[extract]
backend = "bedrock"

[cache]
enabled = true
""",
        encoding="utf-8",
    )
    recipe = load_recipe(recipe_path)
    cache = stage_cache.for_recipe(recipe, tmp_path / "out")
    assert cache is not None

    result = pipeline.run(recipe, cache=cache)
    # The extraction stage never touched the cache; normalization still did.
    assert "extract" not in result.cache.hits
    assert "extract" not in result.cache.misses
    assert result.cache.misses.get("normalize", 0) >= 1
    assert not (cache.root / "extract").exists()


# ---------------------------------------------------------------------------
# CLI wiring: run summary, manifest, dry runs, validate, and destruction.
# ---------------------------------------------------------------------------


def test_run_summary_and_manifest_record_cache_policy_without_paths(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    recipe_path = _write_recipe(tmp_path)
    out_dir = tmp_path / "out"

    assert main(["run", "--config", str(recipe_path), "--out", str(out_dir)]) == 0
    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["cache"] == {"enabled": True, "hits": {}, "misses": {"normalize": 4}}
    assert set(summary["stage_durations_seconds"]) == {"ingest", "normalize", "score", "resolve"}

    manifest_text = (out_dir / "run_manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["cache"] == {
        "enabled": True,
        "custom_boundary": False,
        "hits": {},
        "misses": {"normalize": 4},
    }
    # Policy and counts only: no filesystem location enters the manifest.
    assert "stage_cache" not in manifest_text
    assert str(out_dir) not in manifest_text

    assert main(["run", "--config", str(recipe_path), "--out", str(out_dir)]) == 0
    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["cache"] == {"enabled": True, "hits": {"normalize": 4}, "misses": {}}


def test_dry_run_neither_reads_nor_writes_the_cache(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    recipe_path = _write_recipe(tmp_path)
    out_dir = tmp_path / "out"
    assert main(["run", "--config", str(recipe_path), "--out", str(out_dir), "--dry-run"]) == 0
    assert not (out_dir / "stage_cache").exists()


def test_validate_reports_the_cache_switch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_corpus(tmp_path)
    on = _write_recipe(tmp_path)
    assert main(["validate", "--config", str(on)]) == 0
    assert "cache: enabled=True (stage_cache under the output root)" in capsys.readouterr().out

    off = _write_recipe(tmp_path / "off", extra="")
    _write_corpus(tmp_path / "off")
    assert main(["validate", "--config", str(off)]) == 0
    assert "cache: enabled=False" in capsys.readouterr().out


def test_destroy_removes_a_planted_value_from_the_cache(tmp_path: Path) -> None:
    sentinel = "planted-pii-cache@example.org"
    rows = ROWS[:2] + (f"A8,Casey,Reyes,1991-02-03,{sentinel},555-777-8888,granted",)
    _write_corpus(tmp_path, rows)
    recipe_path = _write_recipe(tmp_path)
    out_dir = tmp_path / "out"

    assert main(["run", "--config", str(recipe_path), "--out", str(out_dir)]) == 0
    cache_files = _entry_files(out_dir / "stage_cache")
    assert any(sentinel in path.read_text(encoding="utf-8") for path in cache_files)

    assert main(["destroy", "--out", str(out_dir), "--older-than", "0d"]) == 0
    remaining = [p for p in out_dir.rglob("*") if p.is_file()]
    assert remaining, "the provenance log must survive"
    for path in remaining:
        assert sentinel.encode("utf-8") not in path.read_bytes(), path
    assert not (out_dir / "stage_cache").exists()

    # Each cache entry got its own content-free destruction certificate, and
    # the chain still verifies.
    entries = [
        json.loads(line)
        for line in (out_dir / "provenance.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    certified = [e["record_id"] for e in entries if e["action"] == "destroyed"]
    assert any(name.startswith("stage_cache/normalize/") for name in certified)
    ok, message = verify_log(out_dir / "provenance.jsonl")
    assert ok, message


def test_destroy_covers_an_explicit_cache_boundary(tmp_path: Path) -> None:
    boundary = tmp_path / "boundary"
    _write_corpus(tmp_path)
    recipe_path = _write_recipe(tmp_path, extra=f'\n[cache]\nenabled = true\ndir = "{boundary}"\n')
    recipe = load_recipe(recipe_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    cache = stage_cache.for_recipe(recipe, out_dir)
    pipeline.run(recipe, cache=cache)
    assert _entry_files(boundary)

    assert (
        main(
            [
                "destroy",
                "--out",
                str(out_dir),
                "--cache-dir",
                str(boundary),
                "--older-than",
                "0d",
            ]
        )
        == 0
    )
    assert not boundary.exists()
    entries = [
        json.loads(line)
        for line in (out_dir / "provenance.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    certified = [e["record_id"] for e in entries if e["action"] == "destroyed"]
    assert certified and all(name.startswith("stage_cache/") for name in certified)
