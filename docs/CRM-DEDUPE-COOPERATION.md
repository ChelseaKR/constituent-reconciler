# CRM dedupe-rule cooperation

How to configure CiviCRM and Salesforce/NPSP duplicate management so the CRM's
own matching layer cooperates with this tool's external-id upsert instead of
fighting it.

The audience is the person who administers the target CRM for a small or
mid-sized human-services nonprofit: a CiviCRM site admin or a Salesforce/NPSP
consultant. The configurations below are reference configurations, not
consultant advice; test them in a sandbox against a copy of your data before
touching a production org (see the standing caveat at the end).

## The problem: two matching layers, one contact table

The reconciler is a matching layer. It clusters source rows, assigns each
cluster a stable id, and writes exactly one contact per cluster into the CRM,
keyed on that id:

- The **CiviCRM connector** upserts on `external_identifier`: it looks the
  contact up by that field, updates it if found, and creates it (carrying the
  cluster id) if not. See
  [`src/constituent_reconciler/connectors/civicrm.py`](../src/constituent_reconciler/connectors/civicrm.py).
- The **Salesforce connector** PATCHes
  `/sobjects/Contact/External_Id__c/<cluster_id>`, the REST
  upsert-by-external-id endpoint, which natively creates or updates on that
  key. See
  [`src/constituent_reconciler/connectors/salesforce.py`](../src/constituent_reconciler/connectors/salesforce.py).

Both writes are idempotent because the cluster id is the only key. A re-run
updates the same contacts rather than minting duplicates.

The CRM has its own matching layer: CiviCRM dedupe rules and Salesforce
matching rules plus duplicate rules. That layer knows nothing about cluster
ids. If it merges or blocks contacts independently, the two layers fight, in
two directions:

- **Rejected writes.** A duplicate rule set to Block (Salesforce), or an
  import configured to skip on duplicate (CiviCRM), stops the upsert's create
  path whenever an incoming contact resembles an existing one. The batch
  fails or rows silently never land, and the cluster id never attaches to any
  record.
- **Silent divergence.** If the CRM auto-merges (or a staff member
  batch-merges) two contacts that carry *different* cluster ids, one id
  disappears from the CRM. The next reconciler run finds no contact for that
  id and creates a fresh one, re-duplicating exactly what was just merged.
  Worse, a loose match-on-import that "updates the existing duplicate" can
  attach a cluster id to the wrong person's record, after which every future
  upsert for that cluster silently updates the wrong contact.

The fix is not to disable CRM dedupe. It is to configure the CRM so its
*automatic* layer never acts across differing external ids, and its *human*
layer (supervised merges, duplicate reports) stays available for the
duplicates that predate the reconciler.

## CiviCRM: unsupervised rule configuration

CiviCRM dedupe rules are per contact type and come in two "uses":
**Unsupervised** rules run automatically (contact import duplicate checking,
and some batch flows), while **Supervised** rules are only applied when a
human runs Find and Merge Duplicate Contacts. A rule is a set of fields with
weights and a threshold: two contacts are duplicates when the weights of
their matching fields sum to the threshold or more.

Two facts about the connector's write paths frame the recommendation:

- **Live API path.** APIv4 `Contact.create` performs no duplicate check, so
  the connector's lookup-then-create sequence (`Contact.get` on
  `external_identifier`, then `update` or `create`) is the *only* matching
  applied on this path. That is what the connector assumes: nothing between
  the lookup and the create silently redirects the write. Dedupe rules do not
  interfere here.
- **Import-file path.** The offline `civicrm_csv` export is loaded through
  CiviCRM's Import Contacts screen, and that flow *does* run duplicate
  checking with the Unsupervised rule. Import the file matching on
  **External Identifier** with "Update" on duplicate, so a re-import updates
  the reconciler's own rows; do not let a name/email rule decide which
  existing contact a row "is".

Recommended Unsupervised rule for Individuals (replace the default
email-only rule):

| Field | Weight |
| --- | --- |
| External Identifier | 10 |
| Email | 5 |
| Last Name | 3 |
| Birth Date | 2 |

**Threshold: 12.**

The arithmetic is the point: email + last name + birth date reach only 10, so
no pair of contacts with differing (or absent) external identifiers can ever
hit the threshold on person-attributes alone. The rule fires only when the
external identifier itself matches (10) plus at least one corroborating field
— a state the upsert already prevents, so in practice the automatic layer
stands down for reconciler-managed contacts. If you prefer to keep your
existing unsupervised fields, the equivalent constraint is: **keep the
unsupervised threshold above the maximum score reachable by name, email, and
phone alone.**

Keep the aggressive rules — fuzzy name, nickname tables, address-based —
**Supervised only**. The merge screen shows a conflicting External Identifier
side by side, and a human can stop and check. Two follow-on rules for that
human:

- Merging two contacts with the *same* external identifier is always safe
  (it should not happen, but it converges).
- Merging two contacts with *different* external identifiers means the
  reconciler split one person into two clusters. Do not just merge in
  CiviCRM: the losing cluster id will be recreated on the next run. Instead,
  treat it as reconciler feedback — the pair belongs in the review queue, and
  the source rows should resolve to one cluster before the next write.

## Salesforce/NPSP: matching rule and duplicate rule configuration

Salesforce splits the layer in two: a **matching rule** defines what counts
as similar, and a **duplicate rule** decides what happens on create/edit
(Allow, Alert, Report, or Block). NPSP's recommended contact matching is
fuzzy first name, exact last name, exact email — keep that; the change is in
the duplicate rule's action, because **upsert-by-external-id must not be
blocked**.

First, the field the connector keys on:

1. **Create the external-id field.** Setup → Object Manager → Contact →
   Fields & Relationships → New → Text. Field Label `External Id` (API name
   `External_Id__c`, the connector's default), length 255, and check
   **External ID** and ideally **Unique** (case-sensitive). Grant the
   integration user's profile or permission set field-level Edit access.
   Without the External ID attribute the REST
   `/sobjects/Contact/External_Id__c/<value>` endpoint does not exist;
   without Unique, a manually keyed duplicate value makes the upsert ambiguous
   (Salesforce rejects the PATCH with a 300 when multiple records match).

2. **Matching rule.** Setup → Duplicate Management → Matching Rules → New
   Rule → Contact. Fields: First Name (Fuzzy: First Name), Last Name
   (Exact), Email (Exact) — the NPSP default shape. Save and **Activate**.

3. **Duplicate rule.** Setup → Duplicate Management → Duplicate Rules → New
   Rule → Contact, using the matching rule above.
   - *Action on Create* and *Action on Edit*: **Allow**, with **Report**
     checked. This records duplicate record sets for staff to review without
     ever stopping a save.
   - Do **not** use **Block**: the create path of the upsert returns
     `DUPLICATES_DETECTED` (HTTP 400) whenever a new cluster resembles an
     existing contact, and the connector fails the batch loudly (it raises
     rather than retrying — fail closed, but nothing lands).
   - Use **Alert** with care: alerts are interactive. An API save under an
     Alert rule is rejected exactly like Block unless the caller sends the
     `Sforce-Duplicate-Rule-Header: allowSave=true` header — which this
     connector deliberately does not send, so a surviving Alert rule behaves
     as Block for the integration.
   - If your org policy requires Alert or Block for humans, scope the rule
     away from the integration instead: add a rule **Condition** such as
     *Current User: Username ≠ (integration user's username)*, or gate the
     rule on a field only interactive flows set. Then human entry keeps its
     guardrails and the upsert path keeps its lane.
4. **NPSP Contact Merge.** NPSP's merge UI keeps one `External_Id__c` value
   and drops the other. As with CiviCRM: merging across two *different*
   cluster ids sends the losing id back to "not found", and the next run
   recreates it. Route cross-cluster merges through the reconciler's review
   queue instead of merging first in the CRM.

The offline `salesforce_csv` export path (Data Import Wizard or Data Loader,
matching by External ID) is subject to the same duplicate rules, so the
Allow-plus-Report action protects that path too.

## Failure modes at a glance

What each CRM-side posture does to the upsert's two paths:

| CRM posture | Upsert create path (new cluster id) | Upsert update path (id already present) |
| --- | --- | --- |
| **Block** (SF duplicate rule) | Rejected with `DUPLICATES_DETECTED`; connector raises, batch fails loudly, no cluster id lands | Rejected on edit if the rule runs on Edit and the record resembles another; otherwise unaffected |
| **Alert** (SF, no `allowSave` header) | Same as Block for API callers: rejected | Same as Block on Edit |
| **Allow + Report** (SF, recommended) | Write lands; the pair is logged in a duplicate record set for human review | Write lands; report captures drift for review |
| **Skip on duplicate** (CiviCRM import) | Row never imported; the cluster id never attaches to any contact — silent gap | Unaffected when matching on External Identifier |
| **Update on duplicate with a loose rule** (CiviCRM import matching on name/email) | Cluster id attached to whichever existing contact the rule matched — possibly the wrong person; all future upserts update that wrong record | Same wrong-record risk on every re-import |
| **Auto/batch merge across differing external ids** (either CRM) | n/a | Losing id vanishes; next run recreates the contact (re-duplication), while upserts for the surviving id keep landing on the merged record |

The two silent rows — loose update-on-duplicate and cross-id merge — are the
ones worth designing against, because nothing errors. Block and Alert fail
loudly, which is recoverable but means the write-back is not landing.

## Standing caveat

This is a reference configuration, not consultant advice. Dedupe behavior
varies with CRM version, installed extensions, and org customization, and a
wrong merge in a live system is sometimes irreversible — the exact harm this
project's thresholds are tuned against. Verify every setting in a sandbox (a
CiviCRM staging copy, a Salesforce sandbox org) with a copy of your data and
a dry run (`--dry-run`) before pointing the connectors at production. For
context on the pilot this slots into, see
[ADOPTION-KIT.md](./ADOPTION-KIT.md); for the write paths themselves, see
[the README](../README.md#writing-back-to-a-case-system).

External references (the evidence behind this item in
[RESEARCH-ROADMAP.md](./RESEARCH-ROADMAP.md)):

- [Understanding CiviCRM dedupe rules](https://civicrm.org/blog/spidersilk/understanding-civicrm-dedupe-rules)
  — unsupervised rules fire automatically and should be defined narrowly.
- [Configure duplicate detection and NPSP contact merge](https://help.salesforce.com/s/articleView?id=sfdo.configure_duplicate_detection_and_npsp_contact_merge.htm&language=en_US&type=5)
  — the NPSP matching-rule shape (fuzzy first, exact last, exact email) and
  duplicate-rule actions.
