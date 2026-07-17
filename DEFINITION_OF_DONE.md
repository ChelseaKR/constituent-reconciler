# Definition of done

A change to this repository is done when every statement below is true of it.
This is the working agreement that CLAUDE.md's quality bar implies, written
down so a PR review checks a list instead of a memory. The PR template asks
for the same items at review time.

1. `make verify` passes locally: format check, `ruff check` (including S and
   C90), `mypy --strict`, the source-hygiene gate (`make hygiene`: no debt
   markers, no uncoded or unexplained suppressions), and the test suite with
   the 85% branch-coverage floor. CI runs the identical Makefile path.
2. `make security` passes: pip-audit and osv-scanner over `uv.lock` with no
   fixable HIGH or CRITICAL finding.
3. The privacy-invariant tests pass unmodified: `tests/test_no_egress.py`,
   `tests/test_consent.py`, `tests/test_suppression.py`,
   `tests/test_provenance.py`. A change that requires weakening any of them
   is not a normal PR; it needs a recorded decision (ADR) first.
4. Behavior that changed has tests, with a passing and a failing fixture.
   Fail-closed paths are exercised, not assumed.
5. If matching, extraction, or bias-relevant behavior changed, the committed
   eval reports were regenerated (`make eval`, `make eval-extraction`,
   `make eval-bias`) and their diffs are part of the PR. Measured misses stay
   in the reports; fixtures are not tuned until green.
6. Docs claim only what the code does. New capability language in README,
   CLAUDE.md, or docs/ is backed by a test; user-visible changes have a
   CHANGELOG entry under Unreleased.
7. A new dependency arrives with a stated rationale in the PR description,
   consistent with the standard-library-first rule in CLAUDE.md.
8. A release PR additionally re-stamps `docs/RESPONSIBLE-TECH-AUDITS.md`
   (and `docs/DPG-CONFORMANCE.md` if conformance changed), regenerates the
   eval reports, and updates `CITATION.cff`'s version and date-released.
9. Commit messages follow conventional commits, and the change is PR-sized
   even when working solo.

Items that a solo maintainer cannot self-satisfy (a second reviewer, live
repository settings) are governed by `docs/adr/0008-solo-maintainer-review-waiver.md`
rather than silently skipped.
