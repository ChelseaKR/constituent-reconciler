# Generic webhook connector

`connector = "webhook"` pushes resolved, consented records to any HTTP
endpoint that accepts a JSON POST: a Zapier or Make automation, an
organization's own intake API, a notification service, or a queue-fed
receiver. It exists for the destinations that do not warrant (or do not yet
have) a dedicated connector like CiviCRM or Salesforce.

This is the one connector in `connectors/` this project has no vendor
relationship with and cannot test against a real receiver, so read the whole
page, not just the payload shape, before pointing it at a production
endpoint.

## Recipe configuration

```toml
[output]
connector = "webhook"
endpoint = "https://example.org/hooks/constituent-reconciler"
external_id_field = "external_identifier"

# Optional: send a bearer token most receivers expect. The value is read from
# the named environment variable at write time, never stored in the recipe.
auth_env = "WEBHOOK_TOKEN"
auth_header = "Authorization"   # default
auth_scheme = "Bearer"          # default

# Optional but recommended for any real deployment: sign every request body
# with HMAC-SHA256 so the receiver can verify it came from this run and was
# not altered in transit. See "Verifying the signature" below.
signing_secret_env = "WEBHOOK_SIGNING_SECRET"
```

Run it:

```sh
WEBHOOK_TOKEN=... WEBHOOK_SIGNING_SECRET=... \
  constituent-reconcile run --config recipe-webhook.toml --out out
```

`--dry-run` reports what would be sent (`action: would-write`) and makes no
network call, the same as every other network connector in this project.

## Consent, inherited, not reimplemented

`pipeline.export` runs every connector's records through the same consent
lifecycle gate (`consent.partition_by_consent`) before that connector is even
constructed, passing `destination=recipe.output.connector` -- `"webhook"` for
this connector. `models.Consent.reason()` checks, in order, an explicit
revocation, an absent or unrecognized status, a not-yet-effective grant date,
an expired ceiling, and finally scope: if a record's consent was recorded
with a scope naming other destinations only (`[consent] scope` in the
recipe), a status of `"granted"` still is not enough -- the record is
withheld from *this* destination with reason `"out-of-scope"`, by id and
reason only, in `withheld.csv`.

This connector implements none of that itself. It sits at the same choke
point in the pipeline every write destination sits at, so a record consented
for CiviCRM only does not leak out through a newly added webhook just
because both are "granted" in the loosest sense. See
`tests/test_pipeline.py::test_webhook_export_honors_consent_scope_not_just_status`
and `tests/test_no_egress.py`.

## Non-local egress

`WebhookConnector.is_local = False`, the same as CiviCRM and Salesforce.
Under the `dv` policy pack, `pipeline.build_connector` refuses to construct
this connector at all, fail-closed, before any client data is touched:
`PolicyViolation: policy pack 'dv' forbids the non-local write target
'webhook'`. Use a local target (`csv`, `civicrm_csv`, `salesforce_csv`)
under that pack instead. See `tests/test_no_egress.py::test_dv_pack_refuses_the_webhook_target_too`.

## Payload shape

One HTTP `POST` per resolved record (not a batch), `Content-Type:
application/json`, body:

```json
{
  "schema_version": 1,
  "event": "constituent.resolved",
  "external_id_field": "external_identifier",
  "external_id": "C0001",
  "fields": {
    "first_name": "Jane",
    "last_name": "Doe",
    "dob": "1990-01-01",
    "email": "jane@example.org",
    "phone": "555-201-3344",
    "address": "123 N MAIN ST"
  }
}
```

* `schema_version` is this envelope's own version
  (`webhook.WEBHOOK_PAYLOAD_SCHEMA_VERSION`), independent of
  `schema.py`'s `CONNECTOR_INTERFACE_VERSION`. A future breaking change to
  this shape bumps it.
* `external_id` is the cluster id this project assigned the resolved record.
  It is stable across re-runs of the same recipe, so a receiver should upsert
  on it (create-or-update, keyed on `external_id`) the same way the CiviCRM
  and Salesforce connectors upsert on their own side. This connector cannot
  do that upsert itself: a generic endpoint has no "look this id up" contract
  this project can assume, so the receiver owns idempotency.
* `fields` carries only the canonical fields the recipe's `[mapping]` section
  activated and that resolved to a non-empty value for this record -- the
  same "no field this run did not touch" rule the CSV and CRM connectors
  follow. Canonical field names are listed in `models.CANONICAL_FIELDS`:
  `first_name`, `last_name`, `dob`, `email`, `phone`, `address`.

### Response handling

Only the HTTP status code determines success: `2xx` is `written`, anything
`>= 400` raises `ConnectorError` (which stops the run's export step) with up
to 200 characters of the response body for diagnosis. This project does not
require a receiver to return any particular body or shape, since "generic"
means the receiver was never in this project's control; a receiver's own
integration guide governs what it returns, not this document.

### Malformed configuration is rejected before any network call

`endpoint` must be an `http://` or `https://` URL; anything else (empty
string, a bare hostname, a `file://` path, a typo'd scheme) raises
`ConnectorError` at connector construction time, before the pipeline attempts
a single write. This mirrors the fail-closed posture the rest of the project
takes toward misconfiguration (an unrecognized policy pack, an unmapped
required field) rather than sending a partial run's records to nowhere and
discovering the mistake in a support ticket.

## Verifying the signature

When `signing_secret_env` is set, every request carries:

```
X-Reconciler-Signature: sha256=<hex-encoded HMAC-SHA256 of the raw request body, keyed on the shared secret>
```

A receiver in Python:

```python
import hashlib
import hmac

def verify(secret: str, body: bytes, header_value: str) -> bool:
    algo, _, digest = header_value.partition("=")
    if algo != "sha256":
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest)
```

Compute the HMAC over the exact raw bytes received (before any JSON
re-serialization, which can reorder keys or change whitespace and invalidate
the signature).

## What this connector deliberately does not do

* **No batching.** One record, one POST, one `WriteResult`. This keeps
  success/failure semantics per-record and matches the CiviCRM and
  Salesforce connectors; a receiver that wants batching should batch on its
  own side (a queue in front of the real endpoint) rather than this project
  guessing at a batch contract no vendor defined.
* **No retry.** A failed POST raises `ConnectorError` and stops the export
  step for that run; re-running `constituent-reconcile run` resends every record (or
  `constituent-reconcile apply` after a review pass). A receiver that wants at-least-once
  delivery semantics under partial failure should be idempotent on
  `external_id`, which this connector always sends.
* **No inbound receiver.** This module only sends. It does not open a
  listening HTTP server to accept incoming payloads. The reconciliation
  pipeline's only intake path today is `[input] incoming`/`existing` (a CSV,
  a folder of CSV/PDF/text/email sources -- see `pipeline._ingest_source`);
  this project has no pluggable-source concept parallel to `connectors/`
  yet, so "accept a webhook as a data source" is a different, larger feature
  (a new ingestion path, not this connector) and is out of scope here. The
  roadmap's E3 item groups "generic webhook" with Apricot, Airtable, and
  Sheets as write *connectors* (see the architecture diagram in
  `docs/ROADMAP.md` and README's "Write" step); this module implements that
  reading of the item.
