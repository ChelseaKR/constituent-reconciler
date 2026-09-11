"""Photographed and scanned intake images: routing, refusals, orientation, spans.

Two kinds of test live here, and the split is deliberate. The first kind
substitutes Tesseract's answers (``_run_tesseract``, ``_run_osd``) or never
reaches them, so it runs anywhere: the refusals, the rule for keeping a turned
reading, the geometry of turning a box back, the routing. The second kind runs
the real Tesseract binary over pages rendered by ``testing.make_form_image``,
because what it asserts -- a photographed form yields its fields, a sideways one
is turned upright, a span points at the word -- is a claim about what Tesseract
reads, and a canned answer cannot make it. That kind takes the ``real_ocr``
fixture, which skips where Tesseract is absent unless
``CONSTITUENT_RECONCILER_REQUIRE_TESSERACT=1``, which CI sets so the suite cannot
pass there by skipping them.

Every refusal test also makes OCR raise if it is reached, so a refusal that
starts happening after decoding, or not at all, fails by name.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from constituent_reconciler import pipeline
from constituent_reconciler.config import ExtractConfig, Recipe
from constituent_reconciler.extract import image as image_module
from constituent_reconciler.extract.base import ExtractionResult
from constituent_reconciler.extract.image import _turn_back, extract_image
from constituent_reconciler.extract.sandbox import SandboxedExtractor
from constituent_reconciler.models import SourceSpan
from constituent_reconciler.testing import make_form_image

pytest.importorskip("PIL", reason="Pillow not installed")

_ALICE = [
    "Intake Form",
    "First Name: Alice",
    "Last Name: Walker",
    "DOB: 1970-05-12",
    "Email: alice@example.org",
    "Phone: 555-123-4567",
]
_ALICE_FIELDS = {
    "first_name": "Alice",
    "last_name": "Walker",
    "dob": "1970-05-12",
    "email": "alice@example.org",
    "phone": "555-123-4567",
}
_BEA = [
    "Constituent Intake",
    "Given Name: Beatriz",
    "Surname: Rivera",
    "Birth Date: 03/09/1988",
    "Email: b.rivera@example.org",
]
_BEA_FIELDS = {
    "first_name": "Beatriz",
    "last_name": "Rivera",
    "dob": "03/09/1988",
    "email": "b.rivera@example.org",
}


def _fields(result: ExtractionResult) -> dict[str, str]:
    return {field.field_name: field.value for page in result.pages for field in page.fields}


def _span(result: ExtractionResult, field_name: str) -> SourceSpan:
    for page in result.pages:
        for field in page.fields:
            if field.field_name == field_name:
                assert isinstance(field.span, SourceSpan)
                return field.span
    raise AssertionError(f"no {field_name} field in {_fields(result)}")


def _box(span: SourceSpan) -> tuple[float, float, float, float]:
    return (span.x0, span.top, span.x1, span.bottom)


def _close(a: tuple[float, ...], b: tuple[float, ...], tolerance: float) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))


def _png_header(width: int, height: int) -> bytes:
    """A PNG that declares ``width`` x ``height`` and carries ten bytes of data.

    Pillow reads the size from the header without decoding, so this is a
    decompression-bomb stand-in that costs nothing to build. Decoding it fails
    as truncated, which is what lets a test tell a refusal before decoding from
    a failure during it.
    """

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 10))
        + chunk(b"IEND", b"")
    )


def _refuse_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any OCR call fail the test: the document should never get that far."""

    def reached(*args: object, **kwargs: object) -> Any:
        raise AssertionError("OCR was reached on a document that should have been refused")

    monkeypatch.setattr(image_module, "_run_tesseract", reached)
    monkeypatch.setattr(image_module, "_run_osd", reached)


def _assert_refused(result: ExtractionResult, name: str, *fragments: str) -> None:
    assert result.source_file == name
    assert result.note is not None, "the image was read, not refused"
    for fragment in fragments:
        assert fragment in result.note, f"{fragment!r} not in {result.note!r}"
    assert [page.fields for page in result.pages] == [[]]


def _recipe(folder: Path, *, backend: str) -> Recipe:
    return Recipe(
        incoming=folder,
        mapping={"first_name": "first", "last_name": "last", "dob": "dob"},
        fields=("first_name", "last_name", "dob"),
        extract=ExtractConfig(backend=backend),
    )


# ---------------------------------------------------------------------------
# Geometry and the rule for keeping a turned reading (no Tesseract)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("turn", [90, 180, 270])
def test_turn_back_undoes_each_quarter_turn(turn: int) -> None:
    """Checked against Pillow's own rotation, not against the formula it inverts."""
    from PIL import Image, ImageDraw, ImageOps

    page = Image.new("L", (300, 200), 255)
    ImageDraw.Draw(page).rectangle((40, 30, 99, 59), fill=0)
    bbox = ImageOps.invert(page.rotate(-turn, expand=True)).getbbox()
    assert bbox is not None
    u0, v0, u1, v1 = bbox
    box = _turn_back({"x0": u0, "top": v0, "x1": u1, "bottom": v1}, turn, 300, 200)
    assert (box["x0"], box["top"], box["x1"], box["bottom"]) == (40, 30, 100, 60)


def test_turn_back_refuses_anything_but_a_quarter_turn() -> None:
    with pytest.raises(ValueError, match="not a quarter turn"):
        _turn_back({"x0": 0, "top": 0, "x1": 1, "bottom": 1}, 45, 10, 10)


def _tesseract_rows(lines: list[list[tuple[str, int, int]]], conf: float) -> dict[str, list[Any]]:
    """Canned ``image_to_data`` output: one entry per line of (word, left, top) triples."""
    data: dict[str, list[Any]] = {
        key: [] for key in ("text", "conf", "block_num", "par_num", "line_num")
    }
    data.update({"left": [], "top": [], "width": [], "height": []})
    for line_num, words in enumerate(lines, start=1):
        for text, left, top in words:
            data["text"].append(text)
            data["conf"].append(conf)
            data["block_num"].append(1)
            data["par_num"].append(1)
            data["line_num"].append(line_num)
            data["left"].append(left)
            data["top"].append(top)
            data["width"].append(10 * len(text))
            data["height"].append(12)
    return data


_READABLE = _tesseract_rows(
    [
        [("First", 10, 10), ("Name:", 70, 10), ("Alice", 130, 10)],
        [("Last", 10, 40), ("Name:", 70, 40), ("Walker", 130, 40)],
    ],
    conf=90.0,
)
_GARBLED = _tesseract_rows([[("xqzvkw", 5, 5)]], conf=10.0)


def test_the_turned_reading_is_kept_when_it_reads_better(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import Image

    page = Image.new("L", (300, 200), 255)
    monkeypatch.setattr(image_module, "_run_osd", lambda image: 90)
    # Only the turned page (200 x 300) reads; the page as it lies does not.
    monkeypatch.setattr(
        image_module,
        "_run_tesseract",
        lambda image: _READABLE if image.size == (200, 300) else _GARBLED,
    )
    result = image_module._read_page(page, "photo.png", 1)
    first = next(field for field in result.fields if field.field_name == "first_name")
    assert first.value == "Alice"
    assert isinstance(first.span, SourceSpan)
    # "Alice" sat at x 130-180, y 10-22 on the turned page; turned back, a
    # quarter counter-clockwise, it is at x 10-22, y 200-180 .. 200-130.
    assert _box(first.span) == (10.0, 20.0, 22.0, 70.0)


def test_the_page_as_it_lies_is_kept_when_the_turn_reads_worse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    page = Image.new("L", (300, 200), 255)
    monkeypatch.setattr(image_module, "_run_osd", lambda image: 90)
    monkeypatch.setattr(
        image_module,
        "_run_tesseract",
        lambda image: _READABLE if image.size == (300, 200) else _GARBLED,
    )
    result = image_module._read_page(page, "photo.png", 1)
    first = next(field for field in result.fields if field.field_name == "first_name")
    assert isinstance(first.span, SourceSpan)
    assert _box(first.span) == (130.0, 10.0, 180.0, 22.0)


def test_osd_declining_a_sparse_page_means_it_is_read_as_it_lies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytesseract = pytest.importorskip("pytesseract")

    def decline(*args: object, **kwargs: object) -> Any:
        raise pytesseract.TesseractError(1, "Too few characters. Skipping this page")

    monkeypatch.setattr(pytesseract, "image_to_osd", decline)
    assert image_module._run_osd(object()) == 0


def test_any_other_osd_failure_is_raised_not_read_as_no_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytesseract = pytest.importorskip("pytesseract")

    def broken(*args: object, **kwargs: object) -> Any:
        raise pytesseract.TesseractError(1, "Failed loading language 'osd'")

    monkeypatch.setattr(pytesseract, "image_to_osd", broken)
    with pytest.raises(pytesseract.TesseractError, match="osd"):
        image_module._run_osd(object())


@pytest.mark.parametrize(("proposed", "kept"), [(90, 90), (180, 180), (270, 270), (0, 0), (45, 0)])
def test_osd_proposes_only_quarter_turns(
    monkeypatch: pytest.MonkeyPatch, proposed: int, kept: int
) -> None:
    pytesseract = pytest.importorskip("pytesseract")
    monkeypatch.setattr(pytesseract, "image_to_osd", lambda *args, **kwargs: {"rotate": proposed})
    assert image_module._run_osd(object()) == kept


# ---------------------------------------------------------------------------
# Refusals: each one names its reason and never reaches OCR (no Tesseract)
# ---------------------------------------------------------------------------


def test_the_pixel_budget_refuses_before_any_pixel_is_decoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_ocr(monkeypatch)
    path = tmp_path / "huge.png"
    # 64 megapixels: over this budget, and under both of Pillow's own limits,
    # so nothing but the budget can refuse it before its data is decoded.
    path.write_bytes(_png_header(8000, 8000))
    _assert_refused(
        extract_image(path),
        "huge.png",
        "8000 x 8000 = 64,000,000 pixels",
        "over the 50,000,000-pixel budget; refused before decoding",
    )


def test_a_header_past_pillows_own_bomb_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_ocr(monkeypatch)
    path = tmp_path / "bomb.png"
    path.write_bytes(_png_header(20000, 20000))
    _assert_refused(
        extract_image(path), "bomb.png", "refused before decoding, over the decompression-bomb"
    )


def test_pillows_bomb_warning_is_a_refusal_not_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between Pillow's two thresholds it only warns; a budget raised past it must not."""
    _refuse_ocr(monkeypatch)
    path = tmp_path / "large.png"
    path.write_bytes(_png_header(10000, 10000))
    _assert_refused(
        extract_image(path, max_pixels=200_000_000),
        "large.png",
        "refused before decoding, over the decompression-bomb",
    )


def test_bytes_no_decoder_recognizes_are_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_ocr(monkeypatch)
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"this is not an image")
    _assert_refused(extract_image(path), "photo.jpg", "could not decode image")


def test_image_data_that_ends_early_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_ocr(monkeypatch)
    buffer = io.BytesIO()
    make_form_image(_ALICE).save(buffer, "JPEG")
    data = buffer.getvalue()
    path = tmp_path / "photo.jpg"
    path.write_bytes(data[: len(data) // 3])
    _assert_refused(extract_image(path), "photo.jpg", "could not decode the image", "truncated")


def test_more_frames_than_the_cap_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    _refuse_ocr(monkeypatch)
    monkeypatch.setattr(image_module, "MAX_FRAMES", 2)
    frames = [Image.new("L", (16, 16), 255) for _ in range(3)]
    path = tmp_path / "fax.tif"
    frames[0].save(path, "TIFF", save_all=True, append_images=frames[1:])
    _assert_refused(extract_image(path), "fax.tif", "holds 3 pages, over the 2-page cap")


def test_a_page_deeper_than_eight_bits_is_refused_not_read_as_white(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    _refuse_ocr(monkeypatch)
    path = tmp_path / "scan.png"
    Image.new("I;16", (32, 32), 40000).save(path)
    _assert_refused(extract_image(path), "scan.png", "deeper than eight bits")


# ---------------------------------------------------------------------------
# Routing and the run's accounting (no Tesseract: every image here is refused)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a.jpg", "a.JPEG", "a.png", "a.tif", "a.TIFF"])
@pytest.mark.parametrize(
    ("backend", "kind", "reason"),
    [
        ("pdfplumber+ocr", "image", ""),
        ("none", "", 'image extraction disabled (extract.backend = "none")'),
        ("pdfplumber", "", 'read only through OCR (extract.backend = "pdfplumber+ocr")'),
        ("bedrock", "", 'this recipe sets "bedrock"'),
    ],
)
def test_an_image_is_routed_only_under_the_ocr_backend(
    tmp_path: Path, name: str, backend: str, kind: str, reason: str
) -> None:
    got_kind, got_reason = pipeline._route(Path(name), _recipe(tmp_path, backend=backend))
    assert got_kind == kind
    assert reason in got_reason
    assert bool(got_reason) == (not kind)


def test_an_over_budget_and_a_corrupt_image_are_unreadable_and_the_run_goes_on(
    tmp_path: Path,
) -> None:
    """Both of #143's refusal criteria, through the real sandboxed pipeline."""
    folder = tmp_path / "intake"
    folder.mkdir()
    (folder / "batch.csv").write_text("first,last,dob\nBob,Smith,1985-07-04\n", encoding="utf-8")
    (folder / "huge.png").write_bytes(_png_header(8000, 8000))
    (folder / "broken.jpg").write_bytes(b"not an image at all")

    result = pipeline.run(_recipe(folder, backend="pdfplumber+ocr"))
    ingest = result.ingest

    reasons = {
        Path(document.path).name: document.reason for document in ingest.documents_unreadable
    }
    assert set(reasons) == {"huge.png", "broken.jpg"}
    assert "over the 50,000,000-pixel budget" in reasons["huge.png"]
    assert "could not decode image" in reasons["broken.jpg"]
    # Neither is a page read and found blank, and neither stopped the run.
    assert (ingest.pages_extracted, ingest.pages_dropped) == (0, 0)
    assert [record.raw["first_name"] for record in result.records.values()] == ["Bob"]
    assert {str(folder / "huge.png"), str(folder / "broken.jpg")} <= set(ingest.files_read)


# ---------------------------------------------------------------------------
# Real Tesseract
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("real_ocr")
def test_a_photographed_form_yields_its_fields_with_spans_on_the_page(tmp_path: Path) -> None:
    from PIL import Image

    photo = make_form_image(_ALICE, background=236).rotate(
        2.5, expand=True, fillcolor=236, resample=Image.Resampling.BICUBIC
    )
    path = tmp_path / "photo.jpg"
    photo.convert("RGB").save(path, quality=70)

    result = extract_image(path)
    assert result.note is None
    assert _fields(result) == _ALICE_FIELDS
    width, height = photo.size
    for page in result.pages:
        for field in page.fields:
            span = field.span
            assert isinstance(span, SourceSpan)
            assert (span.source_file, span.page) == ("photo.jpg", 1)
            assert 0 <= span.x0 < span.x1 <= width
            assert 0 <= span.top < span.bottom <= height


@pytest.mark.usefixtures("real_ocr")
def test_a_sideways_photo_is_turned_upright_and_its_spans_point_into_it_as_shown(
    tmp_path: Path,
) -> None:
    upright = make_form_image(_ALICE)
    upright.save(tmp_path / "upright.png")
    upright.rotate(90, expand=True).save(tmp_path / "sideways.png")

    reference = extract_image(tmp_path / "upright.png")
    sideways = extract_image(tmp_path / "sideways.png")
    assert _fields(sideways) == _ALICE_FIELDS
    # The file is the upright page turned a quarter counter-clockwise, which
    # puts a point (x, y) of the upright page at (y, width - x) in the file.
    width = upright.size[0]
    up = _span(reference, "first_name")
    expected = (up.top, width - up.x1, up.bottom, width - up.x0)
    assert _close(_box(_span(sideways, "first_name")), expected, tolerance=2)


@pytest.mark.usefixtures("real_ocr")
def test_an_upside_down_scan_is_read(tmp_path: Path) -> None:
    make_form_image(_ALICE).rotate(180).save(tmp_path / "scan.png")
    assert _fields(extract_image(tmp_path / "scan.png")) == _ALICE_FIELDS


@pytest.mark.usefixtures("real_ocr")
def test_the_exif_orientation_tag_is_applied_and_spans_follow_it(tmp_path: Path) -> None:
    """Orientation detection alone would recover the fields; only the spans tell."""
    from PIL import Image

    upright = make_form_image(_ALICE)
    upright.save(tmp_path / "upright.png")
    exif = Image.Exif()
    exif[0x0112] = 6  # stored a quarter counter-clockwise: turn clockwise to show
    upright.rotate(90, expand=True).convert("RGB").save(
        tmp_path / "phone.jpg", quality=92, exif=exif.tobytes()
    )

    phone = extract_image(tmp_path / "phone.jpg")
    assert _fields(phone) == _ALICE_FIELDS
    # Shown with its tag applied, the photo is the upright page, so its spans
    # sit where the upright page's do.
    reference = _span(extract_image(tmp_path / "upright.png"), "first_name")
    assert _close(_box(_span(phone, "first_name")), _box(reference), tolerance=6)


@pytest.mark.usefixtures("real_ocr")
def test_each_frame_of_a_multi_page_tiff_is_a_page_of_one_document(tmp_path: Path) -> None:
    path = tmp_path / "fax.tif"
    make_form_image(_ALICE).save(
        path, "TIFF", save_all=True, append_images=[make_form_image(_BEA)], compression="tiff_lzw"
    )
    result = extract_image(path)
    assert [page.page_num for page in result.pages] == [1, 2]
    assert {field.field_name: field.value for field in result.pages[0].fields} == _ALICE_FIELDS
    assert {field.field_name: field.value for field in result.pages[1].fields} == _BEA_FIELDS
    spans = [field.span for field in result.pages[1].fields]
    assert all(isinstance(span, SourceSpan) and span.page == 2 for span in spans)


@pytest.mark.usefixtures("real_ocr")
def test_an_image_read_in_the_sandbox_matches_the_in_process_read(tmp_path: Path) -> None:
    make_form_image(_ALICE).rotate(90, expand=True).save(tmp_path / "sideways.png")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    sandboxed = SandboxedExtractor(image=True, scratch_root=scratch).extract(
        tmp_path / "sideways.png"
    )
    assert sandboxed.note is None
    assert _fields(sandboxed) == _ALICE_FIELDS == _fields(extract_image(tmp_path / "sideways.png"))
    assert list(scratch.iterdir()) == []
