"""constituent-reconciler: resolve and deduplicate nonprofit constituent records.

Public API surface for v0.x is intentionally small. The supported entry points
are the command-line interface (``reconcile``) and the functions re-exported
here. Everything else is internal and may change between minor releases until
v1.0.
"""

from __future__ import annotations

from importlib.metadata import version as _version

# pyproject.toml is the single source of truth for the version (REL-02); this
# reads the installed distribution's metadata rather than duplicating the
# string by hand, so the two can no longer drift apart.
__version__ = _version("constituent-reconciler")

from constituent_reconciler.models import (
    Band,
    Cluster,
    GoldenRecord,
    Pair,
    Record,
    RunResult,
    SourceSpan,
)

__all__ = [
    "__version__",
    "Band",
    "Cluster",
    "GoldenRecord",
    "Pair",
    "Record",
    "RunResult",
    "SourceSpan",
]
