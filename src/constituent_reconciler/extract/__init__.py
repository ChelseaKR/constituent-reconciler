"""Extraction package: pull constituent fields from documents.

The public surface is small: ``PdfplumberExtractor`` for the offline PDF
default, ``SandboxedExtractor`` to run PDF extraction in a resource-limited
child process, ``TextExtractor`` for .txt and .eml intake files, ``make_seam``
to construct the appropriate cloud seam for a policy pack, and the base types
(``ExtractedField``, ``PageResult``, ``ExtractionResult``). ``SourceSpan`` and
``TextSpan`` live in ``constituent_reconciler.models`` because they are also
carried by ``Record.spans``.
"""

from constituent_reconciler.extract.base import (
    CloudSeam,
    ExtractedField,
    ExtractionResult,
    Extractor,
    PageResult,
)
from constituent_reconciler.extract.pdf import PdfplumberExtractor
from constituent_reconciler.extract.sandbox import SandboxedExtractor
from constituent_reconciler.extract.seam import BedrockSeam, LocalSeam, NoOpSeam, make_seam
from constituent_reconciler.extract.text import TextExtractor, extract_eml, extract_text_file

__all__ = [
    "BedrockSeam",
    "CloudSeam",
    "ExtractedField",
    "ExtractionResult",
    "Extractor",
    "LocalSeam",
    "NoOpSeam",
    "PageResult",
    "PdfplumberExtractor",
    "SandboxedExtractor",
    "TextExtractor",
    "extract_eml",
    "extract_text_file",
    "make_seam",
]
