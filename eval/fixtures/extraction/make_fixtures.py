"""Regenerate the labeled extraction fixtures: PDFs and page images.

The PDFs and images in this directory are committed artifacts. This script
rebuilds them from the line definitions below, using the generators the test
suite uses (``constituent_reconciler.testing``), so a reviewer can confirm the
committed binaries contain exactly this text and nothing else. The PDFs come
from a stdlib generator and are byte-for-byte deterministic anywhere. The
images come from Pillow, which bundles the font, the rasterizer and the
encoders, so they are byte-for-byte deterministic for a given Pillow version
and may differ by a few bytes under another; their text does not change.

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

from constituent_reconciler.testing import make_form_image, make_pdf

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


# Two more intake forms, only ever scanned: the two pages of one TIFF, which is
# the shape a faxed intake arrives in.
FAX_PAGES: list[list[str]] = [
    [
        "Intake Form",
        "First Name: Evan",
        "Last Name: Park",
        "DOB: 1991-11-30",
        "Phone: 555-310-7788",
    ],
    [
        "Intake Form",
        "First Name: Farah",
        "Last Name: Haddad",
        "DOB: 07/04/1979",
        "Email: farah.haddad@example.org",
    ],
]


def write_images(outdir: Path) -> list[Path]:
    """Write the page-image fixtures. See README.md for what each one exercises.

    Four reuse a PDF fixture's text, so a document and its photograph are
    labeled alike and the two readers can be compared on the same content.
    """
    from PIL import Image

    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def save(filename: str, image: Image.Image, **options: object) -> None:
        path = outdir / filename
        image.save(path, **options)
        written.append(path)

    # A phone photo of the standard form: off-white paper, held a few degrees
    # off square, saved as a lossy JPEG.
    photo = make_form_image(DOCUMENTS["form-standard.pdf"], background=236).rotate(
        2.5, expand=True, fillcolor=236, resample=Image.Resampling.BICUBIC
    )
    save("photo-standard.jpg", photo.convert("RGB"), quality=70)
    # Photographed sideways with no orientation tag: only orientation detection
    # turns it upright, and Tesseract reads nothing from it as it lies.
    save(
        "photo-sideways.png",
        make_form_image(DOCUMENTS["form-alternate-order.pdf"]).rotate(90, expand=True),
    )
    # Fed into the scanner upside down.
    save("scan-upside-down.png", make_form_image(DOCUMENTS["form-unparseable.pdf"]).rotate(180))
    # A phone that recorded its sideways grip in the EXIF orientation tag
    # instead of turning the pixels.
    exif = Image.Exif()
    exif[0x0112] = 6
    save(
        "photo-exif-rotated.jpg",
        make_form_image(DOCUMENTS["form-worded-date.pdf"]).rotate(90, expand=True).convert("RGB"),
        quality=90,
        exif=exif.tobytes(),
    )
    # Two forms scanned as the two pages of one TIFF.
    first, second = (make_form_image(lines) for lines in FAX_PAGES)
    save(
        "scan-two-page.tif",
        first,
        save_all=True,
        append_images=[second],
        compression="tiff_lzw",
    )
    return written


def write_fixtures(outdir: Path) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, lines in DOCUMENTS.items():
        path = outdir / filename
        path.write_bytes(make_pdf(lines))
        written.append(path)
    return written + write_images(outdir)


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent
    for path in write_fixtures(outdir):
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
