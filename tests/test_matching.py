from __future__ import annotations

from constituent_reconciler.matching import score_pairs
from constituent_reconciler.models import Record
from constituent_reconciler.normalize import normalize_record

FIELDS = ("first_name", "last_name", "dob", "email", "phone")


def _record(
    uid: str,
    source: str,
    first: str,
    last: str,
    dob: str,
    email: str = "",
    phone: str = "",
) -> Record:
    raw = {"first_name": first, "last_name": last, "dob": dob, "email": email, "phone": phone}
    return normalize_record(Record(unique_id=uid, source=source, raw=raw), FIELDS)


def _scores(records: list[Record]) -> dict[frozenset[str], float]:
    return {frozenset((left, right)): prob for left, right, prob in score_pairs(records, FIELDS)}


def test_typo_and_dateformat_duplicate_scores_for_auto() -> None:
    records = [
        _record("E3", "existing", "Jonathan", "Reyes", "1990-04-12"),
        _record("N2", "incoming", "Jonathon", "Reyes", "04/12/1990"),
        _record("X1", "existing", "Wei", "Chen", "1968-01-22"),
    ]
    scores = _scores(records)
    assert scores[frozenset(("E3", "N2"))] >= 0.97


def test_same_name_different_dob_lands_in_review_band() -> None:
    records = [
        _record("E8", "existing", "Maria", "Lopez", "1991-06-14"),
        _record("N7", "incoming", "Maria", "Lopez", "1996-02-22"),
    ]
    probability = _scores(records)[frozenset(("E8", "N7"))]
    assert 0.80 <= probability < 0.97


def test_nickname_rescued_by_matching_phone() -> None:
    records = [
        _record("E12", "existing", "Robert", "Smith", "1965-07-19", phone="(530) 555-0143"),
        _record("N10", "incoming", "Bob", "Smith", "1965-07-19", phone="530-555-0143"),
    ]
    assert _scores(records)[frozenset(("E12", "N10"))] >= 0.97


def test_nickname_alone_reaches_review_band() -> None:
    """A nickname pair with an exact surname but no DOB reaches review.

    Jaro-Winkler similarity between "margaret" and "peggy" is nowhere near
    ``defaults._NAME_CLOSE``; without the vendored nickname table (EXP-03)
    this pair would score far below the review threshold on surname
    agreement alone. With it, the nickname comparison level supplies enough
    additional evidence to land the pair in front of a reviewer instead of
    being silently dropped, even with no date of birth to corroborate it.
    """

    records = [
        _record("E20", "existing", "Margaret", "Chen", ""),
        _record("N20", "incoming", "Peggy", "Chen", ""),
    ]
    probability = _scores(records)[frozenset(("E20", "N20"))]
    assert 0.80 <= probability < 0.97


def test_compound_surname_scores_for_auto() -> None:
    """One shared surname token from a two-surname record reaches auto.

    ``normalize_name`` collapses "Cruz Gomez" into the single opaque token
    "cruzgomez", which never equals "cruz" and is not Jaro-Winkler-close to it
    either. The compound-surname comparison level reads the surname tokens
    normalize.py derives separately and recognizes the shared "cruz" token.
    """

    records = [
        _record("E21", "existing", "Ana", "Cruz Gomez", "1980-05-05"),
        _record("N21", "incoming", "Ana", "Cruz", "1980-05-05"),
    ]
    assert _scores(records)[frozenset(("E21", "N21"))] >= 0.97


def test_phonetic_blocking_surfaces_a_pair_exact_blocking_would_miss() -> None:
    """A surname spelling variant with no other shared field is still blocked.

    "Katz" and "Kats" share a Soundex code but no other field agrees (first
    name and DOB both differ, and email/phone are unset), so none of the
    other blocking rules (``dob``, ``last_name``, ``email``, ``first_name``)
    generate this pair; only the ``last_name_soundex`` rule does. This does
    not assert the pair should auto-merge or even reach review -- it asserts
    the pair is scored at all, which is what a blocking miss would silently
    prevent.
    """

    records = [
        _record("E22", "existing", "Miriam", "Katz", "1975-03-02"),
        _record("N22", "incoming", "Devora", "Kats", "1968-11-20"),
    ]
    scores = {
        frozenset((left, right)): prob
        for left, right, prob in score_pairs(records, FIELDS, floor=0.0)
    }
    assert frozenset(("E22", "N22")) in scores


def test_term_frequency_adjustment_favors_the_rarer_surname() -> None:
    """A shared common surname is weaker match evidence than a rare one.

    Two ambiguous pairs (different first name, different DOB, sharing only a
    last name) are scored against the same background population. The pair
    sharing the population's common surname ("Smith", repeated many times in
    the population) should score lower than the pair sharing a surname that
    appears nowhere else, because the term-frequency adjustment on
    ``last_name`` discounts agreement on a name that occurs by chance far
    more often.
    """

    background = [
        _record(f"S{i}", "existing", "Population", "Smith", f"19{50 + i:02d}-01-01")
        for i in range(20)
    ]
    common_pair = [
        _record("C1", "existing", "Alan", "Smith", "1955-01-01"),
        _record("C2", "incoming", "Brianna", "Smith", "1962-02-02"),
    ]
    rare_pair = [
        _record("R1", "existing", "Zoltan", "Nkemelu", "1980-01-01"),
        _record("R2", "incoming", "Yusuf", "Nkemelu", "1980-06-06"),
    ]
    records = background + common_pair + rare_pair
    scores = {
        frozenset((left, right)): prob
        for left, right, prob in score_pairs(records, FIELDS, floor=0.0)
    }
    assert scores[frozenset(("R1", "R2"))] > scores[frozenset(("C1", "C2"))]


def test_transposed_given_and_family_name_reaches_auto() -> None:
    """A duplicate filed with the two names in the opposite boxes still merges.

    Before the transposition comparison level, this pair was scored as two
    separate name disagreements, one on each field, for a single mistake made
    once. Two "different" levels firing together is a veto no amount of
    corroborating evidence recovers from, so the pair scored near zero however
    well the other fields agreed.
    """

    records = [
        _record("E30", "existing", "Wei", "Li", "1984-09-02", email="wl@example.org"),
        _record("N30", "incoming", "Li", "Wei", "1984-09-02", email="wl@example.org"),
    ]
    assert _scores(records)[frozenset(("E30", "N30"))] >= 0.97


def test_transposed_name_is_recognised_through_a_typo() -> None:
    """The level is Jaro-Winkler on each side, not equality.

    A transposition and an ordinary typo happen together often enough that an
    equality-only level would miss a large share of them, so the crossed pair
    is recognised on the same similarity that counts as close when the names
    are not crossed.
    """

    records = [
        _record("E31", "existing", "Joshua", "Wiechec", "1960-11-11", phone="530-555-0177"),
        _record("N31", "incoming", "Wiechec", "Joshzua", "1960-11-11", phone="530-555-0177"),
    ]
    assert _scores(records)[frozenset(("E31", "N31"))] >= 0.97


def test_transposition_blocking_surfaces_a_pair_no_other_rule_generates() -> None:
    """A crossed-name duplicate agreeing on nothing else is still scored.

    Every other blocking rule compares one column against itself, so a record
    whose names were entered in the opposite boxes agrees with its duplicate
    on none of them: not first name, not last name, not the surname Soundex,
    and here not date of birth or email either. Only ``name_pair_key``
    generates this pair. As with the phonetic-blocking test above, this does
    not assert the pair should merge or even reach review; it asserts the pair
    is scored at all, which is what a blocking miss silently prevents.
    """

    records = [
        _record("E32", "existing", "Tschirn", "Kale", "1955-02-14"),
        _record("N32", "incoming", "Kale", "Tschirn", ""),
    ]
    scores = {
        frozenset((left, right)): prob
        for left, right, prob in score_pairs(records, FIELDS, floor=0.0)
    }
    assert frozenset(("E32", "N32")) in scores


def test_crossed_names_alone_do_not_auto_merge_two_different_people() -> None:
    """Transposition is evidence, not a verdict.

    Some people genuinely carry another person's name reversed, because many
    surnames are also common given names. Two records that cross into each
    other but disagree on date of birth are two people, and the level's weight
    is deliberately set well below an exact given-name agreement so that the
    disagreeing fields still decide.
    """

    records = [
        _record("E33", "existing", "Thomas", "Campbell", "1961-07-10"),
        _record("N33", "incoming", "Campbell", "Thomas", "1964-01-29"),
    ]
    scores = {
        frozenset((left, right)): prob
        for left, right, prob in score_pairs(records, FIELDS, floor=0.0)
    }
    assert scores[frozenset(("E33", "N33"))] < 0.97


ADDRESS_FIELDS = ("first_name", "last_name", "dob", "address")


def _household_record(
    uid: str, source: str, first: str, last: str, dob: str, address: str
) -> Record:
    raw = {"first_name": first, "last_name": last, "dob": dob, "address": address}
    return normalize_record(Record(unique_id=uid, source=source, raw=raw), ADDRESS_FIELDS)


def test_household_members_sharing_an_address_do_not_auto_merge() -> None:
    """Two people in one household are not one person, at any name weight.

    This guards ``defaults._NAME_DIFFERENT_M``. Raising a name disagreement's
    m_probability lets the other fields be heard, which is the point of it,
    but the field it most obviously lets through is a shared address, and
    families and shelter residents share addresses. A wrong merge here would
    join two people's records, which under the DV policy pack is a safety
    failure and not only a quality one.

    Two siblings at one address, same surname, different given names and
    different dates of birth, must stay apart. The assertion is on the
    auto-merge band specifically: the pair may reasonably be shown to a
    reviewer, but it must never be merged without one.
    """

    records = [
        _household_record("E40", "existing", "Ana", "Okafor", "1994-03-08", "12 Alder St, Reno NV"),
        _household_record(
            "N40", "incoming", "Chidi", "Okafor", "1998-11-21", "12 Alder St, Reno NV"
        ),
    ]
    scores = {
        frozenset((left, right)): prob
        for left, right, prob in score_pairs(records, ADDRESS_FIELDS, floor=0.0)
    }
    assert scores[frozenset(("E40", "N40"))] < 0.97


def test_household_members_with_no_date_of_birth_do_not_auto_merge() -> None:
    """The same invariant with the corroborating field missing rather than disagreeing.

    A missing date of birth is the harder case: it is no evidence rather than
    evidence against, so the pair rests on a shared surname and a shared
    address alone. That must still not be enough to merge without a human.
    """

    records = [
        _household_record("E41", "existing", "Ana", "Okafor", "", "12 Alder St, Reno NV"),
        _household_record("N41", "incoming", "Chidi", "Okafor", "", "12 Alder St, Reno NV"),
    ]
    scores = {
        frozenset((left, right)): prob
        for left, right, prob in score_pairs(records, ADDRESS_FIELDS, floor=0.0)
    }
    assert scores[frozenset(("E41", "N41"))] < 0.97
