"""Matching package: score candidate pairs behind a backend seam.

The public surface is ``score_pairs`` (module level, delegating to the default
backend so existing callers keep working), the ``MatcherBackend`` protocol and
``MatcherError`` from :mod:`base`, and ``get_backend``/``default_backend`` for
callers that construct a backend explicitly. The Splink implementation lives in
:mod:`splink_backend` and is imported lazily, so importing this package does
not pull in Splink or pandas.
"""

from __future__ import annotations

from collections.abc import Iterable

from constituent_reconciler import defaults
from constituent_reconciler.matching.base import MatcherBackend, MatcherError
from constituent_reconciler.models import Record

_default_backend: MatcherBackend | None = None


def get_backend(name: str) -> MatcherBackend:
    """Construct a matcher backend by name.

    ``"splink"`` is the only built-in backend. An unknown name raises
    ``ValueError`` rather than silently falling back, so a typo is caught
    instead of changing the matching behavior.
    """

    if name == "splink":
        from constituent_reconciler.matching.splink_backend import SplinkBackend

        return SplinkBackend()
    raise ValueError(f"unknown matcher backend {name!r}; the only built-in backend is 'splink'")


def default_backend() -> MatcherBackend:
    """Return the process-wide default backend (Splink), built on first use."""

    global _default_backend
    if _default_backend is None:
        _default_backend = get_backend("splink")
    return _default_backend


def score_pairs(
    records: Iterable[Record],
    fields: tuple[str, ...],
    *,
    prior: float = defaults.DEFAULT_PRIOR,
    floor: float = 0.001,
) -> list[tuple[str, str, float]]:
    """Score candidate pairs with the default backend.

    See :class:`MatcherBackend` for the result contract.
    """

    return default_backend().score_pairs(records, fields, prior=prior, floor=floor)


__all__ = [
    "MatcherBackend",
    "MatcherError",
    "default_backend",
    "get_backend",
    "score_pairs",
]
