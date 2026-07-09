# Security policy

## Reporting

Preferred: use [GitHub private vulnerability reporting](https://github.com/ChelseaKR/constituent-reconciler/security/advisories/new)
for this repository (Security tab -> Report a vulnerability). If that is not
available to you, report privately by email to
[ckellyreif@gmail.com](mailto:ckellyreif@gmail.com) rather than opening a
public issue. Include what you found, how to reproduce it, and the impact you
see.

**Acknowledgement SLA:** within 3 business days. If a report is confirmed as a
real vulnerability, expect a fix or mitigation plan communicated within 14
calendar days given the solo-maintainer scale of this project; critical,
actively-exploitable findings are prioritized ahead of that window.

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
keeps inference offline so that PII does not egress; the threat model for the
untrusted-document parse path is documented in
[`docs/THREAT-MODEL.md`](docs/THREAT-MODEL.md).
