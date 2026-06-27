.PHONY: install verify lint type test eval run docker clean

# Reproduce the full local toolchain. CI mirrors `make verify` byte for byte.
install:
	uv venv --python 3.11 .venv
	uv pip install --python .venv/bin/python -e ".[dev]"

lint:
	.venv/bin/ruff check src tests

type:
	.venv/bin/python -m mypy

test:
	.venv/bin/python -m pytest

verify: lint type test

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
