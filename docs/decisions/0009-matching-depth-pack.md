# 0009 — Matching depth pack

Status: accepted (2026-07-07)

## Context

`docs/ideation/03-expansions.md` (EXP-03) names four known error classes the
v0.1 matcher does not attack: a nickname pair (Bill/William, Peggy/Margaret)
is too far apart in Jaro-Winkler similarity for `defaults._NAME_CLOSE` to
catch; blocking on an exact normalized surname (`defaults.blocking_rules_for`)
misses a transliteration variant; a match on "Smith" counts the same as a
match on a rare surname; and `normalize_name` collapses a two-surname value
("de la Cruz Gómez") into one opaque token, mishandling the paterno/materno
naming convention common in the communities NNEDV Safety Net member
organizations and many human-services intake forms serve.

This decision covers all four pieces, since they share one mechanism (a
comparison level or blocking rule reading a column `normalize.py` derives)
and one review: no training, no labeled pairs, hand-set m/u probabilities,
same as 0001.

## Decisions

**(a) Nickname table.** `nicknames.py` is a small, vendored,
English-language table of common nickname groups (`canonical_key`).
`normalize_record` derives `first_name_nickname_key` alongside `first_name`;
`defaults._first_name_comparison` adds a "nickname" level between exact and
Jaro-Winkler "close" that fires when two nickname keys agree but the raw
first names differ.

This table is explicitly a v1, not a finished cultural or linguistic
artifact. Real nickname usage is many-to-many (Bert can shorten Albert,
Robert, or Herbert); this table picks one canonical association per variant
and documents that trade-off in its own docstring. Extending it to non-English
naming and diminutive conventions needs review by someone with fluency in
that tradition, not another guess from this codebase. That is tracked as
follow-up work, not shipped here as a false completeness claim.

**(b) Phonetic blocking key.** `normalize.py` adds `soundex()`, a
hand-rolled American Soundex implementation, and `normalize_record` derives
`last_name_soundex`. `defaults.blocking_rules_for` adds it as an additional
blocking rule alongside the existing exact-match rules (additive, not a
replacement).

Soundex, not (Double) Metaphone, despite the ideation note's suggestion of a
"metaphone key". This project's dependency rule (0001) keeps everything
around the matcher on the standard library, and there is no metaphone
implementation in the standard library. Soundex is small and fully specified
enough to implement correctly in a few lines; a hand-rolled Metaphone is a
much larger surface to get subtly wrong, and for a *blocking* key (which only
needs recall, over-generating candidates the scorer then rejects) Soundex's
coarser grouping is an acceptable trade. Known limitation, stated plainly:
Soundex is sensitive to the first letter, so it does not catch a
transliteration variant that changes the leading sound (e.g. a written
"Jiménez" versus "Ximénez" get different codes). A future Metaphone
implementation, if the false-negative rate on that specific class turns out
to matter in practice, is the documented follow-up.

**(c) Term-frequency adjustment on `last_name`.** The "exact" level of
`defaults._last_name_comparison` sets `tf_adjustment_column="last_name"`.
Splink derives the frequency table from the batch being resolved at predict
time; no separate estimation step, no reference table shipped.

The weight is 0.05, not Splink's default of 1.0, and this needed real
tuning, not a guess: at weight 1.0, a surname that happens to repeat in a
small batch (the target user resolves a few dozen to a few hundred records
at a time, not a national reference population) swings the match probability
by two orders of magnitude on that fact alone. It regressed one of this
project's own committed fixtures (`test_typo_and_dateformat_duplicate_scores_for_auto`)
from an auto-merge to a coin flip, and pushed
`test_same_name_different_dob_lands_in_review_band` out of the review band
entirely. A weight of 0.05 keeps the adjustment directionally correct — a
common surname is still measurably discounted relative to a rare one, see
`test_term_frequency_adjustment_favors_the_rarer_surname` in
`tests/test_matching.py` — without letting a small batch's sampling noise
dominate the score. If real deployments turn out to run much larger batches
per run, this weight is the first knob to revisit, and the reasoning for
why it is small lives next to the number in `defaults.py`, not just here.

One Splink quirk worth recording so it is not rediscovered the hard way: a
`tf_adjustment_weight` of exactly `0.0` does not disable the adjustment
through the raw settings-dict path used here (`comparison_level_library.py`'s
`CustomLevel._convert_to_creator` treats it as "not configured" rather than
"configured to zero", a truthiness check on the value, not an identity
check). Any small positive weight behaves as expected; zero does not.

**(d) Compound surname comparison.** `normalize.py` adds `surname_tokens()`,
which takes the last two whitespace-separated tokens of the *raw* last-name
value (before `normalize_name` erases the spaces) and normalizes each one
independently. `normalize_record` derives `last_name_surname1` and
`last_name_surname2`. `defaults._last_name_comparison` adds a level that
fires when either surname token on one side matches either token on the
other, sitting below exact and above Jaro-Winkler "close" (agreeing on one
shared surname token is real evidence, but weaker than agreeing on the whole
string, and it is evidence a whole-string similarity metric usually cannot
see because the two full strings can be quite different in length).

This is a heuristic ("last two words are the surname pair"), not a rule
sourced from a naming-convention reference, and carries the same SME-review
caveat as the nickname table.

## Consequences

- All four levers are read-only additions to `defaults.py`,
  `normalize.py`, and `matching.py`'s frame builder; no change to the
  pipeline's state machine, the recipe schema, or the fail-closed band.
- A pandas/DuckDB interaction is worth naming for whoever touches this next:
  the derived columns (`first_name_nickname_key`, `last_name_soundex`,
  `last_name_surname1`, `last_name_surname2`) are populated with `""` for a
  missing value, not `None`. A batch where every record shares the same
  missing derived value produces an all-`None` pandas column with no type
  information; DuckDB then infers it as `INTEGER` rather than `VARCHAR`, and
  the comparison SQL fails to cast a real string against it. Every SQL
  condition that reads these columns already guards on `<> ''`, so the
  empty-string convention carries the same "no evidence" meaning without
  the type-inference hazard. See the comment in
  `matching.py::_records_to_frame`.
- **Not done here**: corpus-level measurement of the false-merge and
  missed-match rate deltas this pack produces, gated against the FIX-11
  synthetic corpus, as the ideation note's excellence bar asks for. FIX-11
  (`docs/ideation`'s synthetic corpus generator) is itself still unmerged
  work as of this decision; the eval-report wiring is the natural follow-up
  once it lands, so the per-class gain is measured on real generated fixtures
  instead of the hand-built pairs in `tests/test_matching.py`.
