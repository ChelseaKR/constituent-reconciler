"""Splink matcher backend.

The project does not implement record linkage. This backend configures Splink
with the pre-tuned settings from :mod:`defaults` and runs it offline against
the in-process DuckDB backend. The only job here is to turn normalized records
into a frame, run the prediction, and hand back scored pairs in the shape the
``MatcherBackend`` contract requires. Banding and clustering live in
:mod:`decisions` so this module has no policy in it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import pandas as pd
from splink import DuckDBAPI, Linker, block_on

from constituent_reconciler import defaults
from constituent_reconciler.matching.base import MatcherError
from constituent_reconciler.models import Record

# Splink and DuckDB are chatty at INFO. The CLI owns user-facing output, so quiet
# the library down to warnings and above.
logging.getLogger("splink").setLevel(logging.WARNING)

# Columns normalize.py derives alongside a canonical field, keyed by that
# field. These back the matching-depth comparisons and blocking rules in
# defaults.py (nickname key, phonetic key, compound-surname tokens); they are
# not canonical fields themselves; a recipe never maps them, so they only
# enter the frame here, riding along with the base field they are computed
# from.
_DERIVED_COLUMNS: dict[str, tuple[str, ...]] = {
    "first_name": ("first_name_nickname_key",),
    "last_name": ("last_name_soundex", "last_name_surname1", "last_name_surname2"),
}

# Derived columns computed from more than one canonical field, keyed by the set
# of fields they need. ``name_pair_key`` backs the transposed-name blocking rule
# and reads both name fields, so it cannot ride along with a single base field
# the way the columns above do.
_CROSS_FIELD_COLUMNS: dict[frozenset[str], tuple[str, ...]] = {
    frozenset({"first_name", "last_name"}): ("name_pair_key",),
}


def _records_to_frame(records: Iterable[Record], fields: tuple[str, ...]) -> pd.DataFrame:
    rows: list[dict[str, str | None]] = []
    for record in records:
        row: dict[str, str | None] = {"unique_id": record.unique_id}
        for field_name in fields:
            value = record.normalized.get(field_name, "")
            # Empty becomes NULL so the matcher's null level fires (no evidence)
            # rather than scoring it as a disagreement.
            row[field_name] = value if value else None
            for derived_column in _DERIVED_COLUMNS.get(field_name, ()):
                # Derived columns keep "" rather than None for a missing value.
                # A batch where every record shares the same missing derived
                # value can make pandas infer a non-string type; the comparison
                # conditions already treat "" as no evidence.
                row[derived_column] = record.normalized.get(derived_column, "")
        for needed, columns in _CROSS_FIELD_COLUMNS.items():
            if needed <= set(fields):
                for column in columns:
                    row[column] = record.normalized.get(column, "")
        rows.append(row)
    return pd.DataFrame(rows)


class SplinkBackend:
    """The default matcher: pre-tuned Splink over in-process DuckDB."""

    def score_pairs(
        self,
        records: Iterable[Record],
        fields: tuple[str, ...],
        *,
        prior: float = defaults.DEFAULT_PRIOR,
        floor: float = 0.001,
    ) -> list[tuple[str, str, float]]:
        """Score candidate pairs and return ``(left_id, right_id, probability)``.

        Only pairs the blocking rules generate are scored; everything else is an
        implicit non-match. ``floor`` drops near-zero pairs from the result so the
        review and drop bands are not flooded with obvious non-matches.
        """

        record_list = list(records)
        if len(record_list) < 2:
            return []

        frame = _records_to_frame(record_list, fields)
        settings = {
            "link_type": "dedupe_only",
            "probability_two_random_records_match": prior,
            "blocking_rules_to_generate_predictions": [
                block_on(column) for column in defaults.blocking_rules_for(fields)
            ],
            "comparisons": defaults.comparisons_for(fields),
            "retain_intermediate_calculation_columns": False,
            "retain_matching_columns": False,
        }

        try:
            linker = Linker(frame, settings, db_api=DuckDBAPI())
            predictions = linker.inference.predict(threshold_match_probability=floor)
        except Exception as exc:
            # Fail closed: an engine failure surfaces as the seam's own error,
            # it never degrades into an empty (all-non-match) result.
            raise MatcherError(f"splink prediction failed: {exc}") from exc
        # Splink ships no type stubs for this method; the result is a pandas frame.
        predicted = predictions.as_pandas_dataframe()  # type: ignore[no-untyped-call]

        scored: list[tuple[str, str, float]] = []
        for _, row in predicted.iterrows():
            left = str(row["unique_id_l"])
            right = str(row["unique_id_r"])
            probability = float(row["match_probability"])
            # Order the pair so identity does not depend on Splink's row order.
            if left > right:
                left, right = right, left
            scored.append((left, right, probability))
        scored.sort(key=lambda item: (-item[2], item[0], item[1]))
        return scored
