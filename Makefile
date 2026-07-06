.PHONY: install verify format-check lint type test security eval run docker clean

# Reproduce the full local toolchain. CI mirrors `make verify` byte for byte.
# `uv sync --frozen` refuses to run (and exits non-zero) if uv.lock is stale
# relative to pyproject.toml, so local and CI installs are always the exact
# locked dependency set (CQ-09).
install:
	uv sync --frozen --python 3.12 --group dev --extra extract

format-check:
	.venv/bin/ruff format --check src tests

lint:
	.venv/bin/ruff check src tests

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

# Run the demo end to end and write outputs to ./out.
run:
	.venv/bin/reconcile run --config examples/intake-demo/recipe.toml --out out

# Build the self-host image. See the Dockerfile header for run commands.
docker:
	docker build -t constituent-reconciler .

clean:
	rm -rf out out-dv .pytest_cache .mypy_cache .ruff_cache
