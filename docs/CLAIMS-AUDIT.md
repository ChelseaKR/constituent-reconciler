# Capability claims audit

**Audited:** 2026-07-22 (roadmap closeout re-verification); stage-cache row
added and updated 2026-08-03.
**Scope:** every capability claim in `README.md` and `CLAUDE.md`, read against
the code in `src/constituent_reconciler/`.
**Method:** each claim below was checked by opening the named module, not by
trusting a docstring. Line numbers are approximate as of the audit date;
section names are given so the row survives line drift.

Status values: **implemented** (the code does what the sentence says),
**planned** (the docs label it as not yet built, and the code confirms that),
**corrected** (the docs overstated the code and were fixed in this audit).

| Claim | Where stated (file:line) | Code location | Status |
| --- | --- | --- | --- |
| Ingest reads a folder (or single file) of CSVs | README.md:57 ("What it does" step 1); CLAUDE.md "What this is" | `pipeline.py` `_ingest_source` routes `.csv` to the structured reader | implemented |
| Ingest reads digitally created (text-layer) PDFs | README.md:57 (step 1); README status note | `pipeline.py` `_ingest_source` routes `.pdf` to the extractor; `extract/pdf.py` `PdfplumberExtractor` | implemented (text layer only) |
| Scanned-document (OCR) ingest | README.md "What it does" step 1 | `extract/ocr.py`; `pipeline.py` selects `pdfplumber+ocr`; `tests/test_ocr.py` | implemented (local Tesseract, optional `ocr` extra) |
| Plain-text and email-body ingest | README.md "What it does" step 1 | `extract/text.py`; `_ingest_source` routes `.txt` and `.eml`; `tests/test_extract_text.py` | implemented (plain-text body only; attachments are not ingested) |
| Review verdicts: approve and reject | README.md:5, 70-73 (step 5), 181 | `review/session.py` `APPROVED`, `REJECTED`, `_VERDICTS`; served by `review/server.py` | implemented |
| Review verdict: correct (fix field values during review) | README.md "What it does" step 5 | `review/session.py` correction flow; `pipeline.py` applies corrections before normalization | implemented (EXP-01) |
| Local CSV connector (default) | README.md:74-76 (step 6) | `connectors/csv_out.py`; `pipeline.py` `build_connector` | implemented |
| Import-ready CRM export files (`salesforce_csv`, `civicrm_csv`) | README.md:254-258 | `connectors/crm_csv.py` | implemented |
| CiviCRM live write-back (API v4 upsert) | README.md:74-76, 272-283 | `connectors/civicrm.py` | implemented |
| Salesforce live write-back (REST upsert, NPSP Contact) | README.md:74-76, 285-292 | `connectors/salesforce.py` | implemented |
| Generic webhook connector | README.md "What it does" step 6 | `connectors/webhook.py`; connector conformance tests | implemented |
| Airtable connector | README.md "What it does" step 6 | `connectors/airtable.py`; connector conformance and adapter tests | implemented (native batched upsert; live-account evidence remains external) |
| Google Sheets connector | `docs/connectors/sheets-design.md` | design brief only | closed as not meeting the atomic/idempotent upsert contract; reconsider only with an explicit weaker connector contract |
| Apricot connector | `docs/connectors/apricot-design.md` | design brief only | blocked on a verifiable vendor write contract and authorized test account; no API shape is guessed |
| Policy packs: default, dv, hipaa | README.md:89 (the dv pack); CLAUDE.md architecture map | `policy.py` `_PACKS` defines exactly `default`, `dv`, `hipaa` | implemented |
| DV pack: cloud seam fused off, local write targets only, fail-closed | README.md:96-99; CLAUDE.md ground rules | `extract/seam.py` (packs that forbid cloud calls), `pipeline.py` `build_connector` (refuses non-local targets), `tests/test_no_egress.py` | implemented |
| Optional Bedrock (Claude) extraction seam, policy-gated, low-confidence pages only | README.md:60-63 (step 2) | `extract/seam.py` `BedrockSeam` and its disabled fallback | implemented (requires boto3 and AWS credentials at deploy time) |
| Append-only, tamper-evident provenance log (BLAKE2b hash chain, verifiable) | README.md:77-79 (step 7), 294-300 | `provenance.py` `content_hash`, entry chaining, `verify_log`; `reconcile verify` in `cli.py` | implemented |
| RFC 3161 trusted timestamps on provenance entries | README.md "What it does" step 7 | `provenance.py` `Rfc3161Authority`; selected by recipe `tsa_url`; response/imprint/nonce tests | implemented and opt-in; local clock remains the default |
| Consent-gated export: non-consented records withheld, fail-closed | README.md:74, 92-95 | `consent.py` `partition_by_consent`; `pipeline.py` writes `withheld.csv` with ids and reason only | implemented (enforced when the active pack requires consent: dv and hipaa; the default pack does not) |
| CASS-style address standardization, not USPS-certified | README.md:64-66 (step 3) | `address.py` | implemented |
| Content-addressed stage cache for extraction and normalization; scoring stays fresh; cache covered by destruction (UC-01) | docs/NOVEL-USE-CASES-PLAN.md "UC-01"; CHANGELOG.md Unreleased; docs/DATA-FLOW-AND-RETENTION.md inventory | `stage_cache.py`; `pipeline.run(cache=...)`; `config.py` `[cache]`; `destruction.py` cache roots; `tests/test_stage_cache.py`, `tests/test_no_egress.py` (byte-identical cached/uncached runs, per-row invalidation, local write boundary, planted-value destruction) | implemented, with two UC-01 acceptance items undelivered and named here: progress events are not built, and the large-corpus wall-clock and peak-memory before/after numbers required by acceptance criterion 5 have not been measured; no benchmark evidence exists in this repo, and none is claimed |
| CLAUDE.md architecture map matches the code | CLAUDE.md:72-108 formerly listed `ingest.py`, `gate.py`, `resolve.py`, `extract/deterministic.py`, a `policy/` package, and `test_consent_blocks_export.py`, none of which exist | as built: `pipeline.py` (ingest lives here), `decisions.py` (banding and the fail-closed gate), `matching.py` (Splink wrapper), `extract/pdf.py`, `policy.py`, `tests/test_consent.py` | corrected 2026-07-02 |

## Re-running this audit

Before a release:

1. `grep -n "scans\|email\|OCR\|approve\|correct\|reject\|RFC 3161" README.md CLAUDE.md`
   and read each hit against the module that would have to implement it.
2. `ls src/constituent_reconciler` and diff against the CLAUDE.md architecture
   map; every file named in the map must exist on disk.
3. Update this table and its date. A capability stays labeled "planned" until
   the code exists.
