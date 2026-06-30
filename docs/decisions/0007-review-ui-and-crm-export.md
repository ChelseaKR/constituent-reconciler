# 0007 — Web review UI and import-ready CRM export

Status: accepted (v0.7)

## Context

Two gaps remained before the 1.0 tag that this release closes. The review queue
was a CSV a reviewer hand-edited, which the roadmap always named as a stopgap for
the WCAG 2.2 AA web UI that is the product's differentiator. And the write step
could push to a CRM over the network or write a generic resolved CSV, but it
could not produce a file shaped for a CRM's own import tool, which is the path an
organization uses before it trusts a live API push, and the only path available
to an org that keeps its data off the network.

Both features had to hold the project's two hard lines: offline-first by default,
and the DV policy pack's no-egress and minimization posture.

## Decisions

### The review UI is a stdlib loopback server, not a web framework

The server is `http.server` over a `ReviewSession`, with no Flask, FastAPI, or
ASGI dependency. This matches the standing rule that everything around the
matcher stays on the standard library, and it keeps the install weight and the
supply-chain surface unchanged. The serving model is a single-user local tool: a
reviewer runs `reconcile review`, a browser opens to a loopback URL, and the
process serves until Ctrl-C. It is not a multi-tenant web app and does not try to
be one, because the user is one operator on one machine reconciling one batch.

Request handling is split from socket binding. `handle_get` and `handle_post`
take a session and return a status and a body, so the routing and the rendering
are unit-tested with no socket; `build_server` binds a real loopback socket for
the end-to-end test and the CLI. Decisions write through to `decisions.json` on
every verdict, so closing the browser loses nothing and a re-launch resumes.

The output is the same `decisions.json` that `reconcile apply` already consumes
(approved and rejected lists of record-id pairs). The web step therefore replaces
the hand-edited CSV without changing the rest of the pipeline: review, then
`apply`, then re-resolve, exactly as before.

### No egress and minimization are enforced, not asserted in prose

Three concrete properties carry the privacy posture:

* **Loopback only.** The server binds `127.0.0.1` by default, and under a policy
  pack that requires local targets (the DV pack) a non-loopback host is refused
  before the socket is bound, fail-closed, mirroring the connector local-target
  gate. A test asserts the refusal and asserts a real bind lands on loopback.
* **No external assets.** The CSS and the small progressive-enhancement script
  are inlined; the page fetches nothing from a CDN or a network, so it works with
  no connection and cannot beacon out.
* **Minimization of what is persisted.** The only artifact the review step writes
  is `decisions.json`, which carries record ids and verdicts and no field value,
  the same id-and-reason-only discipline `withheld.csv` follows. The server also
  suppresses request logging, since a path can carry a pair id.

The displayed records are not redacted. The authorized local reviewer has to see
both records to judge a merge, so minimization is applied to what is transmitted
and persisted, not to what the operator reads on their own screen. That is the
honest scope of the claim and is stated as such.

### Accessibility is built in, to the WCAG 2.2 AA bar the project commits to

The comparison is a real table with scoped headers; status is carried by a text
label and a symbol, never colour alone; the decision controls are ordinary form
buttons that work with no JavaScript, so a keyboard reviewer completes a pass with
Tab and Enter. The script only adds single-key shortcuts on top of controls that
already work. A full axe audit and a screen-reader walkthrough remain a REVIEW
gate in the metrics ledger; this release lands the structural AA work.

### CRM export is the offline-first default; the live push stays opt-in

The new `salesforce_csv` and `civicrm_csv` connectors write a CSV whose columns
are the target CRM's import field names, plus the external-id column keyed on the
cluster id. An org loads the file with the Salesforce Data Import Wizard or Data
Loader, or CiviCRM's Import Contacts, and upserts on that external id, so a re-run
updates rather than duplicates, the same idempotency the live connectors give.

The column schema comes from the same field maps the live connectors use:
`salesforce_csv` imports `salesforce.FIELD_MAP` directly, and `civicrm_csv` uses
a dedicated `civicrm.IMPORT_FIELD_MAP` because CiviCRM's import columns
(`email`, `phone`, `street_address`) differ from the API v4 join syntax
(`email_primary.email`) the live push needs. Sharing the map means the file an
org imports and the payload the API would push describe the same mapping and
cannot drift.

These are local-file targets, so `is_local` is True and the DV pack permits them
while still refusing the network push. That is the point: a victim-service org
can produce a CRM-shaped import file on its own machine and load it through its
CRM's own controls, without this tool ever opening a network connection.

## Consequences

- `reconcile review` is a new CLI command; the CSV review queue is still written
  by `run` for offline editing, so neither workflow is removed.
- Two new connector names, `salesforce_csv` and `civicrm_csv`, are additive under
  the schema-stability contract (ADR 0006): the `Connector` interface is
  unchanged, so `CONNECTOR_INTERFACE_VERSION` stays 1.
- The review UI's accessibility is structurally AA now; the axe-clean and
  screen-reader-walkthrough gates in the metrics ledger stay open until run.
- The web server is exercised end to end over a loopback socket in CI; it needs
  no external service, so it runs in the standard test job.
