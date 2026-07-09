"""Synthetic corpus generator for the constituent-reconciler eval.

The committed demo eval (`eval/report.md`) runs on 27 records and 7 true
pairs; the false-merge gate passes at 0/6 with a Wilson interval of
[0%, 39%], which is a real gate but weak evidence. This package generates
larger (10^3 to 10^5 record) synthetic populations, entirely fictional, with
planted duplicate structure across configurable error channels (name typo,
nickname, transliteration variant, hyphenation/compound surname, date-format
drift, address variant) plus planted look-alike decoys that must NOT
auto-merge. It also tags every planted duplicate with a name-origin class, so
a per-class recall breakdown (feeding R5, "bias by name and address class")
has a real denominator instead of a handful of records.

Nothing here is real personal data. Names are drawn from public name-origin
pools; addresses are synthetic street/city combinations that do not resolve
to real locations; phone numbers use the reserved 555 exchange; emails use
`example.org`/`example.com`.

The error-model assumptions (which typo shapes are common, which nicknames
map to which given names, how often a hyphenated surname keeps both parts)
are the generator author's best approximation, not measured from real intake
data. Treat the per-channel and per-class rates this generator reports as
illustrative until E8 (real, consented pilot data) is available to calibrate
against; see docs/ideation/02-large-scale-fixes.md, FIX-11.
"""

from __future__ import annotations
