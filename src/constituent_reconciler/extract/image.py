"""Photographed and scanned intake images, read through the local OCR backend.

Outreach staff photograph paper intake forms with a phone far more often than
they scan them to PDF. This module reads ``.jpg``, ``.jpeg``, ``.png``,
``.tif`` and ``.tiff`` files as documents. Nothing is rasterized: the image is
the page, and each frame of a multi-page TIFF is one more page. Every page goes
through the Tesseract path, field patterns and confidence rule an image-only
PDF page does (``extract/ocr.py``) and yields the same ``SourceSpan`` shape, so
a photographed form reaches the review queue exactly as a scanned one would.

Turning the page the right way up
---------------------------------
Two steps, in this order. The file's EXIF orientation tag is applied first: a
phone held sideways usually records that in the tag rather than rotating the
pixels, and applying the tag is exact. Then Tesseract's orientation detection
(OSD) may propose a quarter turn, for a page photographed or scanned sideways or
upside down with no tag. The proposal is not taken on trust. The page is read
both as it lies and turned, and the turned reading is kept only when its page
confidence is strictly higher. OSD's own orientation confidence on a short form
is low (between 3.6 and 4.7 on the six-line forms this was measured on), so a
threshold on it would be a number picked by hand; the confidence rule every
page already passes through is the judge instead.

Tesseract reads a page turned a quarter clockwise well on its own, and reads
nothing from one turned a quarter counter-clockwise or upside down. That is why
the tests turn their pages 90 and 180 degrees: a 270-degree page would pass with
the turn deleted.

Where a span points
-------------------
``x0``, ``top``, ``x1`` and ``bottom`` are pixels in the image as an EXIF-aware
viewer displays it, which is what a reviewer who opens the file sees. When the
kept reading came from the turned page, its boxes are turned back into that
frame (``_turn_back``).

Refusals
--------
Each fails closed with its reason, which the pipeline records as an unreadable
document and never as a page with no name:

* bytes no decoder recognizes, or image data that ends early;
* more frames than ``MAX_FRAMES``;
* a frame over the pixel budget, judged from its header before any pixel is
  decoded, so a decompression bomb is refused without being expanded. Pillow's
  own refusal starts at twice its ``MAX_IMAGE_PIXELS``, far above this budget,
  and is reported the same way;
* pixels deeper than eight bits. Pillow converts a 16-bit greyscale page to
  8-bit by clipping, which turns every value above 255 white; a scan read that
  way comes back as a blank page, so it is refused by name instead.

Pillow is imported only when an image is read. It comes with the ``ocr`` extra,
alongside ``pytesseract``.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from constituent_reconciler.extract.base import (
    IMAGE_SUFFIXES,
    ExtractionResult,
    PageResult,
    failed_closed,
)
from constituent_reconciler.extract.ocr import (
    _extract_ocr_fields,
    _ocr_confidence,
    _run_tesseract,
    _words_from_tesseract_data,
)

if TYPE_CHECKING:
    from PIL.Image import Image

__all__ = [
    "DEFAULT_MAX_IMAGE_PIXELS",
    "IMAGE_SUFFIXES",
    "MAX_FRAMES",
    "ImageOcrExtractor",
    "extract_image",
    "ocr_unavailable_reason",
]

DEFAULT_MAX_IMAGE_PIXELS = 50_000_000
"""The largest frame, in pixels, that is decoded at all.

A 48-megapixel phone photo (8064 x 6048) is under it. It bounds what one decoded
frame can take before the sandbox's own caps apply, and on macOS, where
``RLIMIT_AS`` is not enforced, it is the only bound on that memory there is.
"""

MAX_FRAMES = 50
"""The most frames (pages) one image file may hold."""

_QUARTER_TURNS = (90, 180, 270)

#: Pixel modes read as they are: none is deeper than eight bits per channel, so
#: converting to 8-bit greyscale loses no range.
_EIGHT_BIT_MODES = frozenset(
    {"1", "L", "LA", "La", "P", "PA", "RGB", "RGBA", "RGBa", "RGBX", "CMYK", "YCbCr", "LAB", "HSV"}
)

_MISSING_OCR = (
    "reading an image needs the optional OCR packages. Install them with: "
    "pip install 'constituent-reconciler[ocr]', and install the system Tesseract "
    "binary with its eng and osd language data (e.g. `brew install tesseract` or "
    "`apt-get install tesseract-ocr`)."
)


@dataclass(frozen=True)
class _Reading:
    """One Tesseract pass over one orientation of a page."""

    words: list[dict[str, Any]]
    text: str
    confidence: float


def _read(image: Any) -> _Reading:
    data = _run_tesseract(image)
    words, text = _words_from_tesseract_data(data, 1.0, 1.0)
    return _Reading(words=words, text=text, confidence=_ocr_confidence(words, text))


def _run_osd(image: Any) -> int:
    """The clockwise quarter turn that Tesseract's orientation detection proposes.

    Returns 90, 180 or 270, or 0 for no turn. OSD declines a page with too little
    text ("Too few characters"); that is the absence of a proposal, not a fault
    in the page, so the page is read as it lies. Any other Tesseract failure is
    raised: an installation missing the ``osd`` language data would otherwise
    read every sideways page as blank and say nothing about why. Isolated so
    tests can supply a proposal without the binary.
    """
    try:
        import pytesseract
    except ImportError as exc:
        raise ImportError(_MISSING_OCR) from exc
    try:
        result = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError as exc:
        if "Too few characters" in f"{exc}":
            return 0
        raise
    turn = int(result.get("rotate", 0))
    return turn if turn in _QUARTER_TURNS else 0


def _turn_back(word: dict[str, Any], turn: int, width: int, height: int) -> dict[str, Any]:
    """Map a box read from a page turned ``turn`` degrees clockwise back again.

    ``width`` and ``height`` are the size of the page before it was turned, and
    the box that comes back is in that page's pixels.
    """
    u0, v0, u1, v1 = word["x0"], word["top"], word["x1"], word["bottom"]
    if turn == 90:
        x0, top, x1, bottom = v0, height - u1, v1, height - u0
    elif turn == 180:
        x0, top, x1, bottom = width - u1, height - v1, width - u0, height - v0
    elif turn == 270:
        x0, top, x1, bottom = width - v1, u0, width - v0, u1
    else:
        raise ValueError(f"not a quarter turn: {turn}")
    return {**word, "x0": x0, "top": top, "x1": x1, "bottom": bottom}


def _read_page(page: Image, source_file: str, page_num: int) -> PageResult:
    """Read one upright-by-EXIF greyscale page, turning it when that reads better."""
    turn = _run_osd(page)
    reading = _read(page)
    if turn:
        turned = _read(page.rotate(-turn, expand=True))
        if turned.confidence > reading.confidence:
            width, height = page.size
            reading = _Reading(
                words=[_turn_back(word, turn, width, height) for word in turned.words],
                text=turned.text,
                confidence=turned.confidence,
            )
    fields = _extract_ocr_fields(
        reading.words, reading.text, reading.confidence, source_file, page_num
    )
    return PageResult(page_num=page_num, fields=fields, confidence=reading.confidence)


def _frame_count(image: Image) -> int:
    return int(getattr(image, "n_frames", 1))


def _where(index: int, frames: int) -> str:
    return "the image" if frames == 1 else f"page {index + 1} of the image"


def _refusal(image: Image, max_pixels: int) -> str | None:
    """Why this image is refused before any pixel is decoded, or ``None``.

    Frame counts and sizes come from the headers; seeking to a frame reads its
    header and nothing else.
    """
    frames = _frame_count(image)
    if frames > MAX_FRAMES:
        return f"the image holds {frames} pages, over the {MAX_FRAMES}-page cap; not decoded"
    for index in range(frames):
        image.seek(index)
        width, height = image.size
        if width * height > max_pixels:
            return (
                f"{_where(index, frames)} declares {width} x {height} = {width * height:,} "
                f"pixels, over the {max_pixels:,}-pixel budget; refused before decoding"
            )
    image.seek(0)
    return None


def extract_image(path: Path, *, max_pixels: int = DEFAULT_MAX_IMAGE_PIXELS) -> ExtractionResult:
    """Read a photographed or scanned intake image into one page per frame.

    Every refusal in the module docstring returns ``failed_closed`` with its
    reason. A missing OCR installation raises ``ImportError``, and a missing
    ``tesseract`` binary pytesseract's own error, exactly as the scanned-PDF
    path does; inside the sandbox either becomes the document's reason.
    """
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise ImportError(_MISSING_OCR) from exc

    try:
        with warnings.catch_warnings():
            # Between Pillow's warning and error thresholds it only warns; this
            # budget is lower than both, but the warning must not pass silently.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(path)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        return failed_closed(
            path.name, f"refused before decoding, over the decompression-bomb limit: {exc}"
        )
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        return failed_closed(path.name, f"could not decode image: {exc}")

    with image:
        frames = 1
        try:
            frames = _frame_count(image)
            refusal = _refusal(image, max_pixels)
        except (OSError, ValueError, EOFError, SyntaxError) as exc:
            return failed_closed(path.name, f"could not decode image: {exc}")
        if refusal is not None:
            return failed_closed(path.name, refusal)

        pages: list[PageResult] = []
        for index in range(frames):
            try:
                image.seek(index)
                upright = ImageOps.exif_transpose(image)
            except (OSError, ValueError, EOFError, SyntaxError) as exc:
                return failed_closed(path.name, f"could not decode {_where(index, frames)}: {exc}")
            if upright.mode not in _EIGHT_BIT_MODES:
                return failed_closed(
                    path.name,
                    f"{_where(index, frames)} has {upright.mode!r} pixels, deeper than eight "
                    "bits; converting them would clip every value above 255 to white, so "
                    "it is not read",
                )
            pages.append(_read_page(upright.convert("L"), path.name, index + 1))
    return ExtractionResult(source_file=path.name, pages=pages)


def ocr_unavailable_reason() -> str | None:
    """Why real OCR cannot run in this environment, or ``None`` when it can.

    Checks the three things a real read needs: the Python packages, a
    ``tesseract`` binary that answers, and its ``eng`` and ``osd`` language
    data. The eval command and the real-OCR tests ask this before running
    rather than skipping a document halfway.
    """
    try:
        import PIL  # noqa: F401
        import pytesseract
    except ImportError:
        return "pytesseract or Pillow is not installed (pip install 'constituent-reconciler[ocr]')"
    try:
        languages = set(pytesseract.get_languages(config=""))
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError, OSError) as exc:
        return f"the tesseract binary did not answer: {exc}"
    missing = sorted({"eng", "osd"} - languages)
    if missing:
        return f"tesseract has no {', '.join(missing)} language data"
    return None


class ImageOcrExtractor:
    """Extractor for photographed or scanned intake images (see ``extract_image``).

    Selected by the pipeline for an image file under ``[extract] backend =
    "pdfplumber+ocr"`` when the recipe turns the sandbox off; the sandboxed
    path runs ``extract_image`` in the child instead.
    """

    def __init__(self, *, max_pixels: int = DEFAULT_MAX_IMAGE_PIXELS) -> None:
        self.max_pixels = max_pixels

    def extract(self, path: Path) -> ExtractionResult:
        return extract_image(path, max_pixels=self.max_pixels)
