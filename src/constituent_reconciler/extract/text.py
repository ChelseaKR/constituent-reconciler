"""Offline extraction from plain-text and .eml intake files.

Applies the same label-adjacent patterns as the PDF extractor to a text body,
using only the standard library. Spans are line offsets (``TextSpan``) rather
than bounding boxes. For .eml files only the plain-text body is read:
attachments and non-text parts are skipped and never recurse into the PDF or
any other extraction path, so a hostile attachment cannot re-enter the
pipeline through an email.
"""

from __future__ import annotations

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from constituent_reconciler.extract.base import (
    FIELD_PATTERNS,
    ExtractedField,
    ExtractionResult,
    PageResult,
    page_confidence,
)
from constituent_reconciler.models import TextSpan


def _span_for_value(text: str, start: int, end: int, source_file: str) -> TextSpan:
    """Line-offset span for ``text[start:end]``.

    Lines are 1-indexed; columns are 0-indexed offsets within the line, end
    exclusive. The extraction patterns never capture across a newline, so the
    whole value sits on the line where it starts.
    """
    line_start = text.rfind("\n", 0, start) + 1
    return TextSpan(
        source_file=source_file,
        line=text.count("\n", 0, start) + 1,
        col_start=start - line_start,
        col_end=end - line_start,
    )


def _extract_body(body: str, source_file: str) -> ExtractionResult:
    """Run the shared field patterns over one text body as a single page."""
    confidence = page_confidence(body)
    page = PageResult(page_num=1, confidence=confidence)

    for field_name, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(body)
            if match:
                captured = match.group(1)
                value = captured.strip()
                if not value:
                    continue
                start = match.start(1) + (len(captured) - len(captured.lstrip()))
                span = _span_for_value(body, start, start + len(value), source_file)
                page.fields.append(
                    ExtractedField(
                        field_name=field_name,
                        value=value,
                        confidence=confidence,
                        span=span,
                    )
                )
                break

    result = ExtractionResult(source_file=source_file)
    result.pages.append(page)
    return result


def extract_text_file(path: Path) -> ExtractionResult:
    """Extract constituent fields from a plain-text intake file.

    The whole file is treated as one page. Undecodable bytes are replaced
    rather than raised, matching the fail-closed posture: a mangled file
    yields low confidence and lands in review, not a crash.
    """
    body = path.read_text(encoding="utf-8", errors="replace")
    return _extract_body(body, path.name)


def extract_eml(path: Path) -> ExtractionResult:
    """Extract constituent fields from an RFC 5322 .eml message.

    Only the plain-text body is read (``get_body`` with a text/plain
    preference). Attachments and non-text parts are ignored, deliberately: an
    attached document must not recurse into another extraction path. A message
    with no text/plain body yields one empty, zero-confidence page.
    """
    with path.open("rb") as handle:
        msg = BytesParser(policy=policy.default).parse(handle)

    body = ""
    if isinstance(msg, EmailMessage):
        part = msg.get_body(preferencelist=("plain",))
        if part is not None:
            content = part.get_content()
            if isinstance(content, str):
                body = content
    return _extract_body(body, path.name)


class TextExtractor:
    """Offline extractor for .txt and .eml intake files (stdlib only)."""

    def extract(self, path: Path) -> ExtractionResult:
        if path.suffix.lower() == ".eml":
            return extract_eml(path)
        return extract_text_file(path)
