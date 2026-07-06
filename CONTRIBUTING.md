# Contributing

Thanks for considering a contribution. This project aims to be genuinely useful
to small nonprofits, so correctness and honesty matter more than feature count.

## Setup

```sh
make install      # creates .venv (Python 3.12) via `uv sync --frozen`, dev+extract
make verify       # ruff, mypy --strict, pytest: the full local gate
```

`make verify` reproduces the merge-blocking checks. If it is green locally it
should be green in CI.

Install the pre-commit hooks once per clone (secret scan + ruff, fast local
feedback before CI runs the equivalent checks):

```sh
brew install gitleaks   # or your platform's gitleaks 8.30.1+; pre-commit
                         # runs the already-installed binary, not a Go build
pip install pre-commit  # or: uv tool install pre-commit
pre-commit install
```

## Ground rules

- **Fail closed.** Anything uncertain (a low-confidence match, an ambiguous
  consent value, a parse error) routes to a human or blocks the write. Do not
  add a path that silently merges or silently exports.
- **The privacy invariants are tests.** `tests/test_consent.py` and the pipeline
  tests assert that a non-consented record is withheld. Changes that touch
  consent or export must keep those tests, and add new ones for new surfaces.
- **No real personal data, ever.** Fixtures are seeded and synthetic. Do not add
  a real intake form, a real export, or a real constituent record to the repo or
  to an issue.
- **Do not reimplement record linkage.** The matcher wraps Splink. Contributions
  improve the pre-tuned defaults, the orchestration, and the review surface, not
  a new linkage engine.
- **Honesty in claims.** The address standardizer is "CASS-style," not
  USPS-certified. Keep claims to what the code does.

## Pull requests

- Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).
- Keep PRs small and reviewable, even when working solo.
- Update `docs/ROADMAP.md` and `CHANGELOG.md` when behavior changes, and
  regenerate the eval report (`make eval`) when matching changes.

## Reporting a security or privacy issue

See [SECURITY.md](SECURITY.md). Please do not open a public issue for a
vulnerability or a data-exposure concern.
