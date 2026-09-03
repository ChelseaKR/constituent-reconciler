# Labeled extraction fixtures

This directory is the ground truth behind the extraction row of the metrics
ledger in `docs/ROADMAP.md`. It holds four deterministic synthetic intake-form
PDFs, hand-written labels for what a correct extractor should pull from each,
and the script that regenerates the PDFs. `constituent-reconcile eval-extraction` (or
`make eval-extraction`) runs the offline PDF extractor over the PDFs, scores it
against `labels.json`, and writes `eval/extraction-report.md`. There is no real
personal data here; every name, date, and number is invented.

## Files

* `form-*.pdf` — the fixture documents, committed binaries. Regenerate them
  with `.venv/bin/python eval/fixtures/extraction/make_fixtures.py`; the
  generator is stdlib-only and byte-for-byte deterministic, so a clean
  regeneration produces no diff.
* `labels.json` — the ground truth, maintained by hand:
  `{"<pdf filename>": [{"field_name": ..., "value": ...}, ...]}`.
* `make_fixtures.py` — the document text and the regeneration entry point.

## What each document exercises

| Document | Case |
|----------|------|
| `form-standard.pdf` | The common form: all five canonical fields, common labels. |
| `form-alternate-order.pdf` | Alternate labels (`Given Name`, `Surname`, `Birth Date`) and a different field order, with punctuated phone and slash-date values. |
| `form-unparseable.pdf` | Fields that should not parse: `DOB: unknown` and `Email: none provided`. The labels omit both; extracting anything for them counts as a false positive. |
| `form-worded-date.pdf` | A date written in words (`March 9, 1988`). A human can read it, so it is labeled, and the deterministic extractor misses it: a planted false negative that keeps recall below a guaranteed 100%. |

## Labeling conventions

* **Label what a correct extractor should return, per field.** If a value is
  present and readable by a person, label it, even when the current extractor
  is known to miss it (that is the point of `form-worded-date.pdf`). If a
  field's value should not be extracted (`unknown`, `none provided`), leave it
  out of the labels entirely.
* **Write values as they appear in the document.** Scoring compares on
  normalized values using the same normalizers the matching pipeline applies
  (`evaluate.normalize_extracted_value`): names lose case, accents,
  punctuation, and spacing; dates reduce to ISO `YYYY-MM-DD`; phones reduce to
  their last ten digits; emails are casefolded. `(415) 555-0100` and
  `4155550100` are therefore the same phone, and formatting differences never
  count as extraction errors.
* **One label per expected field occurrence.** Matching is per document and
  each label can be claimed by one prediction; a duplicated prediction scores
  one true positive and one false positive.
* **Zero real PII.** Fixtures are synthetic and reviewed as such before
  committing.

## Changing the fixtures

Edit `DOCUMENTS` in `make_fixtures.py`, rerun it, and re-check `labels.json`
by reading the new document text yourself; do not derive labels from the
extractor's output, which would make the measurement circular. Then run
`make eval-extraction` to regenerate the committed report, and expect
`tests/test_evaluate.py` to hold the set at or above the ledger targets
(precision at least 0.95, recall at least 0.90).
