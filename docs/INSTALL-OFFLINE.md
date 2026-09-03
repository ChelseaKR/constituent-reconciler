# Installing on a machine with no internet access

Some organizations, victim-service providers especially, run the machine that
holds client data with outbound internet disabled. `make install` needs PyPI
and `make docker` needs an image registry, so neither works there. The offline
bundle carries everything the install needs across the air gap on removable
media.

## What the bundle contains

`make bundle` (or the `bundle` job in CI) writes `dist/bundle/` with:

| File | Purpose |
| --- | --- |
| `wheelhouse/` | The `constituent-reconciler` wheel and every dependency, including the dev tools (`pytest`, `mypy`, `ruff`) and the PDF extraction extra, as wheels for the build machine's platform and Python |
| `constituent-reconciler-image.tar` | The self-host Docker image from `docker save` (present when the build machine had Docker) |
| `constituent-reconciler-src.tar.gz` | A `git archive` of the source tree, so `make verify` can run offline |
| `INSTALL-OFFLINE.md` | This document |
| `SHA256SUMS` | SHA-256 checksums of every other file in the bundle |
| `SHA256SUMS.sigstore.json` | Sigstore signature over `SHA256SUMS` (CI-built bundles only) |

The wheelhouse is resolved for the platform and Python minor version of the
machine that built it. Build the bundle on a machine matching the offline
target (CI builds on Linux x86_64 with Python 3.11), and use Python 3.11 on
the target.

## 1. Verify the bundle while still online

On the connected machine that downloaded the CI artifact, check the checksums:

```sh
cd dist/bundle
sha256sum -c SHA256SUMS        # macOS: shasum -a 256 -c SHA256SUMS
```

CI-built bundles include a Sigstore keyless signature over `SHA256SUMS`,
signed by the GitHub Actions workflow identity. Verify it before trusting the
checksums (`pip install sigstore` on the connected machine):

```sh
sigstore verify github \
  --cert-identity "https://github.com/ChelseaKR/constituent-reconciler/.github/workflows/ci.yml@refs/heads/main" \
  --bundle SHA256SUMS.sigstore.json \
  SHA256SUMS
```

A bundle built locally with `make bundle` has no signature; the checksums
still catch corruption in transfer, but authenticity rests on how the bundle
reached you.

## 2. Transfer

Copy the whole `dist/bundle/` directory to removable media and carry it to
the offline machine. On arrival, run the checksum verification again there to
catch corruption in transit.

## 3. Install the package

With Python 3.11 on the offline machine:

```sh
python3.11 -m venv .venv
.venv/bin/pip install --no-index --find-links wheelhouse constituent-reconciler
.venv/bin/constituent-reconcile --help
```

`--no-index` guarantees pip never attempts the network; everything resolves
from the wheelhouse. For PDF extraction, install
`"constituent-reconciler[extract]"` instead. The wheelhouse also carries the
dev tools, used in step 5.

## 4. Load the Docker image (optional)

If the offline machine runs workloads in Docker:

```sh
docker load -i constituent-reconciler-image.tar
docker run --rm constituent-reconciler --help
```

`docker load` reads the tarball directly and contacts no registry.

## 5. Prove the install with `make verify`

The source archive lets the full lint, type, and test gate run offline:

```sh
mkdir constituent-reconciler && tar -xzf constituent-reconciler-src.tar.gz -C constituent-reconciler
cd constituent-reconciler
python3.11 -m venv .venv
.venv/bin/pip install --no-index --find-links ../wheelhouse "constituent-reconciler[dev]"
make verify
```

The install uses the pre-built wheel from the wheelhouse rather than an
editable install, because building from source would need the build backend,
which the bundle does not carry. The tests import the installed package, so
the gate exercises exactly what step 3 installed.

`make verify` passing on the offline machine confirms the bundle carried a
working toolchain, not only a working wheel. The bundled demo then runs the
same way it does anywhere:

```sh
.venv/bin/constituent-reconcile run --config examples/intake-demo/recipe.toml --out out
```
