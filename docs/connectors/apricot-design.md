# Apricot (Bonterra) connector — design brief, not implementation

Status: **externally blocked, not scheduled (confirmed 2026-07-22).** No
connector will be implemented until an authorized Apricot customer supplies a
test account and the vendor supplies a verifiable write contract. This is a
fail-closed product decision: endpoint paths, authentication, and upsert
semantics will not be guessed from partial public material.

## Why this is a brief, not code

Two blockers, independent of engineering effort:

1. **No credentials, no test account.** This project has no Bonterra/Apricot
   customer relationship or API access to build and test against. Every
   existing connector in this project (CiviCRM, Salesforce) was built with an
   injected `Transport` so request construction is unit-testable without a
   live server -- but the *shape* of that construction (endpoint paths,
   auth headers, upsert semantics) still has to come from real docs or a
   real account, and Apricot's is unusually thin publicly.
2. **Apricot's API access is plan-gated**, confirmed below -- so even a
   future implementer with a Bonterra relationship may not have API access
   without an upsell. That is a customer-facing fact worth surfacing before
   anyone budgets engineering time on this.

## What is confirmed

**Plan-tier gating.** API access is bundled into **Apricot Enterprise** by
default. **Apricot Pro** customers must purchase an add-on ("Data
Integration") to unlock API access and automated SFTP imports. **Apricot
Essentials does not get API access at all.**
Source: Bonterra's own packages FAQ,
<https://intercom.help/Bonterra-Apricot/en/articles/11475052-apricot-packages-and-add-ons-essentials-pro-and-enterprise>
(indexed snippet; the Intercom-hosted page itself 404s to a direct
unauthenticated fetch -- it is JS-rendered/bot-restricted, so this and the
citations below rely on search-engine-cached snippets, not a page this
project could re-fetch and re-verify at will). Corroborated by Bonterra's
public pricing page, <https://www.bonterratech.com/pricing/case-management>.

**API integration is described as custom, bidirectional.** Bonterra's own
FAQ characterizes "API Integration" as "a custom integration between Apricot
and a third-party system via API" for bidirectional sync, which reads as a
professional-services-assisted integration model rather than a self-serve
public REST API a project like this one could point a generic connector at
without Bonterra's involvement.
Source: <https://intercom.help/Bonterra-Apricot/en/articles/11475034-faqs-apricot-api-integration>
(indexed snippet; same fetch caveat as above).

## What is unverifiable from public documentation

No rate limits, no pagination model, no record/field schema, and no evidence
of an upsert-by-external-id endpoint were found anywhere in publicly
crawlable Bonterra/Apricot documentation. This is a real gap in what
Bonterra publishes, not a shortcut taken in this research pass. Absent
evidence otherwise, the working assumption for a future implementer should
be that Apricot's API (if and when access is granted) requires the same
get-then-create-or-update upsert pattern this project's CiviCRM connector
already implements (`connectors/civicrm.py`: look up by external id, update
if found, create if not) rather than Salesforce's or Airtable's native
upsert-by-key endpoint. That assumption needs confirming against real API
docs once a developer actually has gated access -- do not build against it
without verifying first.

## The stronger near-term option: file-based, not live API

Apricot has a real, better-documented, non-API bulk-export/import path that
does not depend on the Enterprise-tier API gate:

* **Data Archives tool** (Administrator > Data Archives): exports one Tier
  1/Tier 2 form at a time as CSV.
* **Reports**: native reports can export CSV one section at a time.
* **SQL database exports/extracts**: Bonterra Professional Services offers
  scoped SQL exports as a paid service.
* **Import tool**: recommends batches of at most 20,000 records per upload,
  for writing data back in.

Sources:
<https://intercom.help/Bonterra-Apricot/en/articles/11475170-what-backup-import-and-export-options-are-available-for-my-apricot-data>,
<https://intercom.help/Bonterra-Apricot/en/articles/11475038-faqs-sql-database-exports>
(indexed snippets; same fetch caveat).

This mirrors the shape of this project's existing `salesforce_csv` and
`civicrm_csv` connectors (`connectors/crm_csv.py`): write a CSV mapped to the
target's own import schema, plus an external-id column, and let the
operator load it through the vendor's native import tool -- offline-first,
no API relationship required, and (unlike a live API push) a **local**
target the `dv` policy pack would permit.

## Recommendation

If this connector is prioritized, build the CSV-import-file variant
(`apricot_csv`, on the same `CrmCsvConnector` shape already in
`connectors/crm_csv.py`) before any live-API connector:

* It needs no Bonterra API access to build or test -- only Apricot's
  documented Import-tool column expectations, which would still need
  confirming against a real Apricot org (this project has none).
* It is `is_local = True`, so it is usable under the `dv` policy pack, unlike
  every other network connector this project has.
* It does not depend on the customer being on Apricot Enterprise (or paying
  for the Pro add-on), which the CSV/SQL-export/Import-tool paths are not
  gated behind, per the sources above.

A live-API `apricot` connector (parallel to `civicrm`/`salesforce`) should
stay deferred until either: (a) a pilot organization on Apricot Enterprise
volunteers API access for real integration testing, or (b) Bonterra
publishes a public API reference this project can build and test a
`Transport`-injected connector against without guessing at endpoint shapes,
matching how CiviCRM's and Salesforce's public API docs were used here.

## Open questions for a human decision

* Is there a pilot organization on Apricot (any tier) willing to be the real
  integration-testing partner? Per `docs/ROADMAP.md`'s stated risk for this
  whole roadmap item: "connector API churn is the named solo-maintainer tax
  -- only build against demand evidenced by a pilot or issue."
* If Apricot API access is obtained, does Bonterra provide a non-Intercom,
  fetchable API reference (OpenAPI/Swagger spec, developer portal) this
  project can cite the way the CiviCRM and Salesforce briefs could?
