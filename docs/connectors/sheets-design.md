# Google Sheets connector — design brief, not implementation

Status: **research only, not built.** Part of roadmap item E3 ("More
connectors: Apricot, Airtable, Sheets, generic webhook"). The generic webhook
connector shipped (see `docs/connectors/webhook.md`); this vendor connector
did not.

## Why this is a brief, not code

Google's Sheets API v4 is fully public and precise, and a service account
(no interactive OAuth flow) is a natural fit for this project's
environment-variable-credential pattern. The blocker is the same as
Airtable's: this project has no Google Cloud project, service account, or
test spreadsheet to build and test a `Transport`-injected connector against,
and (unlike Airtable) the write model here is different enough from this
project's existing upsert-by-external-id contract that it deserves a
deliberate design decision, not just credentials, before implementation.

## Auth model

OAuth2 (3-legged, for a human-authorizing-a-connector scenario) or a
**service account** (JSON key file issued from Google Cloud Console, no
user interaction) -- the service-account path is the realistic one for a
server-side connector matching this project's `auth_env`-reads-a-secret
pattern (the recipe would name an env var holding a path to, or the
contents of, a service-account key, not a bearer token string the way
CiviCRM/Salesforce/webhook do).

Relevant scopes: `.../auth/spreadsheets` (full read/write; Google classifies
this as sensitive) or `.../auth/spreadsheets.readonly`. If the connector
needs to *create* new spreadsheets rather than write into an
operator-provisioned one, it also needs a Drive scope
(`drive.file` is the narrowest that still allows creating files; `drive` is
broad; `drive.readonly` is read-only).

Source: <https://developers.google.com/workspace/sheets/api/scopes>.

## Rate limits

Per-minute quotas, refilled every 60 seconds, read and write tracked
**separately**:

* 300 read requests/minute/project
* 60 read requests/minute/user/project
* 300 write requests/minute/project
* 60 write requests/minute/user/project

Exceeding a quota returns HTTP 429; Google recommends exponential backoff.
No documented daily cap as long as per-minute quotas are respected.
Requests taking longer than 180 seconds time out.

Source: <https://developers.google.com/workspace/sheets/api/limits>.

## Pagination

**No cursor/offset pagination for reads.** Access is range-based (A1
notation, e.g. `Sheet1!A1:D`), returning a `ValueRange` with a 2D `values`
array (row-major by default, or column-major via `majorDimension`).
Trailing empty rows/columns are omitted from the response. No documented
hard cap on rows/cells per single `get`/`batchGet` beyond Google Drive's
general file-size limits.

Source: <https://developers.google.com/sheets/api/guides/values>.

Not directly relevant to a write-back connector for the same reason noted
in the Airtable brief -- this project's connectors write, they do not read
a destination back -- but it matters if Sheets were later considered as an
intake source too, which is out of scope here (see
`docs/connectors/webhook.md`'s note on why an inbound path is a separate,
larger feature this project does not have a pluggable-source concept for
yet).

## Upsert support -- none native; this is the important design fork

Unlike Airtable (native `performUpsert`) and unlike Salesforce/CiviCRM
(this project already implements a get-then-create-or-update pattern for
CiviCRM), **the Sheets API has no upsert or find-or-create operation at
all.** `spreadsheets.values.append` only appends after the last row of a
detected table (`insertDataOption`: `OVERWRITE` or `INSERT_ROWS`); it does
not match on a key column. Official docs and community sources agree the
API has no built-in upsert.

Sources:
<https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/append>;
<https://developers.google.com/sheets/api/guides/values>.

**Design implication -- this is the real engineering decision a future
implementer has to make, not just plumbing:** any find-or-create-by-cluster-id
logic has to be implemented client-side in the connector:

1. Read the target range (or a key column) with `values.get`.
2. Scan the returned rows client-side for a match on the external-id column
   (the resolved cluster id, the same key every other connector upserts on).
3. If found, `values.update` that specific row's range.
4. If not found, `values.append` a new row.

This is architecturally closer to CiviCRM's pattern (`connectors/civicrm.py`:
a `get` call, then a branch to `create` or `update`) than to Salesforce's or
Airtable's single-call native upsert -- except CiviCRM's `get` is a
server-side filtered query, while Sheets' `get` has to pull a whole range
and scan it client-side, since there is no server-side "find by column
value" query in the Sheets API. For a sheet with many rows this means either
reading the whole key column on every run (acceptable for the record volumes
this project's demo fixtures use -- dozens, not tens of thousands, of rows)
or maintaining an in-memory or cached row-index the connector builds once
per `write_all` call rather than once per record, to avoid an O(n) scan per
record.

## Record shape and batch limits

REST/JSON `ValueRange` objects -- **arrays of arrays**, not a schema'd
JSON-object-per-row the way Airtable and Salesforce return/accept records.
This means a Sheets connector needs an explicit column-order mapping
(column index -> canonical field name), the same role `civicrm.py`'s
`IMPORT_FIELD_MAP` or `salesforce.py`'s `FIELD_MAP` play, but keyed on
column position instead of a named API field.

`spreadsheets.values.batchUpdate` supports multiple discontinuous ranges in
one call; each batch subrequest counts individually against the per-minute
quota above (not free-of-charge as one call).

Source: <https://developers.google.com/workspace/sheets/api/limits> ("Each
batch request, including any subrequest, is counted as one API request").

## Unusual prerequisites

None beyond enabling the Sheets API on a Google Cloud project and sharing
the target spreadsheet with the service account's email address -- both
well-documented, standard steps, no plan-tier gating (unlike Apricot).

## is_local classification

`False`. Sheets is a hosted Google service; every write leaves the machine,
so the `dv` policy pack must refuse this connector the same way it refuses
the others.

Real offline/export alternative, and a simple one: direct CSV export via
`https://docs.google.com/spreadsheets/d/{id}/export?format=csv`
(unauthenticated for a publicly-shared sheet, or via the Drive API's
`files.export` for a private one). This exports only the active
sheet/tab per request -- a spreadsheet with multiple tabs needs one export
call per tab.

Source: <https://dev.to/googleworkspace/import-csv-to-google-sheets-without-the-sheets-api-20g1>.

This is a plausible basis for a lightweight companion "read a shared sheet"
path, though (unlike Apricot's Data Archives tool) it is a read/export path,
not a write-in path -- it does not help with the upsert problem above, which
is specific to writing resolved records back into a live sheet.

## Recommendation

Build this after Airtable, not before, if/when prioritized: the missing
native upsert means the connector is more code (a read-scan-then-write loop
plus a column-index field map) than Airtable's near-drop-in fit to this
project's existing upsert contract, for a destination (a spreadsheet) that
is arguably a weaker "system of record" fit for a nonprofit's constituent
data than a named CRM in the first place.

## Open questions for a human decision

* Does a pilot organization actually want live write-back into a Sheets
  document that stays open and edited by staff, given the client-side
  scan-for-a-match approach above has an inherent race condition if someone
  edits the sheet between this connector's read and its write? CiviCRM and
  Salesforce do not have this problem because the vendor's own upsert
  endpoint is atomic; a Sheets connector's read-then-write is not, and that
  gap needs a documented, counsel-reviewed-if-relevant answer before this
  connector reaches a `dv`-adjacent or otherwise sensitive deployment (it
  would be refused under `dv` regardless, being non-local, but the same
  race condition could matter for non-DV consent-tracked data too).
* Service-account key handling: how does the recipe reference a
  service-account JSON key without ever storing it in the recipe file
  itself, matching this project's "secrets come from the environment, never
  the recipe" rule (`config.py`'s `OutputConfig` docstring)? A single env var
  holding a file path, versus one holding the JSON blob inline, is a real
  design choice with different operational tradeoffs.
