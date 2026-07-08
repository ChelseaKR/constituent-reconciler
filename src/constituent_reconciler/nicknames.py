"""A vendored, offline nickname table for given-name matching.

Jaro-Winkler similarity (``defaults._NAME_CLOSE``) catches typos and short
transliteration drift, but it does not catch a nickname pair like Bill and
William or Peggy and Margaret: the edit distance between the two strings is
too large for a character-similarity metric to see them as close, even though
a human reviewer recognizes them instantly as the same given name.

This module holds a small, curated table of common English-language nickname
groups and a lookup that reduces a normalized first name to a canonical group
key. Two names that share a group key are treated as nickname-equivalent by
``defaults._first_name_comparison`` even when they share no substring at all.

Scope and honesty notes, read before extending this table:

* This is a v1, English-centric table built from well-documented, widely
  published nickname conventions (the kind found in genealogy and census
  record-linkage references). It is a reasonable starting point, not a
  finished cultural or linguistic artifact.
* Nickname mapping is genuinely many-to-many in real usage (Bert can shorten
  Albert, Robert, Herbert, or Egbert). This table assigns each variant to the
  single most common association so the comparison stays a deterministic
  lookup rather than a fuzzy one. That is a real precision/recall trade a
  reviewer should know about, not an oversight.
* Nickname conventions outside Anglophone naming are not represented here
  (patronymic and matronymic diminutives, for example). Extending this table
  to other naming traditions needs review by someone with cultural and
  linguistic fluency in that tradition, not guesswork from this codebase.
  Tracked as follow-up work; see docs/decisions/0009-matching-depth-pack.md.
* Every key and value here must already be normalized the way
  ``normalize.normalize_name`` normalizes a name (lower-case, no accents, no
  punctuation, no spaces) so a table lookup never has to re-normalize.
"""

from __future__ import annotations

# Each group is a canonical (formal) name mapped to the informal variants that
# should be treated as the same given name. The canonical name is also a
# member of its own group so ``canonical_key`` is a pure lookup with one
# fallback case (a name absent from the table maps to itself).
_NICKNAME_GROUPS: dict[str, tuple[str, ...]] = {
    "william": ("will", "bill", "billy", "willy", "liam"),
    "robert": ("rob", "bob", "bobby", "robbie", "bert"),
    "richard": ("rich", "rick", "ricky", "dick", "richie"),
    "james": ("jim", "jimmy", "jamie"),
    "john": ("jack", "johnny", "jon", "jonny"),
    "joseph": ("joe", "joey", "jose"),
    "charles": ("charlie", "chuck", "chas"),
    "thomas": ("tom", "tommy"),
    "edward": ("ed", "eddie", "eddy", "ted", "teddy"),
    "michael": ("mike", "mikey", "mick", "micky"),
    "anthony": ("tony", "anton"),
    "alexander": ("alex", "al", "sandy", "xander"),
    "samuel": ("sam", "sammy"),
    "daniel": ("dan", "danny"),
    "andrew": ("andy", "drew"),
    "nicholas": ("nick", "nicky", "cole"),
    "steven": ("steve", "stevie"),
    "christopher": ("chris", "topher", "kit"),
    "frederick": ("fred", "freddy", "freddie"),
    "augustus": ("gus", "augie"),
    "henry": ("hank", "harry", "hal"),
    "gerald": ("gerry", "jerry"),
    "kenneth": ("ken", "kenny"),
    "lawrence": ("larry", "lars"),
    "leslie": ("les",),
    "ronald": ("ron", "ronnie"),
    "russell": ("russ", "rusty"),
    "stanley": ("stan",),
    "victor": ("vic",),
    "walter": ("walt", "wally"),
    "zachary": ("zach", "zack", "zac"),
    "albert": ("al", "bertie"),
    "arthur": ("art", "artie"),
    "benjamin": ("ben", "benny"),
    "eugene": ("gene",),
    "francis": ("frank", "frankie"),
    "gregory": ("greg",),
    "harold": ("harry", "hal"),
    "jeffrey": ("jeff", "jeffy"),
    "jonathan": ("jon", "jonny", "johnny"),
    "matthew": ("matt", "matty"),
    "nathaniel": ("nate", "nathan"),
    "patrick": ("pat", "paddy"),
    "peter": ("pete", "petey"),
    "philip": ("phil", "philly"),
    "raymond": ("ray",),
    "timothy": ("tim", "timmy"),
    "vincent": ("vince", "vinny"),
    "margaret": ("peg", "peggy", "meg", "maggie", "greta", "daisy"),
    "elizabeth": ("liz", "beth", "betty", "eliza", "libby", "betsy", "lizzie"),
    "katherine": ("kate", "katie", "kathy", "kit", "cathy", "kay"),
    "susan": ("sue", "susie", "suzy"),
    "patricia": ("pat", "patty", "trish", "tricia"),
    "barbara": ("barb", "barbie"),
    "deborah": ("deb", "debbie"),
    "virginia": ("ginny", "ginger"),
    "josephine": ("jo", "josie", "jojo"),
    "victoria": ("vicky", "vicki", "tori"),
    "gwendolyn": ("gwen", "wendy"),
    "cynthia": ("cindy",),
    "jacqueline": ("jackie", "jacky"),
    "jennifer": ("jen", "jenny", "jenn"),
    "nancy": ("ann", "annie", "nan"),
    "rebecca": ("becky", "becca"),
    "sandra": ("sandy",),
    "theresa": ("terry", "tess", "tessa"),
    "veronica": ("ronnie", "nica"),
    "dorothy": ("dot", "dottie", "dolly"),
    "florence": ("flo", "flossie"),
    "isabella": ("bella", "izzy"),
    "penelope": ("penny",),
    "stephanie": ("steph",),
    "wilhelmina": ("billie", "willa", "mina"),
}

def _build_variant_to_root() -> dict[str, str]:
    index: dict[str, str] = {}
    for root, variants in _NICKNAME_GROUPS.items():
        index[root] = root
        for variant in variants:
            # First writer wins on a collision (e.g. "harry" claimed by both
            # "henry" and "harold"); this is exactly the many-to-many
            # ambiguity documented in the module docstring.
            index.setdefault(variant, root)
    return index


_VARIANT_TO_ROOT: dict[str, str] = _build_variant_to_root()


def canonical_key(normalized_first_name: str) -> str:
    """Return the nickname-group key for an already-normalized first name.

    Returns the input unchanged when it is not in the table, so a lookup miss
    degrades to "no additional evidence" rather than an error. An empty
    string maps to an empty string, matching the null handling elsewhere in
    the normalize step.
    """

    if not normalized_first_name:
        return ""
    return _VARIANT_TO_ROOT.get(normalized_first_name, normalized_first_name)
