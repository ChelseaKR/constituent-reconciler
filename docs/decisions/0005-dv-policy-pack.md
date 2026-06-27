# 0005 — The DV policy pack

Status: accepted (v0.5)

## Context

The research named a privacy mode a survivor-serving organization can legally use
as this project's sharpest differentiator, and named v0.5 the fundability unlock.
Victim-service providers operate under VAWA and FVPSA confidentiality rules that
make a cloud service a structural non-starter, not a preference, and that
constrain what may be written, where, and what may be shared. The DV policy pack
turns those rules into enforced behavior.

The project's own ground rule is that legal facts are not invented: the invariants
here were checked against primary statutory text and NNEDV Safety Net guidance
before being encoded, and the citations are recorded so a reviewer can check them.
That verification produced three corrections worth stating, because they change
what the docs may claim:

* The phrase "regardless of whether the information has been encoded, encrypted,
  hashed, or otherwise protected" is genuine statutory VAWA text
  (34 U.S.C. § 12291(a)(25) and § 12291(b)(2)(B)(i)), but the statute's operative
  verbs are "disclose, reveal, or release." The reading that entry into a shared
  database such as HMIS is a prohibited disclosure is NNEDV and HUD guidance, not
  a verbatim statute, and is attributed that way.
* "Revocable" consent is NNEDV best practice, not statutory text. The statutory
  requirement is informed, written, reasonably time-limited consent
  (34 U.S.C. § 12291(b)(2)(B)(ii)), which may not be a condition of services
  (§ 12291(b)(2)(D)(ii)(I)).
* The small-cell threshold of n < 11 is the U.S. CMS Cell Size Suppression
  Policy, not a HUD, HMIS, VAWA, or FVPSA rule. No uniform federal threshold
  exists; HUD, VAWA, and FVPSA set none. The CMS rule is the most defensible
  bright line and is cited as "modeled on CMS," not as a DV mandate.

## Decisions

### A policy pack is a declarative bundle of invariants

`policy.py` defines a `Policy` dataclass whose fields are the switches the rest of
the pipeline reads: `require_consent`, `forbid_cloud_seam`,
`require_local_targets`, `aggregate_export`, `suppression_threshold`. `policy_for`
maps a pack name to its `Policy`. An unknown pack name raises `PolicyViolation`
rather than falling back to the permissive default, so a typo fails closed. The
recipe derives its enforcement fields from the pack, and a `--policy-pack`
override lets a run apply the DV posture to any recipe without editing it.

### The DV pack enforces four things, each at one site

* **Consent required** (consent.py). A resolved record whose survivor does not
  carry granted consent is withheld and recorded by id and reason only, never
  with field values.
* **Cloud seam fused off** (extract/seam.py). `make_seam` returns a `NoOpSeam`
  for the dv and hipaa packs regardless of the recipe's backend, enforced at
  construction time so there is no path from a DV recipe to a network call.
* **Local write targets only** (pipeline.build_connector). Each connector
  declares `is_local`; the CSV writer is local, the CiviCRM connector is not.
  Under the dv pack a non-local target is refused with `PolicyViolation` before
  any write, so client records do not leave the machine. The org keeps client
  data in its own database, which is the comparable-database posture HUD requires
  of victim-service providers.
* **Aggregate, suppressed sharing** (suppression.py). The dv pack emits an
  `aggregate_summary.json` of non-identifying counts with small cells suppressed.
  It is the only artifact the pack treats as shareable beyond the machine.

### Small-cell suppression follows the CMS rule, with complementary suppression

`suppress_cells` suppresses a count of 1 through 10, preserves a true zero (a
zero reveals no one), and applies complementary suppression: if exactly one cell
is suppressed by the primary rule, the smallest remaining positive cell is
suppressed too, so the first is not recoverable by subtraction from a total. The
limitation is stated plainly: this does not defend against cross-tabulation
attacks that correlate several breakdowns, which is out of scope for v0.5.

### hipaa is deliberately partial

The hipaa pack turns on consent and fuses the cloud seam off, but does not claim
the dv pack's local-target and aggregate rules, because HIPAA permits cloud
processing under a business associate agreement and its full posture (the Safe
Harbor de-identification method, BAAs) is not specified here. Claiming a complete
HIPAA mode the tool does not implement would be the same overclaim the project
refuses on CASS certification.

## Consequences

- `Connector` gains an `is_local` attribute; the two connectors set it.
- `Recipe` gains `require_local_targets`, `aggregate_export`, and
  `suppression_threshold`, derived from the pack.
- The DV invariants are merge-blocking tests: `tests/test_no_egress.py` (refuses
  the non-local target, fuses the seam off, withholds non-consented records,
  aggregate carries no field values) and `tests/test_suppression.py` (the
  suppression rules). If any fails, the pack is no longer safe to claim.
- The legal grounding, with citations and the three corrections above, lives in
  docs/RESPONSIBLE-TECH-AUDITS.md. It is a reference implementation, not legal
  advice; an adopting organization needs its own review.
