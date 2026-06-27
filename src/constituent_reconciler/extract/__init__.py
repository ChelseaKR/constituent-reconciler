"""Extraction package: pull constituent fields from documents.

The public surface is small: ``PdfplumberExtractor`` for the offline default,
``make_seam`` to construct the appropriate cloud seam for a policy pack,
and the base types (``ExtractedField``, ``PageResult``, ``ExtractionResult``).
``SourceSpan`` lives in ``constituent_reconciler.models`` because it is also
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
from constituent_reconciler.extract.seam import BedrockSeam, NoOpSeam, make_seam

__all__ = [
    "BedrockSeam",
    "CloudSeam",
    "ExtractedField",
    "ExtractionResult",
    "Extractor",
    "NoOpSeam",
    "PageResult",
    "PdfplumberExtractor",
    "make_seam",
]
