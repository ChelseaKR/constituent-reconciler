# Digital Public Goods Standard — conformance note

This note maps constituent-reconciler against the nine indicators of the
[Digital Public Goods Standard](https://digitalpublicgoods.net/standard/). It is
a self-assessment, not a registry submission or an endorsement. Each indicator is
marked met, partial, or planned, with the honest reason. The note is regenerated
on release alongside the responsible-tech audits.

Last verified: 2026-06-27. Recheck cadence: per release, or on a DPG Standard
revision.

## 1. Relevance to Sustainable Development Goals

**Met.** The tool serves human-services nonprofits, supporting SDG 1 (no
poverty), SDG 10 (reduced inequalities), and SDG 16 (access to justice and
effective institutions) by reducing the administrative burden that takes staff
time away from clients. The DV policy pack supports SDG 5 (gender equality) by
giving survivor-serving organizations a tool that fits VAWA and FVPSA
confidentiality.

## 2. Use of an approved open license

**Met.** Apache-2.0, an OSI-approved license on the DPG approved list. See
`LICENSE`.

## 3. Clear ownership

**Met.** Ownership is recorded in `pyproject.toml` (author) and the repository
metadata. The project is a single public repository under one maintainer.

## 4. Platform independence

**Met.** Pure Python (3.12+) with one heavy dependency (Splink, itself open). No
proprietary runtime, no mandatory cloud service. The optional connectors target
open or widely available systems (CiviCRM is open source; Salesforce is
optional). The cloud extraction seam is optional and off by default. A one-command
Docker image (`Dockerfile`) runs the tool anywhere Docker runs.

## 5. Documentation

**Met.** `README.md` for practitioners, `CLAUDE.md` as the build spec,
`docs/ROADMAP.md`, MADR architecture decisions in `docs/decisions/`, a committed
eval report, and this conformance note. Module and public-API docstrings
throughout.

## 6. Mechanism for extracting non-PII data

**Met.** The DV policy pack emits an aggregate, non-identifying summary
(`aggregate_summary.json`) with small-cell suppression, designed precisely so a
non-PII extract can be shared for reporting. Resolved records export to open
formats (CSV) that any tool can read.

## 7. Adherence to privacy and applicable laws

**Met, with the standard reference-implementation caveat.** The DV pack encodes
VAWA and FVPSA confidentiality invariants (no PII egress, consent-gated export,
aggregate suppressed sharing) as merge-blocking tests, grounded in primary
statutory and NNEDV sources cited in `docs/RESPONSIBLE-TECH-AUDITS.md`. It is a
reference implementation, not legal advice; an adopting organization needs its
own review. No real personal data is in the repository; all fixtures are seeded
synthetic.

## 8. Adherence to standards and best practices

**Met.** The project holds a consistent engineering bar: `ruff`, `mypy --strict`,
and `pytest` as merge-blocking CI gates; a committed, regenerated eval report
with Wilson confidence intervals; declared config, connector, and report schema
versions (`reconcile schema`); conventional commits and a Keep a Changelog
history. Actions are SHA-pinned; secret, dependency, SAST, workflow, and
container scans run in CI; the release workflow generates a CycloneDX SBOM and
keyless build-provenance attestation. It has not yet been exercised on a real
`v*` tag, which is reported as an operational evidence gap rather than a missing
implementation.

## 9. Do no harm by design

**Met.** The asymmetry of harm drives the design: the system never auto-merges on
uncertainty and routes every ambiguous decision to a person, because a false
merge can corrupt or expose a record irreversibly. Consent is a technical
invariant, not a checkbox. The DV pack makes data egress structurally impossible
under that mode. Disaggregated synthetic results now quantify the documented
name/address risk classes in `docs/audits/bias-report.md`, including the
transliterated-name and non-Western-order misses. The limits of that small
fixture and of small-cell suppression against cross-tabulation are documented
in `docs/RESPONSIBLE-TECH-AUDITS.md` rather than omitted.

## Summary

All nine indicators are met at the reference-implementation level. The honest
qualifications: indicator 7 carries the not-legal-advice caveat every tool in
this space must, and indicator 8's release path still needs its first real-tag
exercise. Nothing here is a substitute for an organization's own review or a
formal DPG registry assessment.
