# Airtable connector

Status: **implemented 2026-07-22.** `connectors/airtable.py` implements the
design below with an injected transport, native `performUpsert`, batches of at
most ten records, one `WriteResult` per record, and `is_local = False`.
`examples/intake-demo/recipe-airtable.toml` is the runnable recipe shape.
Adapter and connector-conformance tests use recorded contract shapes without
contacting Airtable. A live throwaway-base exercise remains external evidence,
not a prerequisite for testing request construction.

## Implementation boundary

The connector is write-only, like the existing CiviCRM and Salesforce
adapters. The operator provisions a table whose writable field names are the
canonical reconciler fields and whose external-id field matches
`[output].external_id_field`. The full Airtable table endpoint goes in
`[output].endpoint`; a personal access token is read from the environment named
by `[output].auth_env`. The connector does not create or migrate a base schema.

## Auth model

Bearer-token based, two token types:

* **Personal Access Tokens (PATs)**: self-serve, created at
  `airtable.com/create/tokens`, scoped per-capability (e.g.
  `data.records:write`) with explicit base/workspace access grants. This is
  the natural fit for this project's `auth_env`-reads-a-token-from-the-shell
  pattern (the same shape `CIVICRM_API_KEY`/`SF_TOKEN` already use).
* **OAuth2**: for third-party integrations acting on behalf of other Airtable
  users, registered at `airtable.com/create/oauth`. Not needed for a
  single-org connector the way this project's other connectors work
  (operator-configured credential, not a multi-tenant integration).

Legacy API keys were deprecated February 1, 2024 -- any design must use PATs
or OAuth2, not the older key scheme some third-party tutorials still show.

Source: <https://airtable.com/developers/web/api/authentication>.

## Rate limits

* **5 requests/second per base.**
* **50 requests/second aggregate per user/service-account token**, across
  all bases that token can access.
* Exceeding the per-base limit returns HTTP 429 and requires a **30-second**
  cooldown before further requests to that base succeed again.
* Airtable reserves the right to throttle `performUpsert` requests
  (see below) differently from standard read/write requests.

Source: <https://airtable.com/developers/web/api/rate-limits>.

Design implication: a connector writing many records to one base at the
default `write_all` per-record-or-per-batch cadence this project's other
connectors use could hit the 5 req/s-per-base ceiling on a large run. Unlike
CiviCRM/Salesforce (where this project has not needed backoff logic in
practice), an Airtable connector should batch writes (see below) and honor a
429 with the documented 30-second cooldown rather than retrying immediately
-- new logic this project's other connectors have not needed yet.

## Pagination (reads)

Offset-based (not the JWT/cursor style some APIs use): `listRecords` returns
up to `pageSize` records (default and max **100**); if more remain, an
opaque `offset` string is returned to pass into the next request. No
`offset` in the response means no more pages. An optional `maxRecords` caps
the total across all pages.

Source: <https://airtable.com/developers/web/api/list-records>.

Not directly relevant to a write-back connector (this project's connectors
only write resolved records; they do not read Airtable back), but relevant
if Airtable is later considered as an intake *source* too (a separate,
larger feature -- this project has no pluggable-source concept today; see
`docs/connectors/webhook.md`'s "what this connector deliberately does not
do" section for why that is out of scope here).

## Upsert support -- real, native, and the best fit of the three

Unlike CiviCRM (this project implements its own get-then-create-or-update)
and unlike the assumption for Apricot and the confirmed absence for Sheets
(both below), Airtable has a **native upsert** on its update-multiple-records
endpoint: `PATCH` with a top-level `performUpsert: { fieldsToMergeOn: [...]
}` object naming 1-3 field names/IDs (restricted to non-computed field
types: number, text, long text, single/multiple select, date).

* Records without an `id` are matched against `fieldsToMergeOn`: zero
  matches creates, one match updates, **multiple matches fails the whole
  request** (no partial success -- a design detail this project's own
  upsert connectors do not have to reason about, since CiviCRM/Salesforce
  upsert on a project-assigned cluster id that is unique by construction).
* Records that do include an `id` bypass merge-matching and update-or-fail
  (never create).

This is the closest fit to how this project's `civicrm`/`salesforce`
connectors already upsert on the resolved cluster id as the external key:
an Airtable connector would set `fieldsToMergeOn: ["external_identifier"]`
(or whatever field the recipe's `external_id_field` maps to) and send the
cluster id in that field on every record, the same idempotency contract the
other two live connectors already promise.

Sources: <https://airtable.com/developers/web/api/update-multiple-records>;
community confirmation
<https://community.airtable.com/development-apis-11/new-beta-rest-api-upserts-5313>;
worked examples <https://github.com/Airtable-Labs/upsert-examples>.

## Record shape and batch limits

REST/JSON. Create/update/delete batch endpoints (including the upsert
`PATCH`) are capped at **10 records per request** -- confirmed on Airtable's
own docs and reproduced in third-party tool error messages ("A maximum of
10 records can be created per request").

Sources: <https://airtable.com/developers/web/api/create-records>;
<https://community.zapier.com/troubleshooting-99/could-not-create-records-in-airtable-a-maximum-of-10-records-can-be-created-per-request-but-you-have-provided-11-27482>.

Design implication: unlike this project's per-record `write_all` loop in
`civicrm.py`/`salesforce.py`/`webhook.py`, an Airtable connector should batch
records into groups of at most 10 per request, and should report one
`WriteResult` per record (matching this project's contract) by fanning the
per-batch response back out to the records it covered, rather than one
`WriteResult` per batch call.

## Unusual prerequisites

None beyond a base ID and a scoped PAT. One unconfirmed detail: some
third-party docs cite a **1,000 API calls/month cap on Airtable's Free
plan**, but this was not confirmed on an official Airtable page in this
research pass -- verify against Airtable's current plan-comparison page
before relying on it, since a per-plan monthly cap (versus the per-second
rate limits above, which appear plan-independent) would matter for choosing
which Airtable plan a pilot organization needs.

## is_local classification

`False`. Airtable is a hosted SaaS with no self-host option; every write
leaves the machine, so the `dv` policy pack must refuse this connector the
same way it refuses `civicrm`/`salesforce`/`webhook`
(`tests/test_no_egress.py` pattern).

There is no documented programmatic bulk-export endpoint equivalent to
CiviCRM's/Salesforce's import-file connectors; Airtable does support manual
per-table CSV export via its UI, which could seed an offline companion
connector, but (unlike Apricot's Data Archives/SQL-export tooling) this is
not a documented API surface this project could automate against today.

## Recommendation

Of the three remaining vendors, build this one first if/when prioritized:
real public docs, a native upsert that matches this project's existing
idempotency contract almost exactly, and no plan-tier gating blocking API
access (unlike Apricot). The only missing piece is a throwaway Airtable base
and PAT to build and test the `Transport`-injected connector against, the
same way CiviCRM and Salesforce were built.

## Open questions for a human decision

* Which base schema should the connector target -- does the pilot
  organization (if/when one exists, per `docs/ROADMAP.md`'s pilot-readiness
  gate) already have an Airtable base with a `first_name`/`last_name`/etc.
  shape, or does this project need to define a canonical Airtable table
  schema the way `civicrm.py`'s `IMPORT_FIELD_MAP` does for CiviCRM?
* Confirm the Free-plan monthly call cap (or its absence) before
  recommending a plan tier to a pilot organization.
