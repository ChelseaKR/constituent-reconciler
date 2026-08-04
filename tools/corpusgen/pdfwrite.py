"""Deterministic multi-page text-layer PDF writer for the corpus generator.

The package already ships a stdlib PDF helper, ``constituent_reconciler
.testing.make_pdf``, and this module deliberately extends its approach rather
than pulling in reportlab or fpdf2. That helper is the wrong tool for the
corpus generator on two counts: it writes exactly one page per file, which at
corpus scale means one file per record, and it encodes text as latin-1, which
raises on the transliteration-channel names the generator plants (for example
``Nguyễn`` and ``Zdeněk``). Silently dropping those rows from the PDF side
would bias the PDF-carried population against exactly the name classes the
R5 audit exists to measure, so this writer handles them instead.

Mechanics: one Helvetica font object carries a custom single-byte encoding
built over the document's distinct characters. ASCII stays at its own code
point; every other character is assigned a code from 128 upward, declared in
an ``/Encoding`` ``/Differences`` array by ``uniXXXX`` glyph name, and mapped
back to Unicode by a ``/ToUnicode`` CMap, which is the path pdfminer (and so
pdfplumber, and so ``extract/pdf.py``) uses to decode extracted text. A
``/Widths`` array gives every code a fixed width so word grouping during
extraction has real advance values to work with. tests/test_corpusgen_pdf.py
holds the round trip to account: what the writer is told to say is exactly
what the repository's own extractor reads back, non-ASCII names included.

Everything here is dev tooling. Nothing in the package's runtime imports this
module, and it adds no dependency: output is built byte by byte from the
standard library, deterministic for a given input (no timestamps, no ids, no
compression), so a regenerated corpus digests identically.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

_PAGE_WIDTH = 612
_PAGE_HEIGHT = 792
_MARGIN_LEFT = 72
_TOP_BASELINE = 720
_FONT_SIZE = 11
_LEADING = 14
_GLYPH_WIDTH = 600

# Object numbers are fixed by construction: 1 catalog, 2 page tree, 3 font,
# 4 encoding, 5 ToUnicode CMap, then two objects per page (page, contents).
_FIRST_PAGE_OBJECT = 6


def _build_code_map(pages: Sequence[Sequence[str]]) -> dict[str, int]:
    """Assign one single-byte code to each distinct character in the document.

    Printable ASCII keeps its own code point. Everything else gets the next
    free code from 128 upward, in sorted character order so the assignment is
    deterministic. More than 128 distinct non-ASCII characters in one document
    cannot be represented single-byte; the writer refuses rather than mangling
    text (no corpus this generator produces comes near that bound).
    """

    charset: set[str] = set()
    for lines in pages:
        for line in lines:
            charset.update(line)
    code_map: dict[str, int] = {}
    next_code = 128
    for char in sorted(charset):
        point = ord(char)
        if 32 <= point <= 126:
            code_map[char] = point
            continue
        if next_code > 255:
            raise ValueError("more than 128 distinct non-ASCII characters in one document")
        code_map[char] = next_code
        next_code += 1
    return code_map


def _escape(encoded: bytes) -> bytes:
    """Escape the three bytes with meaning inside a PDF literal string."""

    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _content_stream(lines: Sequence[str], code_map: dict[str, int]) -> bytes:
    """One page's text: a line per row value, stepped down by the leading."""

    parts = [
        b"BT\n",
        f"/F1 {_FONT_SIZE} Tf\n{_LEADING} TL\n{_MARGIN_LEFT} {_TOP_BASELINE} Td\n".encode("ascii"),
    ]
    for i, line in enumerate(lines):
        if i:
            parts.append(b"T*\n")
        encoded = bytes(code_map[char] for char in line)
        parts.append(b"(" + _escape(encoded) + b") Tj\n")
    parts.append(b"ET\n")
    return b"".join(parts)


def _encoding_object(code_map: dict[str, int]) -> bytes:
    """The font's /Encoding dictionary, naming each custom code's glyph."""

    customs = sorted((code, char) for char, code in code_map.items() if code >= 128)
    differences = ""
    if customs:
        parts: list[str] = []
        previous = None
        for code, char in customs:
            if previous is None or code != previous + 1:
                parts.append(str(code))
            parts.append(f"/uni{ord(char):04X}")
            previous = code
        differences = " /Differences [" + " ".join(parts) + "]"
    return f"<< /Type /Encoding /BaseEncoding /WinAnsiEncoding{differences} >>".encode("ascii")


def _to_unicode_cmap(code_map: dict[str, int]) -> bytes:
    """A ToUnicode CMap covering every code, so extraction round-trips exactly."""

    entries = "\n".join(
        f"<{code:02X}> <{char.encode('utf-16-be').hex().upper()}>"
        for code, char in sorted((code, char) for char, code in code_map.items())
    )
    return (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\n"
        "begincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n"
        "<00> <FF>\n"
        "endcodespacerange\n"
        f"{len(code_map)} beginbfchar\n"
        f"{entries}\n"
        "endbfchar\n"
        "endcmap\n"
        "CMapName currentdict /CMap defineresource pop\n"
        "end\n"
        "end\n"
    ).encode("ascii")


def _stream_object(stream: bytes) -> bytes:
    return f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"


def _document_objects(pages: Sequence[Sequence[str]], code_map: dict[str, int]) -> list[bytes]:
    """The document's objects in file order; index i holds object number i+1."""

    kids = " ".join(f"{_FIRST_PAGE_OBJECT + 2 * i} 0 R" for i in range(len(pages)))
    max_code = max(code_map.values(), default=126)
    widths = " ".join([str(_GLYPH_WIDTH)] * (max_code - 31))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"),
        (
            f"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /FirstChar 32 "
            f"/LastChar {max_code} /Widths [{widths}] /Encoding 4 0 R /ToUnicode 5 0 R >>"
        ).encode("ascii"),
        _encoding_object(code_map),
        _stream_object(_to_unicode_cmap(code_map)),
    ]
    for i, lines in enumerate(pages):
        contents_ref = _FIRST_PAGE_OBJECT + 2 * i + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_WIDTH} {_PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {contents_ref} 0 R >>"
            ).encode("ascii")
        )
        objects.append(_stream_object(_content_stream(lines, code_map)))
    return objects


def render_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """Render one text-layer PDF: one page per entry, one text line per string.

    Deterministic for a given input, byte for byte. Raises ``ValueError`` on
    an empty page list; an intake document with no pages is a generator bug,
    not a case to paper over.
    """

    if not pages:
        raise ValueError("a PDF intake document needs at least one page")
    code_map = _build_code_map(pages)
    objects = _document_objects(pages, code_map)

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def write_intake_pdf(path: Path, pages: Sequence[Sequence[str]]) -> None:
    """Write ``render_pdf(pages)`` to ``path``."""

    path.write_bytes(render_pdf(pages))
