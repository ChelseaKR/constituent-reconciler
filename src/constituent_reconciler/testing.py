"""Deterministic test and fixture helpers.

``make_pdf`` generates a minimal but valid PDF-1.4 file whose text content
pdfplumber can extract. It uses only the Python standard library, with no
dependency on reportlab, fpdf2, or any PDF-creation library, and its output is
byte-for-byte deterministic for a given input. The test suite uses it to build
throwaway intake forms, and ``eval/fixtures/extraction/make_fixtures.py`` uses
it to regenerate the committed labeled extraction fixtures.

``make_form_image`` is its counterpart for photographed and scanned pages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image


def make_pdf(lines: list[str]) -> bytes:
    """Build a minimal valid PDF with Helvetica text on one page.

    The resulting file passes pdfplumber's open() and extract_text(), which is
    all the callers need. Character-level word bounding boxes may not be present
    because the minimal font descriptor omits character widths; extract_words()
    may return an empty list on some pdfplumber versions, and that is acceptable
    (_find_span returns None rather than raising).
    """
    content_parts = ["BT", "/F1 12 Tf", "72 720 Td"]
    for i, line in enumerate(lines):
        if i > 0:
            content_parts.append("0 -18 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_parts.append(f"({escaped}) Tj")
    content_parts.append("ET")
    content = "\n".join(content_parts)
    content_b = content.encode("latin-1")

    parts: list[bytes] = []
    offsets: list[int] = []

    def w(b: bytes) -> None:
        parts.append(b)

    def pos() -> int:
        return sum(len(p) for p in parts)

    w(b"%PDF-1.4\n")

    offsets.append(pos())
    w(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n\n")

    offsets.append(pos())
    w(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n\n")

    offsets.append(pos())
    w(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n\n"
    )

    offsets.append(pos())
    w(b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n\n")

    offsets.append(pos())
    stream_header = f"5 0 obj\n<< /Length {len(content_b)} >>\nstream\n".encode("latin-1")
    w(stream_header)
    w(content_b)
    w(b"\nendstream\nendobj\n\n")

    xref_start = pos()
    n_obj = len(offsets) + 1
    xref = f"xref\n0 {n_obj}\n"
    xref += "0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n"
    w(xref.encode("latin-1"))

    trailer = f"trailer\n<< /Size {n_obj} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n"
    w(trailer.encode("latin-1"))

    return b"".join(parts)


def make_form_image(
    lines: list[str],
    *,
    size: tuple[int, int] = (1700, 2200),
    font_px: int = 44,
    background: int = 255,
) -> Image:
    """Render intake-form lines as an upright greyscale page image.

    The image counterpart of ``make_pdf``: black text in Pillow's bundled
    scalable font on a plain page, one entry per line, at a size Tesseract reads
    reliably. The default page is 1700 x 2200 pixels, a letter page at 200 dots
    per inch. Deterministic for a given Pillow, which bundles both the font and
    the rasterizer. Pillow is imported here rather than at module level so the
    rest of this module works without it.
    """
    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageFont

    image = PILImage.new("L", size, background)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_px)
    top = 160
    for line in lines:
        draw.text((150, top), line, fill=0, font=font)
        top += int(font_px * 1.9)
    return image


#: Set to "1" where a skip of the real-OCR tests must count as a failure (CI).
REQUIRE_TESSERACT_ENV = "CONSTITUENT_RECONCILER_REQUIRE_TESSERACT"


def require_real_ocr() -> None:
    """Return if real Tesseract OCR can run here; otherwise skip, or fail under CI.

    A test that exists to show what Tesseract reads cannot pass by substituting
    its answer. Where Tesseract, its ``eng`` and ``osd`` language data, or
    pytesseract is missing, this skips the calling test and names what is
    missing, unless ``REQUIRE_TESSERACT_ENV`` is ``"1"``: then the same absence
    fails it. CI sets that on the job that installs Tesseract, so a runner that
    lost the binary cannot turn every real-OCR test into a skip and report
    green. pytest is imported here, not at module level, so the rest of this
    module works without it.
    """
    import os

    import pytest

    from constituent_reconciler.extract.image import ocr_unavailable_reason

    reason = ocr_unavailable_reason()
    if reason is None:
        return
    if os.environ.get(REQUIRE_TESSERACT_ENV) == "1":
        pytest.fail(f"{REQUIRE_TESSERACT_ENV}=1 and real OCR cannot run: {reason}")
    pytest.skip(f"real OCR cannot run here: {reason}")
