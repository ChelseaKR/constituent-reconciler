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

With ``--pdf-share`` above zero, that share of the incoming rows is written
as seeded text-layer PDF intake documents instead of CSV rows (the UC-01
extract-half prerequisite, issue #78): the recipe then points ``incoming`` at
a directory holding the remaining ``incoming.csv`` beside the PDFs and turns
the pdfplumber extraction backend on, so the pipeline's own extractor does
real work over a mixed corpus. ``pdf_manifest.json`` records which rows each
document carries.

Determinism: the same ``--seed``, ``--records``, and ``--pdf-share`` always
produce byte-identical output. Nothing here reads or writes real personal
data; see the package docstring in ``tools/corpusgen/__init__.py`` for the
fictional-data guarantee.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tools.corpusgen import errors, pools
from tools.corpusgen.pdfwrite import write_intake_pdf

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

# Records per generated PDF intake document, pinned rather than configurable:
# the extractor makes one record per page, and a fixed page count keeps the
# document set (and therefore its digest) a pure function of seed, size, and
# share. Twenty-five pages models a scanned intake packet rather than one
# file per person, which at 10^4 rows would mean thousands of tiny files.
PDF_PAGES_PER_DOC = 25


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _namespaced_clusters(clusters: list[list[str]]) -> list[list[str]]:
    return [[f"existing:{left}", f"incoming:{right}"] for left, right in clusters]


def _select_pdf_row_indices(n_rows: int, pdf_share: float) -> list[int]:
    """Deterministic, evenly spaced choice of which incoming rows ride as PDFs.

    Even spacing keeps the PDF-carried population representative of the whole
    incoming side (duplicates, decoys, and singletons in their natural mix)
    without consuming any randomness, so the generated rows themselves stay
    byte-identical to a run of the same seed with ``pdf_share`` zero.
    """

    if pdf_share == 0.0 or n_rows == 0:
        return []
    count = min(max(1, round(n_rows * pdf_share)), n_rows)
    step = n_rows / count
    return [int(i * step) for i in range(count)]


def _pdf_page_lines(row: dict[str, str]) -> list[str]:
    """One intake-form page for one row, labeled the way the extractor reads.

    The labels match ``extract/base.py``'s field patterns, so the pipeline's
    own extractor recovers the row's values from the text layer. Empty email
    and phone cells are omitted entirely, the way a blank form field yields no
    labeled line.

    Two kinds of value are printed here and do not survive extraction, both
    because of what the extractor matches rather than anything this generator
    does: address and consent have no field pattern at all, and a date of
    birth the date-drift channel rendered in prose ("26 November 1942") does
    not match the numeric date pattern. tests/test_corpusgen_pdf.py pins that
    behaviour, and the stage-baseline report states it, so the mixed corpus's
    run counts are not read as a matcher regression.
    """

    lines = [
        "Constituent Intake Form",
        f"Record: {row['id']}",
        f"First Name: {row['First Name']}",
        f"Last Name: {row['Last Name']}",
        f"Date of Birth: {row['DOB']}",
    ]
    if row["Email"]:
        lines.append(f"Email: {row['Email']}")
    if row["Phone"]:
        lines.append(f"Phone: {row['Phone']}")
    lines.append(f"Address: {row['Address']}")
    lines.append(f"Consent: {row['Consent']}")
    return lines


def _remove_stale_layout(out_dir: Path) -> None:
    """Drop the other layout's files so a regenerated corpus has no strays.

    A directory that held a CSV-only corpus and is regenerated with a PDF
    share (or the reverse) must not leave the previous layout's inputs where
    the pipeline would ingest them alongside the new ones.
    """

    (out_dir / "incoming.csv").unlink(missing_ok=True)
    (out_dir / "pdf_manifest.json").unlink(missing_ok=True)
    incoming_dir = out_dir / "incoming"
    if incoming_dir.exists():
        shutil.rmtree(incoming_dir)


def _write_incoming_pdfs(corpus: Corpus, out_dir: Path, *, pdf_share: float) -> None:
    """Write the mixed incoming side: a CSV beside seeded PDF intake documents."""

    incoming_dir = out_dir / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    selected = _select_pdf_row_indices(len(corpus.incoming_rows), pdf_share)
    selected_set = set(selected)
    csv_rows = [row for i, row in enumerate(corpus.incoming_rows) if i not in selected_set]
    pdf_rows = [corpus.incoming_rows[i] for i in selected]
    _write_csv(incoming_dir / "incoming.csv", csv_rows)

    documents = []
    for doc_num, start in enumerate(range(0, len(pdf_rows), PDF_PAGES_PER_DOC), 1):
        chunk = pdf_rows[start : start + PDF_PAGES_PER_DOC]
        name = f"intake-{doc_num:04d}.pdf"
        write_intake_pdf(incoming_dir / name, [_pdf_page_lines(row) for row in chunk])
        documents.append({"file": name, "incoming_ids": [row["id"] for row in chunk]})

    manifest = {
        "note": (
            "Which incoming rows each generated PDF intake document carries, "
            "one record per page. The pipeline mints content-derived ids for "
            "records read from PDFs, so these incoming ids exist in the "
            "corpus files and in ground_truth.json but not in pipeline "
            "output; the PDF variant measures stage timing (UC-01), it does "
            "not feed id-based eval scoring."
        ),
        "pdf_share": pdf_share,
        "pages_per_document": PDF_PAGES_PER_DOC,
        "documents": documents,
    }
    (out_dir / "pdf_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def write_corpus(
    corpus: Corpus,
    out_dir: Path,
    *,
    seed: int,
    total_records: int,
    pdf_share: float = 0.0,
) -> None:
    """Write the corpus files. ``pdf_share`` above zero writes the mixed layout.

    At the default share of zero the layout and bytes are identical to what
    this function has always produced. Above zero, that share of the incoming
    rows is carried as text-layer PDF intake documents in an ``incoming/``
    directory beside the remaining ``incoming.csv``, and the recipe gains the
    pdfplumber extraction backend.
    """

    if not 0.0 <= pdf_share <= 1.0:
        raise ValueError(f"pdf_share must be in [0, 1], got {pdf_share}")
    out_dir.mkdir(parents=True, exist_ok=True)
    _remove_stale_layout(out_dir)
    _write_csv(out_dir / "existing.csv", corpus.existing_rows)
    if pdf_share == 0.0:
        _write_csv(out_dir / "incoming.csv", corpus.incoming_rows)
    else:
        _write_incoming_pdfs(corpus, out_dir, pdf_share=pdf_share)

    note = (
        "Synthetic ground truth, generated by tools/corpusgen/generate.py "
        f"(seed={seed}, requested total_records={total_records}). Zero real "
        "personal data. Each cluster is a planted duplicate pair; decoy "
        "pairs (same name, different date of birth) are recorded in "
        "labels.json with kind='decoy' and are deliberately NOT here, "
        "the way the demo fixture's look-alike Marias are not. Record ids "
        "in this file are namespaced by source to match pipeline ingestion."
    )
    if pdf_share > 0.0:
        note += (
            " A share of the incoming rows is carried as PDF intake documents "
            "(see pdf_manifest.json); the pipeline mints content-derived ids "
            "for records read from PDFs, so clusters naming a PDF-carried "
            "incoming id cannot be scored by id against pipeline output. The "
            "PDF variant exists for stage timing (UC-01), not eval scoring."
        )
    ground_truth = {
        "note": note,
        "clusters": _namespaced_clusters(corpus.clusters),
    }
    (out_dir / "ground_truth.json").write_text(
        json.dumps(ground_truth, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "labels.json").write_text(
        json.dumps({"labels": corpus.labels}, indent=2) + "\n", encoding="utf-8"
    )

    pdf_flag = f" --pdf-share {pdf_share}" if pdf_share > 0.0 else ""
    incoming_value = "incoming" if pdf_share > 0.0 else "incoming.csv"
    extract_section = '\n[extract]\nbackend = "pdfplumber"\n' if pdf_share > 0.0 else ""
    recipe = f"""\
# Generated synthetic corpus. Regenerate with:
#   python -m tools.corpusgen.generate --records {total_records} --seed {seed}{pdf_flag} \\
#       --out-dir {out_dir}
# Do not hand-edit; edit the generator instead.

[input]
existing = "existing.csv"
incoming = "{incoming_value}"
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
{extract_section}"""
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
    parser.add_argument(
        "--pdf-share",
        type=float,
        default=0.0,
        help=(
            "fraction of incoming rows written as text-layer PDF intake "
            "documents instead of CSV rows (default: 0.0, CSV-only)"
        ),
    )
    args = parser.parse_args(argv)

    corpus = generate(
        total_records=args.records,
        seed=args.seed,
        duplicate_rate=args.duplicate_rate,
        decoy_rate=args.decoy_rate,
    )
    write_corpus(
        corpus,
        args.out_dir,
        seed=args.seed,
        total_records=args.records,
        pdf_share=args.pdf_share,
    )
    n_records = len(corpus.existing_rows) + len(corpus.incoming_rows)
    n_pdf_rows = len(_select_pdf_row_indices(len(corpus.incoming_rows), args.pdf_share))
    pdf_summary = ""
    if n_pdf_rows:
        n_docs = -(-n_pdf_rows // PDF_PAGES_PER_DOC)
        pdf_summary = f", {n_pdf_rows} incoming rows carried by {n_docs} PDF documents"
    print(
        f"wrote {n_records} records ({len(corpus.existing_rows)} existing, "
        f"{len(corpus.incoming_rows)} incoming) to {args.out_dir}: "
        f"{len(corpus.clusters)} planted duplicate pairs, "
        f"{sum(1 for label in corpus.labels if label['kind'] == 'decoy')} decoy pairs"
        f"{pdf_summary}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
