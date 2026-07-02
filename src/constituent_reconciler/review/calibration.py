"""Planted known-answer pairs for reviewer calibration (EXP-09).

The review queue can optionally mix in a few synthetic pairs whose correct
answer is known, so a session can report how well the reviewer's verdicts track
ground truth. The generator here is deterministic (a seeded ``random.Random``)
and pure: it reads no real record and touches no file, so two runs of the same
recipe plant the same pairs.

The ethics of planting are handled by construction, not by trust:

* **Disclosure.** The renderer shows a persistent banner saying planted pairs
  are present (the individual pairs are not pointed out); a reviewer is never
  deceived about being measured.
* **Visibly synthetic data.** Every planted record uses obviously fake values
  ("Calibration Sample" names, ``.invalid`` emails, ``555-01xx`` numbers), so
  a planted record can never be mistaken for a real constituent.
* **Never applied.** The session excludes planted pairs from the decisions
  file, so ``reconcile apply`` — which reads only that file — can never merge
  a synthetic record into real ones.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from constituent_reconciler.models import Band, Pair, Record

# The source label planted records carry, shown in the comparison table.
CALIBRATION_SOURCE = "calibration"

# Default seed for the deterministic generator. Fixed so the same recipe plants
# the same pairs on every run; a caller may pass another seed to vary the set.
DEFAULT_SEED = 9

# Field values for the two synthetic identities. A clear-merge pair uses the
# "sample" identity on both sides; a clear-non-merge pair opposes it with the
# "control" identity, which differs on every canonical field. All values are
# obviously fake: reserved example domains, the 555-01xx fictional phone range.
_SAMPLE = {
    "first_name": "Calibration",
    "last_name": "Sample",
    "dob": "1900-01-01",
    "email": "calibration.sample@example.invalid",
    "phone": "555-0100",
    "address": "1 Calibration Way",
}
_CONTROL = {
    "first_name": "Synthetic",
    "last_name": "Control",
    "dob": "1901-12-31",
    "email": "synthetic.control@example.invalid",
    "phone": "555-0199",
    "address": "99 Synthetic Court",
}


@dataclass(frozen=True)
class PlantedPair:
    """One planted pair: the scored pair, its two synthetic records, and the
    known answer (True means the correct verdict is approve/merge)."""

    pair: Pair
    left: Record
    right: Record
    known_answer: bool


def _record(unique_id: str, values: dict[str, str], fields: Sequence[str]) -> Record:
    raw = {name: values.get(name, f"synthetic {name}") for name in fields}
    # Normalized values mirror the raw ones (lower-cased), so the comparison
    # table's match/differ tags behave the same as for real records.
    return Record(
        unique_id=unique_id,
        source=CALIBRATION_SOURCE,
        raw=raw,
        normalized={name: value.lower() for name, value in raw.items()},
    )


def _identity(base: dict[str, str], index: int) -> dict[str, str]:
    """The base identity, made distinct per planted pair so no two repeat."""

    values = dict(base)
    values["last_name"] = f"{base['last_name']} {index}"
    values["email"] = base["email"].replace("@", f"{index}@")
    return values


def generate_calibration_pairs(
    count: int, fields: Sequence[str], *, seed: int = DEFAULT_SEED
) -> list[PlantedPair]:
    """Deterministically generate ``count`` planted pairs over the mapped fields.

    Answers are a near-even mix of clear merges (both records carry the same
    synthetic identity) and clear non-merges (the records differ on every
    mapped field), shuffled by a seeded ``random.Random`` so the queue's
    planted answers are not predictable from position alone. Raises
    ``ValueError`` on a negative count.
    """

    if count < 0:
        raise ValueError(f"calibration count must be zero or positive, got {count}")
    # Determinism is the requirement here, not unpredictability: this creates
    # synthetic test ordering and never generates a secret or security token.
    rng = random.Random(seed)  # noqa: S311
    # Half merges, half non-merges (the extra one is a merge), then shuffled.
    answers = [i % 2 == 0 for i in range(count)]
    rng.shuffle(answers)

    planted: list[PlantedPair] = []
    for i, should_merge in enumerate(answers, start=1):
        left_values = _identity(_SAMPLE, i)
        right_values = left_values if should_merge else _identity(_CONTROL, i)
        left = _record(f"CAL-{i:03d}-A", left_values, fields)
        right = _record(f"CAL-{i:03d}-B", right_values, fields)
        pair = Pair(
            left=left.unique_id,
            right=right.unique_id,
            # A plausible review-band score, jittered so planted pairs do not
            # all show the same probability. Display-only: the pair is already
            # routed to review by construction.
            probability=round(rng.uniform(0.80, 0.95), 4),
            band=Band.REVIEW,
        )
        planted.append(PlantedPair(pair=pair, left=left, right=right, known_answer=should_merge))
    return planted
