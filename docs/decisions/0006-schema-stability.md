# 0006 — Schema and interface stability

Status: accepted (v0.6)

## Context

The v1.0 milestone in the roadmap commits to semantic-versioning guarantees on
three surfaces: the recipe config schema, the connector interface, and the JSON
report artifacts. A guarantee is only meaningful if the surface it covers is
named and versioned, so a consumer knows what "no breaking change" refers to.
This decision names the three surfaces, declares their versions, and states the
contract. It does not yet declare 1.0: that tag is gated on the pipeline proving
out against more than one real organization, which is an adoption fact, not a
code change. Declaring 1.0 stability before that would be the same kind of
overclaim the project refuses elsewhere (CASS certification, a complete HIPAA
mode).

## Decisions

### Three versioned surfaces, declared as integers

`schema.py` declares `CONFIG_SCHEMA_VERSION`, `CONNECTOR_INTERFACE_VERSION`, and
`REPORT_SCHEMA_VERSION`, each an integer bumped independently of the package
version. `reconcile schema` prints them, and `REPORT_SCHEMA_VERSION` is stamped
into `aggregate_summary.json` so a consumer reading the file knows its shape.

* **Config schema** — the recipe TOML: the sections (`input`, `mapping`,
  `consent`, `thresholds`, `policy`, `normalize`, `extract`, `output`), their
  keys, and their meaning.
* **Connector interface** — the `Connector` protocol: the `write_all` signature,
  the `WriteResult` shape, and the `is_local` attribute the policy gate reads.
* **Report schema** — the JSON artifacts: `aggregate_summary.json` and the
  provenance log entry shape.

### The contract

Once the project reaches 1.0, a breaking change to any of these surfaces bumps
the package MAJOR version and ships a migration note in the CHANGELOG. Before
1.0, a surface may change with a MINOR bump and a CHANGELOG entry. Adding an
optional recipe key, a new connector, or a new field to a JSON artifact is
additive and not breaking; removing or renaming a key, changing a default that
alters results, or changing the meaning of an existing field is breaking.

### Why declare this at v0.6 rather than waiting for 1.0

The surfaces are stable enough now that consumers are starting to depend on
them, and naming them is what lets the next two releases demonstrate "no breaking
change for two consecutive releases," which is one of the 1.0 gates. Declaring
the versions early is the mechanism by which the 1.0 stability claim can later be
made honestly.

## Consequences

- `schema.py` is the single source of the three versions; `reconcile schema`
  exposes them and `aggregate_summary.json` carries `schema_version`.
- The 1.0 tag stays gated on real-organization validation; v0.6 ships the
  engineering deliverables of the 1.0 milestone (a second connector, Docker
  self-host, the DPG conformance note, these version declarations) without
  claiming the stability commitments that depend on adoption.
- A change to any named surface now has a clear, documented versioning rule, so a
  future breaking change is a deliberate, recorded act rather than a silent one.
