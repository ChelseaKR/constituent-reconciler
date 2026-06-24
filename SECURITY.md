# Security policy

## Reporting

Report a vulnerability or a data-exposure concern privately by email to the
maintainer rather than opening a public issue. Include what you found, how to
reproduce it, and the impact you see. You can expect an acknowledgement within a
few days.

Please do not post intake data, exports, or any real personal data in a report.
A minimal synthetic reproduction is enough.

## Supported versions

This project is pre-1.0. Fixes land on the latest minor release. There is no
backport guarantee until 1.0.

## Threat surface

The tool reads untrusted files (CSVs today; PDFs and scans once extraction
lands) and writes to a case system. The concerns it takes seriously:

- **Parsing untrusted input.** Malformed CSVs must fail with a clear error, not
  a crash that leaks a stack trace with data in it. Document parsing, when it
  arrives, runs in a hardened path.
- **Consent enforcement.** Under a consent-required policy, a record without
  granted consent must never appear in an export or in logs. This is enforced by
  tests, not by review alone.
- **Data minimization in artifacts.** The `withheld.csv` file lists ids and a
  reason, never field values, so the record of what was withheld does not itself
  leak the data.

## Out of scope for v0.x

Network hardening, multi-tenant isolation, and authentication are out of scope
while the tool runs locally against an operator's own data. The DV policy pack
keeps inference offline so that PII does not egress; a later release documents
the full threat model in `docs/`.
