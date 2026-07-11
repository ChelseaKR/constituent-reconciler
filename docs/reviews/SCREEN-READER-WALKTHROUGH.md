# Screen-reader walkthrough — review queue

Status: **not yet performed.** This is a checklist for a human tester to run
with real assistive technology, not a report of a walkthrough that has
happened. Do not fill in the results section from inference, another tool's
output, or a prior run of a different tool; leave it blank until someone has
actually done this with a screen reader.

This is the second half of the accessibility gate named in
docs/ROADMAP.md's metrics ledger ("axe clean, screen-reader walkthrough"). The
first half — an automated axe-core scan of the rendered review queue — is
implemented in `scripts/axe_audit.mjs` and runs in CI
(`.github/workflows/ci.yml`, job `accessibility`); see
docs/decisions/0009-automated-axe-audit.md for why an automated scan can cover
that half but not this one. A scan can confirm markup conforms to a rule set;
it cannot tell you whether the rationale sentence reads sensibly out loud,
whether the reading order matches the visual order at a table with scoped
headers, or whether a real screen-reader user can complete a review pass
without seeing the screen. Those are the questions this checklist is for.

## Who can run this

Anyone comfortable with a screen reader at a beginner-to-intermediate level.
It does not require being a daily assistive-technology user, but it does
require actually turning one on and navigating with the mouse and monitor off
(or the monitor on, screen reader driving) rather than reading the HTML and
imagining how it would sound.

## Setup

1. `git clone` (or use a local checkout) and `make install`.
2. `.venv/bin/reconcile run --config examples/intake-demo/recipe.toml --out out`
   to produce `out/decisions.json`'s prerequisites and a real review queue
   with the two known lookalike pairs from the demo fixture.
3. `.venv/bin/reconcile review --config examples/intake-demo/recipe.toml --out out`
   and open the printed loopback URL.
4. Pick one screen reader for the pass, matched to the OS in front of you:
   - macOS: VoiceOver (Cmd+F5), tested in Safari.
   - Windows: NVDA (free), tested in Firefox or Edge.
   - Linux: Orca, tested in Firefox.

## Walkthrough script

Perform every step below with the screen reader driving and the mouse
untouched. Note anything that surprised you, not just outright failures — a
label that is technically present but confusing is worth recording.

1. **Landing on the queue overview.** Load `/`. Can you tell, from the first
   few seconds of speech, what this page is for and how many pairs need a
   decision?
2. **The progress announcement.** The decided/approved/rejected count uses
   `aria-live="polite"`. Approve or reject a pair from a later step, then
   return to `/` — does the screen reader announce the updated count, or does
   the update pass silently?
3. **The privacy banner** (DV pack). Re-run the setup's `reconcile review`
   command with `--policy-pack dv` added. Does the screen reader announce the
   privacy banner (`role="status"`) without the user having to go looking for
   it, and without it interrupting whatever else is being read?
4. **Reaching a pair.** From `/`, navigate to the first unreviewed pair using
   only the screen reader's own navigation commands (headings, links,
   landmarks — whatever the tool offers), not sighted mouse clicks.
5. **The comparison table.** Navigate the comparison table cell by cell.
   - Does the screen reader announce the row header (field name) and column
     header (record id and source) for each cell, or only the cell contents?
   - Is the agreement marker ("match" / "differs" / "not compared") announced
     as text, not left silent because it's carried by color/a symbol alone?
6. **The rationale.** Does "Why this pair is here" and "What matches and what
   differs" read as a sentence that makes sense spoken aloud, or does it read
   as a jargon dump? (This one has no automated proxy — it is a judgment
   call the eval and the axe scan cannot make.)
7. **Making a decision with no mouse.** Reach the Approve and Reject buttons
   by keyboard/screen-reader navigation alone and activate one. Confirm the
   "Current decision" status text updates and is announced.
8. **Undo and redo.** Change the decision on the same pair (approve, then
   reject, or vice versa). Is the change to the "Current decision" line
   announced, not just visually updated?
9. **Moving between pairs.** Use the Previous/Next links (not the `j`/`k`
   shortcuts — those are progressive enhancement, and this step is checking
   the baseline that works without them) to move through every remaining
   pair, repeating steps 5–7 loosely rather than skipping to the end.
10. **Finishing.** Return to `/` once every pair has a verdict. Does "All
    pairs reviewed" get announced, and is the `apply` command readable/
    copyable via the screen reader (it's inside a `<pre><code>` block)?
11. **The empty case.** Run the same launch against a recipe with no
    uncertain pairs (or delete `decisions.json` and approve/reject every pair
    via the CLI's decisions file directly, then reload `/`) and confirm "There
    are no uncertain pairs to review" is announced clearly.

## Results

_(Leave this section as-is — unfilled — until a human tester has completed
the script above with a real screen reader. Fill in: date, tester, OS,
browser, screen reader and version, and one line per numbered step: pass,
pass with a note, or fail with what happened. Any fail or note should become
a linked issue before this file's Status line at the top is changed to
"performed.")_

| Step | Screen reader | Result | Notes |
|------|----------------|--------|-------|
| _(not yet run)_ | | | |
