# One-command self-host for constituent-reconciler.
#
# Build:
#   docker build -t constituent-reconciler .
#
# Run the bundled demo (writes to a mounted ./out):
#   docker run --rm -v "$PWD/out:/work/out" constituent-reconciler \
#     run --config examples/intake-demo/recipe.toml --out out
#
# Run against your own data and recipe mounted at /work/data:
#   docker run --rm -v "$PWD/data:/work/data" constituent-reconciler \
#     run --config /work/data/recipe.toml --out /work/data/out
#
# The image installs the PDF extraction extra (pdfplumber). The libpostal address
# backend is not included because it needs a system C library; the default
# deterministic address backend works without it.

# Pinned by digest, not just tag, so a build is reproducible and Renovate has
# something concrete to bump (renovate.json's digest-pinning helper covers
# Docker references the same way it covers GitHub Actions).
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

# Avoid interactive prompts and keep the image lean.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /work

# Install the package first so its layer caches independently of the examples.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[extract]"

# Ship the example fixtures so the demo runs out of the box.
COPY examples ./examples

# A non-root user; the work directory is writable for mounted volumes.
RUN useradd --create-home runner && chown -R runner:runner /work
USER runner

ENTRYPOINT ["reconcile"]
CMD ["--help"]
