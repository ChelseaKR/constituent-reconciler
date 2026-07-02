"""Shared fixtures and fakes for the test suite.

``make_pdf`` (in ``constituent_reconciler.testing``) generates a minimal but
valid PDF-1.4 file whose text content pdfplumber can extract. It uses only the
Python standard library, with no dependency on reportlab, fpdf2, or any
PDF-creation library. It lives in the package rather than here so that
``eval/fixtures/extraction/make_fixtures.py`` can regenerate the committed
labeled extraction fixtures from the same generator.

``FakeCivicrmTransport`` and ``FakeSalesforceTransport`` are queued-response
transports for the network connectors. The connector unit tests and the
conformance suite share them so every test observes requests the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from constituent_reconciler.testing import make_pdf


class FakeCivicrmTransport:
    """Returns queued responses and records every request for inspection."""

    def __init__(self, responses: list[tuple[int, dict[str, object]]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        self.calls.append((url, headers, body))
        status, payload = self._responses.pop(0)
        return status, json.dumps(payload).encode("utf-8")


class FakeSalesforceTransport:
    """Returns queued responses and records every request for inspection."""

    def __init__(self, responses: list[tuple[int, dict[str, object] | None]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def send(
        self, method: str, url: str, *, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        status, payload = self._responses.pop(0)
        raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        return status, raw


@pytest.fixture()
def intake_pdf(tmp_path: Path) -> Path:
    """A single-page intake form PDF with all five canonical fields present."""
    pdf_bytes = make_pdf(
        [
            "Intake Form",
            "First Name: Alice",
            "Last Name: Walker",
            "DOB: 1970-05-12",
            "Email: alice@example.org",
            "Phone: 555-123-4567",
        ]
    )
    path = tmp_path / "intake-form.pdf"
    path.write_bytes(pdf_bytes)
    return path


@pytest.fixture()
def low_confidence_pdf(tmp_path: Path) -> Path:
    """A PDF whose page has too little text to reach full confidence."""
    pdf_bytes = make_pdf(["Hi"])
    path = tmp_path / "low-confidence.pdf"
    path.write_bytes(pdf_bytes)
    return path


@pytest.fixture()
def scanned_pdf(tmp_path: Path) -> Path:
    """A single-page PDF with no text operators at all -- i.e. no text layer.

    Stands in for an image-only scan: pdfplumber's ``extract_text()`` returns
    "" for it just as it would for a real scanned page with no OCR baked in by
    the scanner, which is exactly the condition the OCR backend (``extract/
    ocr.py``) watches for. The page still rasterizes fine via pdfplumber's
    renderer, so `page.to_image()` works; tests supply the Tesseract output
    rather than depending on OCR actually reading anything from a blank page.
    """
    pdf_bytes = make_pdf([])
    path = tmp_path / "scanned-form.pdf"
    path.write_bytes(pdf_bytes)
    return path


@pytest.fixture()
def intake_pdf_folder(tmp_path: Path) -> Path:
    """A folder with one intake-form PDF and one CSV, for folder-ingest tests."""
    folder = tmp_path / "intake-docs"
    folder.mkdir()

    pdf_bytes = make_pdf(
        [
            "Intake Form",
            "First Name: Alice",
            "Last Name: Walker",
            "DOB: 1970-05-12",
            "Email: alice@example.org",
            "Phone: 555-123-4567",
        ]
    )
    (folder / "form-001.pdf").write_bytes(pdf_bytes)

    csv_content = (
        "id,first,last,dob,email,phone,consent\n"
        "X001,Bob,Smith,1985-07-04,bob@example.org,555-987-6543,granted\n"
    )
    (folder / "batch-001.csv").write_text(csv_content, encoding="utf-8")

    return folder
