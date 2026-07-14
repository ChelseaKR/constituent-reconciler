# 0004 — Address normalization

Status: accepted (v0.4)

## Context

v0.4 adds address as a field the matcher can use. Two records for the same
person often write the address differently ("123 North Main Street" versus
"123 N Main St"), and without standardization that difference reads as a
mismatch. The research named address handling as one of the chained steps, with
a sharp credibility trap attached: real USPS CASS certification requires licensed
USPS data and is legally constrained, so a project that claims certification it
does not have loses the technical audience it wants.

Three questions came with the phase. How to standardize an address deterministically
and offline. Whether to take on libpostal as a dependency. How to add the field
without disturbing the committed eval, which currently shows a clean false-merge
rate on the demo fixtures.

## Decisions

### A vendored, deterministic CASS-style ruleset is the default

`address.py` standardizes an address by upper-casing, dropping punctuation other
than a unit pound sign, and mapping each token through abbreviation tables drawn
from USPS Publication 28 (street suffixes, directionals, secondary unit
designators). The result is a stable matching key: the two writings above both
reduce to "123 N MAIN ST". The standardization is idempotent, which a test
asserts.

It is labeled, in the code and the docs, as **CASS-style and not USPS-certified**.
As shipped in v0.4 the mapping was also position-insensitive (a real CASS engine
distinguishes a leading directional from a trailing one, and "ST" as Street from
"Saint"), which was a documented simplification acceptable for a matching key and
not acceptable to call certification. Overclaiming here was called out in the
research as the fastest way to lose credibility, so the honesty is deliberate.

*Amended 2026-07-02 (E6):* the position-insensitive simplification is retired.
The pass is now position-aware: a directional abbreviates directly after the
leading house number or at the end of the street portion, a street suffix
abbreviates only in the suffix position (leading "ST" stays Saint, "123 AVENUE B"
stays a street named Avenue), and a unit designator maps only when a unit value
follows it. The ruleset is still not USPS-certified and still does not validate
deliverability; that label does not change.

### libpostal is an optional backend, never a requirement

The `postal` Python package binds the libpostal C library, which is a heavy
system dependency (the library plus a large data download). Forcing it on every
install, and into CI, would be a poor trade for a tool whose users run on one or
two IT staff. So libpostal is an optional backend selected by
`address_backend = "libpostal"` in the recipe's `[normalize]` section. Selecting
it without the package installed raises a clear `ImportError` pointing back to
the deterministic default; it never silently falls back, because a silent
fallback would change the matching key without telling anyone. This mirrors how
the pdfplumber extractor and the Bedrock seam are optional in earlier phases: the
deterministic path is the default and the one the committed eval scores.

### Address weights sit below email, and a loose match routes to review

The address comparison is three levels (exact, close by Jaro-Winkler at 0.90,
else). Agreement on a full standardized address is good evidence but weaker than
email, because families and shelter residents share an address and people move.
The exact-level weight (m 0.80) is set below the email level (m 0.85) for that
reason, and the close level is deliberately conservative so an approximate
address match contributes toward review rather than toward an auto-merge.

### The field is available but off by default

Address is added to `CANONICAL_FIELDS`, but a recipe activates only the fields it
maps. The demo recipe does not map address, so the committed eval is byte-for-byte
unchanged, which a CI step verifies. A separate `examples/address-demo/` fixture
and recipe exercise the field end to end, and `tests/test_address.py` covers the
standardization and the pipeline behavior. This keeps the new field from
perturbing the tuned demo while still proving it works.

## Consequences

- `Record.normalized["address"]` carries the standardized form; the connectors
  write that form, which is the desired behavior for an address.
- `normalize_record` now threads an `address_backend` argument from the recipe;
  it also now preserves `Record.spans`, which it had been dropping (a latent v0.3
  bug surfaced while wiring this through).
- libpostal is documented as an optional backend; the default install does not
  pull it in. Since E6 (2026-07-02), a non-blocking CI job builds the pinned
  libpostal release from source on main pushes and a weekly schedule and runs
  the real-library tests; the blocking verify job still runs the deterministic
  path only.
- The abbreviation tables are a subset of USPS Publication 28; extending them is
  additive and does not change existing standardized outputs.
