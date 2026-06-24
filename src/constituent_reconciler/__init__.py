"""constituent-reconciler: resolve and deduplicate nonprofit constituent records.

Public API surface for v0.x is intentionally small. The supported entry points
are the command-line interface (``reconcile``) and the functions re-exported
here. Everything else is internal and may change between minor releases until
v1.0.
"""

from __future__ import annotations

__version__ = "0.1.0"

from constituent_reconciler.models import (
    Band,
    Cluster,
    GoldenRecord,
    Pair,
    Record,
    RunResult,
)

__all__ = [
    "__version__",
    "Band",
    "Cluster",
    "GoldenRecord",
    "Pair",
    "Record",
    "RunResult",
]
