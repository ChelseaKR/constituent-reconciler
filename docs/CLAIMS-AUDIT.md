# Capability claims audit

**Audited:** 2026-07-02 (FIX-12).
**Scope:** every capability claim in `README.md` and `CLAUDE.md`, read against
the code in `src/constituent_reconciler/`.
**Method:** each claim below was checked by opening the named module, not by
trusting a docstring. Line numbers are as of the audit date; section names are
given so the row survives line drift. The README text cited here includes the
docs corrections dated 2026-07-02 (scans and email bodies labeled planned, the
review verdicts stated as approve and reject), which land in the same release
as this table.

Status values: **implemented** (the code does what the sentence says),
**planned** (the docs label it as not yet built, and the code confirms that),
**corrected** (the docs overstated the code and were fixed in this audit).

| Claim | Where stated (file:line) | Code location | Status |
| --- | --- | --- | --- |
| Ingest reads a folder (or single file) of CSVs | README.md:57 ("What it does" step 1); CLAUDE.md "What this is" | `pipeline.py` `_ingest_source` routes `.csv` to the structured reader | implemented |
| Ingest reads digitally created (text-layer) PDFs | README.md:57 (step 1); README status note | `pipeline.py` `_ingest_source` routes `.pdf` to the extractor; `extract/pdf.py` `PdfplumberExtractor` | implemented (text layer only) |
| Scanned-document (OCR) ingest | README.md:57-59 labels it planned; CLAUDE.md:9 formerly claimed it as present | none yet; `extract/pdf.py` has no OCR path | planned (EXP-04, docs/ideation/03-expansions.md); CLAUDE.md corrected 2026-07-02 |
| Email-body ingest | README.md:57-59 labels it planned; CLAUDE.md:9 formerly claimed it as present | none yet; `_ingest_source` handles only `.csv` and `.pdf` | planned (EXP-08); CLAUDE.md corrected 2026-07-02 |
| Review verdicts: approve and reject | README.md:5, 70-73 (step 5), 181 | `review/session.py` `APPROVED`, `REJECTED`, `_VERDICTS`; served by `review/server.py` | implemented |
| Review verdict: correct (fix field values during review) | README.md:70-73 labels it planned; the old text promised "approve, correct, or reject" | none yet | planned (EXP-01); README corrected 2026-07-02 |
| Local CSV connector (default) | README.md:74-76 (step 6) | `connectors/csv_out.py`; `pipeline.py` `build_connector` | implemented |
| Import-ready CRM export files (`salesforce_csv`, `civicrm_csv`) | README.md:254-258 | `connectors/crm_csv.py` | implemented |
| CiviCRM live write-back (API v4 upsert) | README.md:74-76, 272-283 | `connectors/civicrm.py` | implemented |
| Salesforce live write-back (REST upsert, NPSP Contact) | README.md:74-76, 285-292 | `connectors/salesforce.py` | implemented |
| Airtable, Sheets, and webhook connectors | README.md:75-76 labels them "to follow" | none yet | planned |
| Policy packs: default, dv, hipaa | README.md:89 (the dv pack); CLAUDE.md architecture map | `policy.py` `_PACKS` defines exactly `default`, `dv`, `hipaa` | implemented |
| DV pack: cloud seam fused off, local write targets only, fail-closed | README.md:96-99; CLAUDE.md ground rules | `extract/seam.py` (packs that forbid cloud calls), `pipeline.py` `build_connector` (refuses non-local targets), `tests/test_no_egress.py` | implemented |
| Optional Bedrock (Claude) extraction seam, policy-gated, low-confidence pages only | README.md:60-63 (step 2) | `extract/seam.py` `BedrockSeam` and its disabled fallback | implemented (requires boto3 and AWS credentials at deploy time) |
| Append-only, tamper-evident provenance log (BLAKE2b hash chain, verifiable) | README.md:77-79 (step 7), 294-300 | `provenance.py` `content_hash`, entry chaining, `verify_log`; `reconcile verify` in `cli.py` | implemented |
| RFC 3161 trusted timestamps on provenance entries | README.md:78 (step 7); CLAUDE.md formerly listed "RFC 3161 timestamps" in the module map | `provenance.py` `TimestampAuthority` defaults to the local clock; RFC 3161 is a pluggable interface, not shipped | planned (roadmap R2); CLAUDE.md corrected 2026-07-02. README step 7 still names the timestamp without the "pluggable" caveat and should soften it or land R2 |
| Consent-gated export: non-consented records withheld, fail-closed | README.md:74, 92-95 | `consent.py` `partition_by_consent`; `pipeline.py` writes `withheld.csv` with ids and reason only | implemented (enforced when the active pack requires consent: dv and hipaa; the default pack does not) |
| CASS-style address standardization, not USPS-certified | README.md:64-66 (step 3) | `address.py` | implemented |
| CLAUDE.md architecture map matches the code | CLAUDE.md:72-108 formerly listed `ingest.py`, `gate.py`, `resolve.py`, `extract/deterministic.py`, a `policy/` package, and `test_consent_blocks_export.py`, none of which exist | as built: `pipeline.py` (ingest lives here), `decisions.py` (banding and the fail-closed gate), `matching.py` (Splink wrapper), `extract/pdf.py`, `policy.py`, `tests/test_consent.py` | corrected 2026-07-02 |

## Re-running this audit

Before a release:

1. `grep -n "scans\|email\|OCR\|approve\|correct\|reject\|RFC 3161" README.md CLAUDE.md`
   and read each hit against the module that would have to implement it.
2. `ls src/constituent_reconciler` and diff against the CLAUDE.md architecture
   map; every file named in the map must exist on disk.
3. Update this table and its date. A capability stays labeled "planned" until
   the code exists.
