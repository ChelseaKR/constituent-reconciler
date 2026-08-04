# 0012 — Connector repair-capability protocol

Status: accepted (2026-08-03); decided ahead of implementation, which follows
as the remaining UC-03 pull requests

## Context

Durable cannot-link constraints already prevent a rejected pair from
re-merging on later runs, and a local output file can be regenerated from its
inputs. Neither helps the administrator who discovers a false merge after the
batch was written into a live CRM. That person needs a safe way to understand
and execute the repair in the destination, which is the problem
`docs/NOVEL-USE-CASES-PLAN.md` scopes as UC-03 and sequences as "Now-3: UC-03
repair planning (study + 3 PRs)", with this decision record as the first
deliverable.

The roadmap closeout resolved the older "un-merge" idea (E7) by decision, not
by implementation: a generic connector protocol cannot truthfully promise
post-write reversal, because remote destinations differ on delete, merge,
restore, and audit semantics, and destructive repair must not be guessed
(`docs/ROADMAP-CLOSEOUT.md`). Whatever repair support exists must therefore be
destination-specific and explicitly declared, and it must not widen the
contract every connector is held to. Today that contract is one method:
`Connector.write_all` plus the `is_local` attribute the policy gate reads
(`src/constituent_reconciler/connectors/base.py`), a surface versioned by ADR
0006's `CONNECTOR_INTERFACE_VERSION`.

Several pieces this decision leans on already exist. The review session
implements two-person approval with distinct reviewer names, where repeated
approvals under one name count once (`review/session.py`,
`require_second_reviewer` in `policy.py`). The provenance chain stores BLAKE2b
hashes of written payloads and never the payloads themselves
(`provenance.py`, and the artifact inventory in
`docs/DATA-FLOW-AND-RETENTION.md`). The destruction executor deletes only
artifacts on an explicit list, by design never reaching past what the
pipeline is known to have written (`destruction.py`). The committed threat
model covers the untrusted-document parse path and nothing else
(`docs/THREAT-MODEL.md`).

## Decisions

### Repair is a pair of optional capabilities, not part of `write_all`

Two capabilities are added beside the connector protocol: `inspect_repair`,
which is read-only, and `apply_repair`, which mutates the destination and is
gated as described below. `Connector.write_all` keeps its current one-method
contract, and an adapter that implements neither capability remains a complete,
valid connector. Under ADR 0006's stability contract this is additive: the
`write_all` signature, the `WriteResult` shape, and `is_local` do not change.
The repair-plan artifact and the capability declaration get their own integer
schema version, declared in `schema.py` when the implementation lands, so the
plan file is a versioned surface from its first byte.

Separation is the point. Folding repair into the base protocol would force
every adapter to advertise a promise E7 established it cannot keep. A
capability, by contrast, exists only where the destination's semantics have
been verified, and its absence is machine-checkable rather than a caveat in
prose.

### The capability declaration names exact destination/version pairs

An adapter that supports repair publishes a declaration, data the planner
reads before offering any remote operation. The declaration carries:

- the destination product and API surface the adapter targets (for the pilot,
  CiviCRM API v4);
- the exact destination versions the repair behavior was verified against,
  enumerated one by one, never an open-ended range;
- the operation vocabulary supported on those versions, each operation marked
  destructive or non-destructive (the vocabulary is expected to include
  split-create and field-restore, with names finalized in the implementation
  PR once vendor semantics are read);
- the vendor documentation consulted, the date it was checked, and the
  disposable live instance the behavior was exercised against.

A destination version absent from the list is unsupported for repair, even
when the same adapter writes to it through `write_all` every day. No
declaration means no remote repair. This is fail-closed by construction and
matches the UC-03 requirement that an applied repair needs "an adapter whose
capability declaration covers that exact destination/version" together with
its dependency rule: delete, deactivate, and merge semantics come only from
current vendor documentation plus a live disposable instance, never from
memory or extrapolation across versions.

### Planning is read-only and repeatable

`reconcile plan-split --manifest ... --cluster ...` reconstructs the cluster's
members and field lineage from the local source batch named by the run
manifest and from the provenance chain. It requires a stated reason and a
reviewer identity, and it writes the repair plan to a local file. Nothing in
planning mutates the destination: `inspect_repair` may read the destination's
current state of the affected external id, and it issues no write.

Repeatability follows from those inputs. Rerunning planning against the same
manifest and cluster yields an equivalent plan, so a plan file is never
precious state. Both reviewers can independently regenerate and compare it, an
interrupted planning session leaves the destination untouched, and when the
destination has drifted since planning, the stale plan is discarded and
regenerated rather than patched. Read-only planning is also what makes it safe
to run against a destination with no repair declaration at all. That includes
the probe: `inspect_repair` may read a destination and version no declaration
covers, because the declaration gates verified mutation semantics and
`apply_repair` alone requires them. The read still passes the policy gate, so
the DV pack blocks it on a non-local destination, and the implementation PR
must make that boundary a test.

A completed repair feeds back into the local decision record: the split pairs
receive binding cannot-links, so the next run cannot recreate the bad cluster.
The cannot-link machinery in `decisions.py` already exists; the repair path
must write into it.

### Raw values stay in the local plan; provenance stores the digest

The plan records the old external id, the proposed split records, the fields
that need restoration, and the operations the destination supports. Restoring
a field requires its raw value, so the plan is a PII-bearing artifact and
lives only on the operator's machine. Provenance gets the plan's digest, never
its content, consistent with the existing rule that provenance references
payloads by hash (`docs/DATA-FLOW-AND-RETENTION.md`, `provenance.jsonl` row).
At apply time the digest binds both approvals to the exact plan bytes being
executed, and each applied operation appends its own provenance entry, which
is what lets `reconcile verify` later confirm the repair-plan digest and every
operation performed under it.

### A second reviewer is mandatory for remote destructive operations

Applying a plan that contains any destructive operation against a remote
destination requires approval from two distinct reviewers, reusing the
existing session machinery in which approvals under a single name count once.
For match review, two-person mode is optional outside the DV pack. For remote
destructive repair it is unconditional in every policy pack, because the risk
profile differs in kind: a local output can be regenerated from inputs, while
a remote delete or merge destroys state this tool does not hold. Routing that
irreversible step through a second human is the fail-closed rule applied to
the one place a mistake cannot be recomputed away.

Policy gating otherwise stays uniform. A pack that refuses non-local write
targets, as the DV pack does at `pipeline.build_connector`, refuses both
repair capabilities on a non-local destination for the same reason and through
the same gate. No adapter special-cases the pack, which preserves the plan's
boundary that a policy pack can prevent egress without special cases in the
adapter.

### Unsupported destinations get manual instructions, never a forced delete

When no declaration covers the destination and version, planning still
completes and renders operator-facing manual instructions: the affected
external id, the proposed replacement records, and the field values needing
restoration, stated in the destination's own terms as far as the plan can name
them. `apply_repair` is not there to call, and no CLI flag can force
one adapter's operations onto another destination or fall back to a generic
delete. E7's "destructive repair must not be guessed" becomes structural: an
operation nobody verified is an operation the code cannot execute.

### CiviCRM is the pilot destination

The pilot follows the same reasoning that made CiviCRM the first live
connector in ADR 0002: it is fully open and self-hostable, so the disposable
live instance the declaration requires can be stood up without a vendor
relationship. Its entity model is already explicit in this codebase, since the
adapter writes dedicated Contact, Email, and Phone entities keyed by an
external identifier (`connectors/civicrm.py`), which gives split records
concrete entities to map onto. UC-03's dependency list names it the preferred
pilot for the same reasons.

What this ADR does not claim is any CiviCRM repair semantics. Whether Contact
deletion is a recoverable trash state, how contact merge behaves and whether
it can be reversed, and what CiviCRM's own audit log records are open inputs.
They must be read from current CiviCRM API v4 documentation and exercised on a
live disposable instance before the CiviCRM declaration lists a single
version. Salesforce and Airtable receive no declaration in the pilot; each
would need the same vendor-documentation and live-instance work first.

### Threat-model and destruction-inventory updates precede stored plans

The repair plan is a new local artifact holding raw field values, and
`apply_repair` is a new remote-mutation path, so two committed documents and
one code list must change before the first plan is written to disk:

- `docs/THREAT-MODEL.md` gains the repair surface: theft or exposure of a
  plan file that concentrates raw values for the people in a bad merge; a
  tampered plan applied remotely, mitigated by the digest binding and the
  second reviewer; and credential scope, since an API token able to delete or
  merge records is a larger asset than one that upserts.
- `docs/DATA-FLOW-AND-RETENTION.md` gains a repair-plan row in the artifact
  inventory, marked as holding individual records, and each policy pack's
  retention model places it.
- `destruction.PII_ARTIFACTS` gains the plan artifact by name. The
  destruction executor deliberately deletes only what its explicit list
  names, so without this entry `reconcile destroy` would leave repair plans
  behind.

These updates are merge-blocking prerequisites for the implementation PR that
first stores a plan, matching the plan's cross-cutting rule that every new
PII artifact updates the retention and destruction inventory.

## Consequences

- Adapters gain verified repair support one destination version at a time,
  without touching `write_all`, other adapters, or the ADR 0006 stability
  surface.
- A new release of a supported destination is unsupported for repair until
  re-verified against vendor documentation and a live instance. That is the
  fail-closed default doing its job, and it is recurring maintenance work.
- An organization with a single trained reviewer cannot apply a remote
  destructive repair through this tool. It will get the manual instructions
  instead. This is deliberate and should be said plainly in the adoption
  docs.
- Vendor-semantics research is now the pacing item for the pilot; the open
  inputs named above cannot be closed from this repository alone.
- Revisit this decision when a second destination requests repair, when pilot
  feedback answers "which destination needs the first repair capability and
  what undo means in that system" differently than assumed here, or if the
  declaration proves too coarse because operations differ by sub-entity
  within one destination version.
- Revisit the cannot-link timing when `apply_repair` lands: the shipped
  planner deliberately binds at planning time rather than at the repair
  completion described above, because rejection is authority a single
  reviewer already holds in match review and because plan-time binding is
  what keeps the next run from recreating the cluster while no apply path
  exists.
