# Pilot-readiness adoption kit

A short guide for bringing your own organization onto constituent-reconciler.
It walks one reporting period of real intake through the pipeline, from a
spreadsheet export to records written back into your case system, so you can
judge whether the tool fits before you commit to it.

The audience is a data or operations person at a small or mid-sized
human-services nonprofit. You do not need to write code. You do need a CSV export
of your constituents, a way to run a command (a terminal or Docker), and about an
hour for a first pass.

This is a reference implementation, not legal advice. The retention, consent, and
confidentiality rules an adopting organization must follow are yours and your
counsel's to determine. The DV policy pack encodes a defensible reading of VAWA
and FVPSA confidentiality, with its sources and limits in
[RESPONSIBLE-TECH-AUDITS.md](./RESPONSIBLE-TECH-AUDITS.md), and it is still no
substitute for your own review.

## What a pilot proves

The 1.0 stability tag is gated on the pipeline proving out against real
organizations, not synthetic fixtures. A pilot answers four questions a demo
cannot:

1. Do the pre-tuned defaults find your duplicates without flooding the review
   queue?
2. Is the review queue something a non-technical colleague can actually run?
3. Does the write-back land where your staff need the records, keyed so a re-run
   updates rather than duplicates?
4. If you serve survivors, does the DV pack hold client data on the machine the
   way your funding stream requires?

Hold the pilot to those questions. A run that surfaces a real near-duplicate you
had missed, and routes a genuinely ambiguous pair to a person, is the tool
working as designed.

## Before you start

You will need:

- Python 3.12 or newer, or Docker. Install the tool with `make install`, or build
  the container with `make docker`.
- A CSV of the constituents already in your case system. Export the fields you
  match on: name, date of birth, email, phone, and address if you keep it.
- The new intake to reconcile against that set. A second CSV is the simplest
  start; a folder of PDFs works once you install the `extract` extra.
- A column that records consent, if you have one. The pipeline reads it on every
  record.

Work on a copy. Nothing in the pilot writes to your live system until you choose
the write step and point it there.

## Step 1: Get your data into two files

Put the existing records in one CSV and the new intake in another. The column
headers can be whatever your export produces; you map them in the next step. A
small first pass, a few hundred rows, tells you most of what you need and runs in
seconds.

```text
existing.csv   # constituents already in your CRM
incoming.csv   # the new intake to reconcile against them
```

If your intake arrives as PDFs, point `incoming` at the folder instead and add an
`[extract]` section (see [the README](../README.md#reading-from-pdfs)). The
pipeline routes `.csv` files through the structured reader and `.pdf` files
through the offline extractor.

## Step 2: Write a recipe

A recipe is a small TOML file that names your two CSVs, maps their columns onto
the fields the matcher reasons over, and points at the column that holds consent.

### Start from your own column headers

```sh
constituent-reconcile init --existing existing.csv --incoming incoming.csv --out recipe.toml
```

`init` reads the header row of your files and writes a starter recipe from what
it finds. Point `--incoming` at a folder and it reads every `.csv` directly
inside it, and tells you which other file types it found and did not inspect.

Three things it does not do, which is most of why it is safe to run on a real
export:

* **It never reads past the header.** No value is inspected, so nothing is
  inferred from your data. Two files with the same headers and completely
  different contents produce the same recipe.
* **It maps a field only on an exact alias from the table below**, compared
  case-insensitively with runs of spaces collapsed, and in no other way.
  `Client Given` is not `first name`. Anything unrecognised becomes a `CHOOSE`
  line with your real column names printed beside it, and every column the
  recipe does not use is listed at the bottom of the file so nothing is dropped
  without you seeing it. If two of your columns both match one field, it maps
  neither and names both.
* **It does not choose a policy pack.** `[policy] pack` is written empty, which
  is not a valid pack, so the recipe will not load until you have made that
  decision in Step 3. Run `constituent-reconcile validate --config recipe.toml`
  and it lists every outstanding `CHOOSE`.

`init` will not overwrite a file that already exists. To re-scaffold, write it
somewhere else with `--out` and merge by hand.

#### The alias table

| Recipe field | Column headers recognised |
|---|---|
| `first_name` | `first name`, `firstname`, `fname`, `first`, `given name`, `givenname` |
| `last_name` | `last name`, `lastname`, `lname`, `last`, `surname`, `family name` |
| `dob` | `dob`, `date of birth`, `birth date`, `birthdate`, `birthday` |
| `email` | `email`, `e-mail`, `email address`, `emailaddress` |
| `phone` | `phone`, `phone number`, `telephone`, `mobile`, `cell`, `cell phone` |
| `address` | `address`, `street`, `street address`, `address line 1`, `address1` |
| `[consent] column` | `consent`, `consent status`, `consent flag`, `opt in`, `optin` |
| `[input] id_column` | `id`, `record id`, `recordid`, `client id`, `constituent id` |

An alias earns its place only if the header means that field and nothing else in
a human-services export. `name` is deliberately absent: it is a full name at
least as often as a first name.

### Or write it by hand

Copy `examples/intake-demo/recipe.toml` and change the names to match your export.

```toml
[input]
existing = "existing.csv"
incoming = "incoming.csv"
id_column = "id"          # a stable per-row id in your data, if you have one

[mapping]
first_name = "First Name" # the value on the right is your column header
last_name  = "Last Name"
dob        = "DOB"
email      = "Email"
phone      = "Phone"
# address  = "Street"     # map this only if you keep address and want it matched

[consent]
column  = "Consent"          # the column that records each person's consent status
# date    = "Consent Date"     # optional: ISO-8601 (YYYY-MM-DD) grant date
# expires = "Consent Expires"  # optional: ISO-8601 expiry date; unmapped = no ceiling
# scope   = "Consent Scope"    # optional: comma-separated destinations ("crm,funder_export")

[thresholds]
prior  = 0.01
auto   = 0.97
review = 0.80
```

You map only the fields you have. A recipe that maps no `address` runs without it,
so you are never forced to invent data you do not collect. The thresholds are the
pre-tuned defaults; leave them until a pilot run gives you a reason to move them.
Consent values of `granted`, `active`, `yes`, or `true` permit export; anything
else, including a blank, is read as no consent and blocks the write when a pack
requires it.

Consent is a lifecycle, not just a status column. `date` and `expires` let you
record that a person's consent is time-limited; `expires` is checked against
today's date on every run, fail-closed, so a consent granted years ago does not
read as granted forever. There is no default expiry window: if your consent
needs a hard ceiling, that number comes from your organization's counsel, not
from this tool, and you record it per person by mapping `expires`. `scope`
lets one consent cover one destination (your CRM, say) without covering
another (a funder export, say); leave it unmapped and consent covers every
destination, matching the behavior with no scope column at all. A withheld
record's reason -- `absent`, `revoked`, `future-dated`, `expired`, or
`out-of-scope` -- is recorded in `withheld.csv` so a follow-up knows what to
ask for.

## Step 3: Pick a policy pack

The pack sets the privacy posture for the whole run.

- `default` for general nonprofit data. Consent is enforced only if you turn it
  on in the recipe.
- `dv` for victim-service providers under VAWA or FVPSA. The pack fuses off the
  cloud extraction seam, refuses any non-local write target, withholds records
  without granted consent, and emits an aggregate, suppressed summary as the only
  shareable artifact. Use the bundled `recipe-dv.toml`, set `pack = "dv"`, or pass
  `--policy-pack dv` to apply it to any recipe without editing it.
- `hipaa` is a partial pack (consent plus no cloud seam). It does not claim the DV
  local-target and aggregate rules.

If you are unsure whether your program falls under survivor-confidentiality rules,
treat that as a question for your counsel before the pilot, not after.

## Step 4: Validate, dry-run, then read the review queue

Before the first real run, check the recipe's shape without resolving anything:

```sh
constituent-reconcile validate --config recipe.toml
```

It loads the recipe, rejects an unknown section or a misspelled key by name
(a typo'd `[consnet]` or `auto_threshold` used to run quietly at a default
instead of raising), checks that `incoming` and `existing` point at files that
exist, and prints the active policy pack, thresholds, and switches so you can
eyeball them before anything runs.

Run the pipeline without writing anything:

```sh
constituent-reconcile run --config recipe.toml --out out --dry-run
```

The summary tells you how many records were read, how many candidate pairs the
matcher scored, how many it merged automatically, and how many it sent to review.
A queue that is mostly empty means the defaults found clean matches; a large queue
means your data has more genuine ambiguity, which is exactly what a person should
look at.

Open the queue in a browser:

```sh
constituent-reconcile review --config recipe.toml --reviewer "your name" --out out
```

Each uncertain pair shows the two records side by side, with a plain-language line
that says what they agree on, what they differ on, and what could not be compared
because a field was blank. A colleague decides approve or reject with the keyboard
or the mouse, and the decisions save as they go, each attributed to the
`--reviewer` name. The server stays on your machine and writes no field value to
disk. Under the DV pack it refuses any non-loopback bind, so the review surface
cannot become a way for client data to leave, and two-person review is on: a
merge only takes effect after a second reviewer, under their own name, also
approves it.

Carry the decisions back in:

```sh
constituent-reconcile apply --config recipe.toml --decisions out/decisions.json --out out
```

## Step 5: Calibrate how far to trust it

The gated metric is the false-merge rate, the share of automatic merges that join
two different people. A false merge can corrupt a record and is sometimes
irreversible; a missed match only leaves a duplicate you can still find later.
The tool is built to keep the first error near zero at the cost of sending more
pairs to a person.

The committed [eval report](../eval/report.md) shows those rates on the demo
fixtures, with Wilson confidence intervals because the counts are small. To score
your own pilot, write a small ground-truth file of the duplicates you already
know about and run:

```sh
constituent-reconcile eval --config recipe.toml --truth ground_truth.json
```

A run with zero false merges and every known duplicate surfaced to a human, at
the automatic or the review level, is the result to look for.

## Step 6: Write back

Start with the offline path. The `civicrm_csv` and `salesforce_csv` connectors
write a file mapped to your CRM's own import schema, plus an external-id column
keyed on the resolved cluster:

```sh
constituent-reconcile run --config recipe-civicrm-csv.toml --out out
# writes out/civicrm_import.csv for CiviCRM's Import Contacts
```

Load that file with your CRM's native import tool and upsert on the external
identifier, so a second pilot run updates the same contacts rather than creating
new ones. Because the file stays local, this path is permitted under the DV pack.

The live API push is the opt-in alternative. Select the `civicrm` or `salesforce`
connector in the recipe's `[output]` section and pass the credential through the
environment, never the recipe:

```sh
CIVICRM_API_KEY=your-key constituent-reconcile run --config recipe-civicrm.toml --out out
```

Use `--dry-run` first to see what would be written without contacting the server.

## Step 7: Keep the provenance log

Every write records an entry in an append-only, tamper-evident log
(`out/provenance.jsonl`): a BLAKE2b hash of the written fields chained to the
hash before it. Check the chain at any time:

```sh
constituent-reconcile verify --provenance out/provenance.jsonl
```

This is what lets you show a funder or an auditor what was written, when, and
under which consent.

## A pilot-readiness checklist

You are ready to run a pilot when each of these is true:

- [ ] You have an `existing.csv` and an `incoming.csv` copied from real data, on a
      machine you control.
- [ ] Your recipe maps every field you match on, and points at your consent
      column.
- [ ] You have chosen a policy pack, and if it is `dv` you have confirmed the
      choice against your funding stream with your counsel.
- [ ] A dry-run completes and the review queue size looks plausible for your data.
- [ ] A non-technical colleague has stepped through a few review pairs and found
      them legible.
- [ ] You know which write path you will use, and you have tried it against a test
      instance or a throwaway import.

## What the pilot does not decide for you

The tool enforces consent and non-egress as tested invariants, but it does not set
your retention schedule or write your consent language, and it does not determine
which confidentiality rules apply to your program. Those are yours to settle with
your own counsel and your funder's requirements. The retention and destruction model
per policy pack is defined in
[DATA-FLOW-AND-RETENTION.md](./DATA-FLOW-AND-RETENTION.md) (R8), and it is not a
substitute for that review.

If a pilot surfaces a gap, a connector you need, a matching default that misfires
on your data, or a confidentiality requirement the pack does not cover, open a
GitHub issue. Real-organization feedback is the evidence the 1.0 tag is waiting
on, and it is the most useful thing a pilot can produce.
