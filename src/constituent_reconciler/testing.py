"""Deterministic test and fixture helpers.

``make_pdf`` generates a minimal but valid PDF-1.4 file whose text content
pdfplumber can extract. It uses only the Python standard library, with no
dependency on reportlab, fpdf2, or any PDF-creation library, and its output is
byte-for-byte deterministic for a given input. The test suite uses it to build
throwaway intake forms, and ``eval/fixtures/extraction/make_fixtures.py`` uses
it to regenerate the committed labeled extraction fixtures.
"""

from __future__ import annotations


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

    trailer = (
        f"trailer\n<< /Size {n_obj} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    )
    w(trailer.encode("latin-1"))

    return b"".join(parts)
