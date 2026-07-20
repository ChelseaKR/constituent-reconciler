<!-- The checklist mirrors DEFINITION_OF_DONE.md; check what applies and say
so where it does not. Honest "not applicable, because X" beats a hollow tick. -->

## What changed and why

<!-- One or two sentences. Name the roadmap or ideation item if there is one. -->

## Checklist

- [ ] `make verify` green locally (format, lint, mypy --strict, hygiene gate,
      tests with the 85% coverage floor)
- [ ] `make security` green (pip-audit + osv-scanner, no fixable HIGH/CRITICAL)
- [ ] Privacy-invariant tests pass unmodified (`test_no_egress`,
      `test_consent`, `test_suppression`, `test_provenance`)
- [ ] New or changed behavior has tests, including the failing fixture
- [ ] Eval reports regenerated if matching, extraction, or bias-relevant
      behavior changed (`make eval`, `make eval-extraction`, `make eval-bias`);
      measured misses preserved
- [ ] Docs and CHANGELOG updated, claiming only what the code does
- [ ] New dependency? Rationale stated below
- [ ] Release PR? Responsible-tech audits re-stamped, DPG conformance checked,
      `CITATION.cff` version and date updated

## New-dependency rationale (if any)

<!-- Why the standard library or an existing dependency was not enough. -->
