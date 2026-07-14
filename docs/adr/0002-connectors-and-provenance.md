# 0002 — Connector interface and provenance log

Status: accepted (v0.2)

## Context

v0.2 adds the thing v0.1 deliberately left out: writing resolved records back
into the system an organization already runs, which the research named the
"write-back wall." Two questions came with it. How should destinations be
structured so adding one does not destabilize the others, and how should a write
be recorded so an organization can later prove what was written and under which
consent.

## Decisions

### Connectors behind one interface

Destinations implement a single `Connector` protocol with one method,
`write_all`. The CSV writer was refactored onto it alongside the new CiviCRM
connector. Each destination is one module, isolated from the others, so the
ongoing tax of a destination's API churn stays contained. This mirrors the
source-adapter pattern used elsewhere in the author's portfolio.

CiviCRM is the first real destination because it is fully open and self-hostable,
which makes a clean end-to-end demo possible without a vendor relationship. The
write is an upsert keyed on an external identifier (the cluster id): the
connector looks the contact up by that key and updates it if present, creates it
if not. Idempotency on a stable key is what makes a re-run safe.

HTTP goes through an injected `Transport`. The default uses the standard library
(no third-party HTTP dependency). Tests inject a fake transport, so request
construction, the upsert branch, dry-run behavior, and error handling are all
covered without a live CiviCRM.

### Append-only provenance with a pluggable timestamp authority

Each real write appends one entry to a JSONL log. An entry carries a BLAKE2b hash
of the written field values and the hash of the previous entry, so the entries
form a chain. Altering or deleting a past entry breaks every entry after it, and
`verify_log` (exposed as `reconcile verify`) detects it. This gives a
tamper-evident record of what was written, when, and under which consent.

Time is supplied by a `TimestampAuthority`. The default `LocalClockAuthority` is
honest about being only as trustworthy as the machine. The interface is the
point: a production deployment can plug in an RFC 3161 trusted-timestamp
authority for third-party non-repudiation. v0.2 does **not** claim to do RFC 3161
already; doing it correctly means an ASN.1 request, a TSA round trip, and token
verification, which is a later hardening step. Shipping the chain now and the TSA
as a seam is the honest split, and it matches how the cloud extraction seam was
deferred in v0.1.

## Consequences

- Consent is enforced in the pipeline before a connector is constructed, so a
  connector never sees a withheld record and never has to reason about consent.
- The provenance log chains across runs: a second run's entries link onto the
  first run's last hash.
- Secrets stay out of the recipe. The CiviCRM API key is read from an environment
  variable named by the recipe, never stored in it.
- "RFC 3161 timestamps" in the architecture diagram is the target authority, not
  a v0.2 claim; the metrics and audit docs say so plainly.
