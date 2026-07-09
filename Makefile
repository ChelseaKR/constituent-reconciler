.PHONY: install verify format-check lint type test security eval eval-large run docker clean

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

verify: format-check lint type test

# Regenerate the committed eval report. Run after any change to matching.
eval:
	.venv/bin/reconcile eval \
		--config examples/intake-demo/recipe.toml \
		--truth examples/intake-demo/ground_truth.json \
		--out eval/report.md

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

# Run the demo end to end and write outputs to ./out.
run:
	.venv/bin/reconcile run --config examples/intake-demo/recipe.toml --out out

# Build the self-host image. See the Dockerfile header for run commands.
docker:
	docker build -t constituent-reconciler .

clean:
	rm -rf out out-dv .pytest_cache .mypy_cache .ruff_cache
