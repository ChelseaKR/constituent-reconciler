"""Matcher backend interface.

A matcher backend takes normalized records and returns scored candidate pairs.
The seam exists for the same reason ``connectors/base.py`` exists: Splink plus
DuckDB plus pandas is the package's one heavy dependency, and containing it
behind an interface keeps engine churn out of the pipeline. The project still
does not implement record linkage; a backend wraps an existing matcher, it
never reimplements one.

This module imports only the standard library and the project's own dataclass
models. pandas and Splink live inside the Splink backend, nowhere else.

The contract every backend must honor, so banding in :mod:`decisions` stays
backend-independent:

* Each result tuple is ``(left_id, right_id, probability)`` with
  ``left_id < right_id``, so a pair's identity does not depend on the engine's
  row order.
* Results are sorted by ``(-probability, left_id, right_id)``: strongest pairs
  first, ties broken deterministically.
* Pairs with a probability below ``floor`` are omitted. Pairs a backend never
  scores (for example, outside its blocking rules) are implicit non-matches.
* Fewer than two records returns ``[]``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from constituent_reconciler.models import Record


class MatcherError(RuntimeError):
    """A matcher backend could not score pairs (bad config or engine failure)."""


@runtime_checkable
class MatcherBackend(Protocol):
    def score_pairs(
        self,
        records: Iterable[Record],
        fields: tuple[str, ...],
        *,
        prior: float,
        floor: float,
    ) -> list[tuple[str, str, float]]: ...
