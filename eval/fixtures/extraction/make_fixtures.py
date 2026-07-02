"""Regenerate the labeled extraction fixture PDFs.

The PDFs in this directory are committed artifacts. This script rebuilds them
byte for byte from the line definitions below, using the same deterministic
stdlib generator the test suite uses (``constituent_reconciler.testing``), so
a reviewer can confirm the committed binaries contain exactly this text and
nothing else.

The ground-truth labels live in ``labels.json`` beside this script and are
maintained by hand, on purpose: they state what a correct extractor should
return, not what the current extractor does return. Editing a document here
means re-checking its labels.

Usage:

    .venv/bin/python eval/fixtures/extraction/make_fixtures.py [outdir]

``outdir`` defaults to this script's own directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

from constituent_reconciler.testing import make_pdf

# One entry per fixture PDF. See README.md in this directory for what each
# case exercises and for the labeling conventions.
DOCUMENTS: dict[str, list[str]] = {
    # The standard intake form: every canonical field, common labels.
    "form-standard.pdf": [
        "Intake Form",
        "First Name: Alice",
        "Last Name: Walker",
        "DOB: 1970-05-12",
        "Email: alice@example.org",
        "Phone: 555-123-4567",
    ],
    # Alternate labels and field ordering, plus differently formatted values.
    "form-alternate-order.pdf": [
        "Constituent Intake",
        "Phone: (415) 555-0100",
        "Email: b.rivera@example.org",
        "Given Name: Beatriz",
        "Surname: Rivera",
        "Birth Date: 03/09/1988",
    ],
    # Fields that should not parse: a correct extractor returns no dob and no
    # email for this document, so the labels omit them.
    "form-unparseable.pdf": [
        "Intake Form",
        "First Name: Casey",
        "Last Name: Nguyen",
        "DOB: unknown",
        "Email: none provided",
        "Phone: 555-234-9876",
    ],
    # A date written in words. A human labels it (dob 1988-03-09); the
    # deterministic extractor only parses numeric dates, so this is a planted
    # false negative that keeps the recall measurement honest.
    "form-worded-date.pdf": [
        "Intake Form",
        "First Name: Dana",
        "Last Name: Okafor",
        "DOB: March 9, 1988",
        "Email: dana.okafor@example.org",
    ],
}


def write_fixtures(outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, lines in DOCUMENTS.items():
        path = outdir / filename
        path.write_bytes(make_pdf(lines))
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent
    for path in write_fixtures(outdir):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
