"""Generate a seeded synthetic corpus with planted duplicate structure.

Produces the same shape ``examples/intake-demo/`` uses (an ``existing.csv``,
an ``incoming.csv``, a ``ground_truth.json`` of duplicate clusters, and a
``recipe.toml``) at any scale from 10^3 to 10^5 records, plus a
``labels.json`` that tags every planted duplicate and decoy with its
name-origin class and error channel, so a per-class recall breakdown (R5) and
a per-channel breakdown have real denominators.

Usage::

    python -m tools.corpusgen.generate --records 20000 --seed 20260707 \\
        --out-dir eval/large-corpus

Determinism: the same ``--seed`` and ``--records`` always produce byte-
identical output. Nothing here reads or writes real personal data; see the
package docstring in ``tools/corpusgen/__init__.py`` for the fictional-data
guarantee.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tools.corpusgen import errors, pools

# Relative channel weights for a planted true-duplicate pair. "exact" is the
# control (no error at all, still a genuine duplicate); the rest each stress
# one part of the pipeline. "dob_typo" is deliberately included even though
# it is expected to survive as a real mismatch (see errors.dob_typo) --- the
# gate's job is to route those to review, not to auto-merge them, and this
# corpus should contain enough of them to measure that honestly.
_CHANNEL_WEIGHTS: dict[str, float] = {
    "exact": 0.15,
    "typo_first": 0.15,
    "typo_last": 0.12,
    "nickname": 0.13,
    "transliteration": 0.08,
    "compound_surname": 0.05,
    "date_drift": 0.15,
    "dob_typo": 0.10,
    "address_variant": 0.12,
}

_DEFAULT_DUPLICATE_RATE = 0.35
_DEFAULT_DECOY_RATE = 0.06
_DOB_MIN_YEAR = 1940
_DOB_MAX_YEAR = 2008


@dataclass(frozen=True)
class Person:
    first_name: str
    last_name: str
    dob: str  # ISO
    name_class: str
    street_number: int
    street_name: str
    street_suffix: str
    directional: str
    unit: str
    city: str
    state: str
    zip_code: str
    true_email: str
    true_phone: str

    def address_long(self) -> str:
        parts = [str(self.street_number)]
        if self.directional:
            parts.append(self.directional)
        parts.append(self.street_name)
        parts.append(self.street_suffix)
        if self.unit:
            parts.append(f"{self.unit} {self.street_number % 90 + 1}")
        return " ".join(parts) + f", {self.city}, {self.state} {self.zip_code}"


def _make_person(rng: random.Random, index: int) -> Person:
    pool = rng.choice(pools.NAME_POOLS)
    first = rng.choice(pool.first_names)
    last = rng.choice(pool.last_names)
    year = rng.randint(_DOB_MIN_YEAR, _DOB_MAX_YEAR)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    city, state, zip_prefix = rng.choice(pools.CITIES)
    local = f"{first}.{last}.{index}".lower().replace(" ", "")
    area = rng.randint(200, 899)
    line = rng.randint(0, 99)
    return Person(
        first_name=first,
        last_name=last,
        dob=f"{year:04d}-{month:02d}-{day:02d}",
        name_class=pool.name_class,
        street_number=rng.randint(1, 9999),
        street_name=rng.choice(pools.STREET_NAMES),
        street_suffix=rng.choice(pools.STREET_SUFFIX_LONG),
        directional=rng.choice(pools.DIRECTIONAL_LONG),
        unit=rng.choice(pools.UNIT_LONG),
        city=city,
        state=state,
        zip_code=f"{zip_prefix}{rng.randint(100, 999)}",
        true_email=f"{local}@example.org",
        true_phone=f"({area}) 555-01{line:02d}",
    )


# Probability a row includes an email / phone at all. Intake forms often lack
# one or both, the way the demo fixture leaves most Email/Phone cells blank;
# when a duplicate's true email or phone IS given on both sides, it is given
# identically (it is the same real value), which is what makes it strong
# matching evidence rather than a coin flip that would otherwise force every
# planted duplicate to disagree on a comparison the matcher weighs heavily.
_EMAIL_FILL_RATE = 0.35
_PHONE_FILL_RATE = 0.30


def _row(
    record_id: str, person: Person, *, first: str, last: str, dob: str, rng: random.Random
) -> dict[str, str]:
    return {
        "id": record_id,
        "First Name": first,
        "Last Name": last,
        "DOB": dob,
        "Email": person.true_email if rng.random() < _EMAIL_FILL_RATE else "",
        "Phone": person.true_phone if rng.random() < _PHONE_FILL_RATE else "",
        "Address": person.address_long(),
        "Consent": "granted",
    }


# Each entry maps a channel name to a function of (rng, first, last, dob,
# address) -> (first, last, dob, address) for the incoming-side copy. Split
# out per channel (rather than one long if/elif) so each stays independently
# readable and testable, and no single function's branching grows with the
# channel count.
_Fields = tuple[str, str, str, str]


def _channel_exact(rng: random.Random, first: str, last: str, dob: str, address: str) -> _Fields:
    return first, last, dob, address


def _channel_typo_first(
    rng: random.Random, first: str, last: str, dob: str, address: str
) -> _Fields:
    return errors.typo_name(rng, first), last, dob, address


def _channel_typo_last(
    rng: random.Random, first: str, last: str, dob: str, address: str
) -> _Fields:
    return first, errors.typo_name(rng, last), dob, address


def _channel_nickname(rng: random.Random, first: str, last: str, dob: str, address: str) -> _Fields:
    alt = errors.nickname(rng, first, pools.NICKNAMES)
    return (alt or errors.typo_name(rng, first)), last, dob, address


def _channel_transliteration(
    rng: random.Random, first: str, last: str, dob: str, address: str
) -> _Fields:
    alt_first = errors.transliteration(rng, first, pools.TRANSLITERATIONS)
    if alt_first:
        return alt_first, last, dob, address
    alt_last = errors.transliteration(rng, last, pools.TRANSLITERATIONS)
    if alt_last:
        return first, alt_last, dob, address
    return errors.typo_name(rng, first), last, dob, address


def _channel_compound_surname(
    rng: random.Random, first: str, last: str, dob: str, address: str
) -> _Fields:
    alt = errors.compound_surname(rng, last, pools.COMPOUND_SURNAMES)
    return first, (alt or errors.typo_name(rng, last)), dob, address


def _channel_date_drift(
    rng: random.Random, first: str, last: str, dob: str, address: str
) -> _Fields:
    return first, last, errors.date_format_drift(rng, dob), address


def _channel_dob_typo(rng: random.Random, first: str, last: str, dob: str, address: str) -> _Fields:
    return first, last, errors.dob_typo(rng, dob), address


def _channel_address_variant(
    rng: random.Random, first: str, last: str, dob: str, address: str
) -> _Fields:
    return first, last, dob, errors.address_variant(rng, address)


_CHANNEL_FUNCS: dict[str, Callable[[random.Random, str, str, str, str], _Fields]] = {
    "exact": _channel_exact,
    "typo_first": _channel_typo_first,
    "typo_last": _channel_typo_last,
    "nickname": _channel_nickname,
    "transliteration": _channel_transliteration,
    "compound_surname": _channel_compound_surname,
    "date_drift": _channel_date_drift,
    "dob_typo": _channel_dob_typo,
    "address_variant": _channel_address_variant,
}


def _apply_channel(rng: random.Random, person: Person, channel: str) -> _Fields:
    """Return (first_name, last_name, dob, address) for the incoming-side copy."""

    func = _CHANNEL_FUNCS.get(channel)
    if func is None:
        raise ValueError(f"unknown error channel {channel!r}")
    return func(rng, person.first_name, person.last_name, person.dob, person.address_long())


@dataclass
class Corpus:
    existing_rows: list[dict[str, str]] = field(default_factory=list)
    incoming_rows: list[dict[str, str]] = field(default_factory=list)
    clusters: list[list[str]] = field(default_factory=list)
    labels: list[dict[str, str]] = field(default_factory=list)


def generate(
    *,
    total_records: int,
    seed: int,
    duplicate_rate: float = _DEFAULT_DUPLICATE_RATE,
    decoy_rate: float = _DEFAULT_DECOY_RATE,
) -> Corpus:
    """Build a full synthetic corpus. Deterministic for a given seed and size."""

    if total_records < 4:
        raise ValueError("total_records must be at least 4")
    if not 0.0 <= duplicate_rate < 1.0 or not 0.0 <= decoy_rate < 1.0:
        raise ValueError("duplicate_rate and decoy_rate must be in [0, 1)")
    if duplicate_rate + decoy_rate >= 1.0:
        raise ValueError("duplicate_rate + decoy_rate must be < 1")

    rng = random.Random(seed)
    n_identities = max(2, round(total_records / (1 + duplicate_rate + decoy_rate)))

    corpus = Corpus()
    channels = list(_CHANNEL_WEIGHTS)
    weights = list(_CHANNEL_WEIGHTS.values())

    existing_seq = 0
    incoming_seq = 0

    for i in range(n_identities):
        roll = rng.random()
        person = _make_person(rng, i)

        if roll < duplicate_rate:
            existing_seq += 1
            incoming_seq += 1
            eid = f"E{existing_seq:06d}"
            nid = f"N{incoming_seq:06d}"
            channel = rng.choices(channels, weights=weights, k=1)[0]
            first, last, dob, address_incoming = _apply_channel(rng, person, channel)
            corpus.existing_rows.append(
                _row(
                    eid,
                    person,
                    first=person.first_name,
                    last=person.last_name,
                    dob=person.dob,
                    rng=rng,
                )
            )
            incoming_person = person
            row = _row(nid, incoming_person, first=first, last=last, dob=dob, rng=rng)
            row["Address"] = address_incoming
            corpus.incoming_rows.append(row)
            corpus.clusters.append([eid, nid])
            corpus.labels.append(
                {
                    "kind": "duplicate",
                    "name_class": person.name_class,
                    "channel": channel,
                    "existing_id": eid,
                    "incoming_id": nid,
                }
            )
        elif roll < duplicate_rate + decoy_rate:
            # A decoy: two DIFFERENT people who happen to share first + last
            # name, with different dates of birth. A real duplicate too, but
            # of nobody -- these must never land in ground truth, and a
            # matcher that auto-merges on name alone will fail on them.
            twin = _make_person(rng, i + n_identities)
            existing_seq += 1
            incoming_seq += 1
            eid = f"E{existing_seq:06d}"
            nid = f"N{incoming_seq:06d}"
            corpus.existing_rows.append(
                _row(
                    eid,
                    person,
                    first=person.first_name,
                    last=person.last_name,
                    dob=person.dob,
                    rng=rng,
                )
            )
            corpus.incoming_rows.append(
                _row(
                    nid,
                    twin,
                    first=person.first_name,
                    last=person.last_name,
                    dob=twin.dob,
                    rng=rng,
                )
            )
            corpus.labels.append(
                {
                    "kind": "decoy",
                    "name_class": person.name_class,
                    "channel": "none",
                    "existing_id": eid,
                    "incoming_id": nid,
                }
            )
        else:
            # Singleton: appears on exactly one side, no planted duplicate.
            if rng.random() < 0.5:
                existing_seq += 1
                eid = f"E{existing_seq:06d}"
                corpus.existing_rows.append(
                    _row(
                        eid,
                        person,
                        first=person.first_name,
                        last=person.last_name,
                        dob=person.dob,
                        rng=rng,
                    )
                )
            else:
                incoming_seq += 1
                nid = f"N{incoming_seq:06d}"
                corpus.incoming_rows.append(
                    _row(
                        nid,
                        person,
                        first=person.first_name,
                        last=person.last_name,
                        dob=person.dob,
                        rng=rng,
                    )
                )
            corpus.labels.append(
                {
                    "kind": "singleton",
                    "name_class": person.name_class,
                    "channel": "none",
                    "existing_id": "",
                    "incoming_id": "",
                }
            )

    return corpus


_FIELDNAMES = ["id", "First Name", "Last Name", "DOB", "Email", "Phone", "Address", "Consent"]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _namespaced_clusters(clusters: list[list[str]]) -> list[list[str]]:
    return [[f"existing:{left}", f"incoming:{right}"] for left, right in clusters]


def write_corpus(corpus: Corpus, out_dir: Path, *, seed: int, total_records: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "existing.csv", corpus.existing_rows)
    _write_csv(out_dir / "incoming.csv", corpus.incoming_rows)

    ground_truth = {
        "note": (
            "Synthetic ground truth, generated by tools/corpusgen/generate.py "
            f"(seed={seed}, requested total_records={total_records}). Zero real "
            "personal data. Each cluster is a planted duplicate pair; decoy "
            "pairs (same name, different date of birth) are recorded in "
            "labels.json with kind='decoy' and are deliberately NOT here, "
            "the way the demo fixture's look-alike Marias are not. Record ids "
            "in this file are namespaced by source to match pipeline ingestion."
        ),
        "clusters": _namespaced_clusters(corpus.clusters),
    }
    (out_dir / "ground_truth.json").write_text(
        json.dumps(ground_truth, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "labels.json").write_text(
        json.dumps({"labels": corpus.labels}, indent=2) + "\n", encoding="utf-8"
    )

    recipe = f"""\
# Generated synthetic corpus. Regenerate with:
#   python -m tools.corpusgen.generate --records {total_records} --seed {seed} \\
#       --out-dir {out_dir}
# Do not hand-edit; edit the generator instead.

[input]
existing = "existing.csv"
incoming = "incoming.csv"
id_column = "id"

[mapping]
first_name = "First Name"
last_name = "Last Name"
dob = "DOB"
email = "Email"
phone = "Phone"
address = "Address"

[consent]
column = "Consent"

[thresholds]
prior = 0.01
auto = 0.97
review = 0.80

[policy]
pack = "default"
"""
    (out_dir / "recipe.toml").write_text(recipe, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--records",
        type=int,
        default=5000,
        help="approximate total record count across existing + incoming (default: 5000)",
    )
    parser.add_argument("--seed", type=int, default=20260707, help="RNG seed (default: 20260707)")
    parser.add_argument(
        "--out-dir", type=Path, required=True, help="directory to write the corpus into"
    )
    parser.add_argument(
        "--duplicate-rate",
        type=float,
        default=_DEFAULT_DUPLICATE_RATE,
        help=f"fraction planted as true duplicates (default: {_DEFAULT_DUPLICATE_RATE})",
    )
    parser.add_argument(
        "--decoy-rate",
        type=float,
        default=_DEFAULT_DECOY_RATE,
        help=f"fraction planted as same-name decoys (default: {_DEFAULT_DECOY_RATE})",
    )
    args = parser.parse_args(argv)

    corpus = generate(
        total_records=args.records,
        seed=args.seed,
        duplicate_rate=args.duplicate_rate,
        decoy_rate=args.decoy_rate,
    )
    write_corpus(corpus, args.out_dir, seed=args.seed, total_records=args.records)
    n_records = len(corpus.existing_rows) + len(corpus.incoming_rows)
    print(
        f"wrote {n_records} records ({len(corpus.existing_rows)} existing, "
        f"{len(corpus.incoming_rows)} incoming) to {args.out_dir}: "
        f"{len(corpus.clusters)} planted duplicate pairs, "
        f"{sum(1 for label in corpus.labels if label['kind'] == 'decoy')} decoy pairs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
