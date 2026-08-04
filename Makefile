.PHONY: install verify format-check lint type test security axe-fixtures axe eval eval-extraction eval-bias eval-large perf-baseline perf-baseline-pdf run docker bundle clean

# Reproduce the full local toolchain. CI mirrors `make verify` byte for byte.
# `uv sync --frozen` refuses to run (and exits non-zero) if uv.lock is stale
# relative to pyproject.toml, so local and CI installs are always the exact
# locked dependency set (CQ-09).
install:
	uv sync --frozen --python 3.12 --group dev --extra extract

format-check:
	.venv/bin/ruff format --check src tests tools

lint:
	.venv/bin/ruff check src tests tools

type:
	.venv/bin/python -m mypy

# Tests run under coverage; the report enforces the 85% line-coverage floor
# on src/ (configured in pyproject.toml), so `make verify` — and therefore
# CI — is a merge-blocking coverage gate.
test:
	.venv/bin/python -m pytest

# Dependency-vulnerability gate (SEC-11, SEC-13): pip-audit and osv-scanner
# both block on any HIGH/CRITICAL finding with a fix available; no mute pattern.
# --skip-editable excludes only this repo's own (unpublished) package from the
# PyPI-advisory lookup; -S/--strict is intentionally omitted because it treats
# that expected skip itself as a collection failure.
security:
	.venv/bin/python -m pip_audit --skip-editable --progress-spinner=off
	osv-scanner --lockfile uv.lock

# Source-hygiene gate (CQ-34/CQ-35): no debt markers, no uncoded or
# unexplained suppressions. tools/hygiene.py documents each rule.
hygiene:
	.venv/bin/python tools/hygiene.py

verify: format-check lint type hygiene test

# Automated axe-core audit of the review queue's actual rendered HTML
# (docs/adr/0011-automated-axe-audit.md). Not part of `verify`, so a
# contributor without Node installed is not blocked from the Python checks;
# CI runs it as its own job. Requires `npm ci` once.
axe-fixtures:
	rm -rf .axe-fixtures
	.venv/bin/python scripts/render_axe_fixtures.py --out .axe-fixtures

axe: axe-fixtures
	node scripts/axe_audit.mjs .axe-fixtures

# Regenerate the committed eval report. Run after any change to matching.
# The calibration flag arms the fail-closed kappa gate for the LLM field judge.
eval:
	.venv/bin/reconcile eval \
		--config examples/intake-demo/recipe.toml \
		--truth examples/intake-demo/ground_truth.json \
		--calibration examples/intake-demo/calibration_labels.json \
		--out eval/report.md

# Regenerate the committed extraction eval report. Run after any change to the
# extractor or to eval/fixtures/extraction. Exits nonzero below the ledger
# targets (precision 0.95, recall 0.90).
eval-extraction:
	.venv/bin/reconcile eval-extraction \
		--fixtures eval/fixtures/extraction \
		--out eval/extraction-report.md

# Regenerate the disaggregated matching-risk audit (R5). Every named segment
# must be explicit in ground_truth.json; the report preserves misses rather
# than reducing the fixture until every row looks green.
eval-bias:
	.venv/bin/reconcile eval \
		--config examples/bias-demo/recipe.toml \
		--truth examples/bias-demo/ground_truth.json \
		--calibration examples/bias-demo/calibration_labels.json \
		--out docs/audits/bias-report.md

# Regenerate the large synthetic-corpus eval report (FIX-11). Not part of
# `verify` or CI: a 10^4-10^5 record corpus through Splink/DuckDB takes
# materially longer than the 27-record demo `make eval` gates on, so this
# runs on release instead of every push. Corpus and report are both
# regenerated fresh each time (deterministic from the seed baked into the
# script), so nothing here needs to be committed except the resulting report.
eval-large:
	.venv/bin/python -m tools.corpusgen.run_large_eval \
		--out-dir eval/large-corpus \
		--report-out eval/large-corpus-report.md \
		--regenerate

# Stage-timing baseline over the large synthetic corpus (UC-01 "before"
# numbers): per-stage wall clock and peak memory for ingest, extract,
# normalize, score, review artifact, and write, as a dated report plus a JSON
# companion the future cached run diffs against. Local command, not a CI job,
# for the same reason as eval-large; the harness itself is smoke-tested on a
# tiny corpus by tests/test_stage_baseline.py.
PERF_DATE := $(shell date +%Y-%m-%d)
perf-baseline:
	.venv/bin/python -m tools.corpusgen.stage_baseline \
		--out-dir eval/large-corpus \
		--report-out eval/large-corpus-stage-baseline-$(PERF_DATE).md \
		--json-out eval/large-corpus-stage-baseline-$(PERF_DATE).json \
		--regenerate

# The same measurement over a mixed CSV+PDF corpus. `perf-baseline` above is
# honest that extract takes 0.0s, because that corpus is CSV-only and the
# extractor never runs; this variant carries PDF_SHARE of the incoming rows as
# seeded text-layer PDF intake documents so the extract row measures the
# pipeline's own pdfplumber path. Its corpus goes in a separate directory:
# the two layouts differ, and neither should overwrite the other's inputs.
# Share and directory are pinned here so the command is reproducible; the
# corpus is regenerated from the same seed on every run.
PDF_SHARE := 0.15
perf-baseline-pdf:
	.venv/bin/python -m tools.corpusgen.stage_baseline \
		--out-dir eval/large-corpus-pdf \
		--pdf-share $(PDF_SHARE) \
		--report-out eval/large-corpus-stage-baseline-pdf-$(PERF_DATE).md \
		--json-out eval/large-corpus-stage-baseline-pdf-$(PERF_DATE).json \
		--regenerate

# Run the demo end to end and write outputs to ./out.
run:
	.venv/bin/reconcile run --config examples/intake-demo/recipe.toml --out out

# Build the self-host image. See the Dockerfile header for run commands.
docker:
	docker build -t constituent-reconciler .

# Build the offline install bundle in dist/bundle: a wheelhouse for this
# platform and Python, the saved Docker image when Docker is available, a source
# archive, the install doc, and SHA256SUMS over all of it. See
# docs/INSTALL-OFFLINE.md for the receiving side.
BUNDLE := dist/bundle
SHA256 := $(shell command -v sha256sum >/dev/null 2>&1 && echo sha256sum || echo "shasum -a 256")

bundle:
	rm -rf $(BUNDLE)
	mkdir -p $(BUNDLE)/wheelhouse
	uv build --wheel --out-dir $(BUNDLE)/wheelhouse
	uv export --frozen --format requirements.txt --group dev --extra extract --no-hashes --no-emit-project --output-file $(BUNDLE)/requirements.txt
	.venv/bin/python -m pip download --requirement $(BUNDLE)/requirements.txt --dest $(BUNDLE)/wheelhouse
	@if command -v docker >/dev/null 2>&1; then \
		docker build -t constituent-reconciler . && \
		docker save constituent-reconciler -o $(BUNDLE)/constituent-reconciler-image.tar; \
	else \
		echo "warning: docker not found; bundle omits constituent-reconciler-image.tar" >&2; \
	fi
	git archive --format=tar.gz -o $(BUNDLE)/constituent-reconciler-src.tar.gz HEAD
	cp docs/INSTALL-OFFLINE.md $(BUNDLE)/INSTALL-OFFLINE.md
	cd $(BUNDLE) && find . -type f ! -name SHA256SUMS | sed 's|^\./||' | LC_ALL=C sort \
		| xargs $(SHA256) > SHA256SUMS
	@echo "bundle written to $(BUNDLE)"

clean:
	rm -rf out out-dv dist .pytest_cache .mypy_cache .ruff_cache .coverage .axe-fixtures
