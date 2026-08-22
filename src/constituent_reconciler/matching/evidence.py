"""Real, field-level Splink comparison evidence for one scored pair.

Splink decides match probability; this module never touches that decision. It
exists so the AI assistant package (``constituent_reconciler.assistant``) has
something true to narrate and to check its own claims against: which
comparison level fired for each field, the m/u probabilities that level
carries (the same hand-set constants in :mod:`constituent_reconciler.defaults`
a human reviewer of this codebase can already read), the realized Bayes
factor, and any term-frequency adjustment Splink applied. Every claim the
assistant package shows a reviewer is checked against a ``FieldEvidence``
value built here before display; nothing here is inferred, estimated, or
asked of a model.

Splink numbers a comparison's non-null levels in reverse declaration order:
the first (most-similar) level in a comparison's ``comparison_levels`` list
gets the *highest* ``comparison_vector_value`` ("gamma"), the last ("else")
level gets 0, and any null level is always -1, regardless of position. This
was confirmed empirically against a live
``Linker.inference.predict(retain_intermediate_calculation_columns=True)``
call (first_name: JOHN/JON -> gamma 2 -> "nickname", bf 6.0, matching
``_first_name_comparison``'s m=.06/u=.01; last_name: SMITH/SMITH -> gamma 4
-> "exact", bf 87.0, matching m=.87/u=.01; dob and email similarly), and is
exercised again in ``tests/test_matching_evidence.py``. ``_level_for_gamma``
replays that numbering; it does not guess it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd
from splink import DuckDBAPI, Linker, block_on

from constituent_reconciler import defaults
from constituent_reconciler.matching.base import MatcherError
from constituent_reconciler.matching.splink_backend import _records_to_frame
from constituent_reconciler.models import Record


@dataclass(frozen=True)
class FieldEvidence:
    """One field's real comparison evidence for one candidate pair.

    ``level_label`` is Splink's own ``label_for_charts`` for the level that
    fired ("exact", "close", "nickname", "given and family name transposed",
    "shared compound surname token", "null or empty", "different").
    ``bayes_factor`` is the realized value Splink's prediction carries for
    this field (``bf_<field>``): the m/u ratio for the level that fired.
    ``tf_adjustment_bayes_factor`` is set only when Splink applied a
    term-frequency adjustment (today, only ``last_name``'s exact level).
    """

    field: str
    left_value: str
    right_value: str
    level_label: str
    m_probability: float
    u_probability: float
    bayes_factor: float
    is_null_level: bool
    tf_adjustment_bayes_factor: float | None = None


@dataclass(frozen=True)
class PairEvidence:
    """All field evidence for one candidate pair, plus its overall score."""

    left_id: str
    right_id: str
    match_probability: float
    match_weight: float
    fields: tuple[FieldEvidence, ...]

    def field(self, name: str) -> FieldEvidence | None:
        """The evidence for one field, or ``None`` if that field was not compared."""
        return next((f for f in self.fields if f.field == name), None)


def _level_for_gamma(levels: list[dict[str, Any]], gamma: int) -> dict[str, Any]:
    """Map a Splink comparison_vector_value back to the level spec that produced it.

    See the module docstring for the numbering this replays. Raises
    ``MatcherError`` rather than guessing when a gamma value does not fit the
    comparison's own level count -- a mismatch here means the comparison
    spec and the prediction frame disagree, which is a bug to surface, not
    to paper over with a best-effort mapping.
    """

    null_levels = [lvl for lvl in levels if lvl.get("is_null_level")]
    non_null_levels = [lvl for lvl in levels if not lvl.get("is_null_level")]
    if gamma < 0:
        if not null_levels:
            raise MatcherError("gamma indicates a null level but the comparison declares none")
        return null_levels[0]
    index_from_start = len(non_null_levels) - 1 - gamma
    if not 0 <= index_from_start < len(non_null_levels):
        raise MatcherError(
            f"gamma value {gamma} out of range for {len(non_null_levels)} non-null levels"
        )
    return non_null_levels[index_from_start]


def _is_missing(value: object) -> bool:
    """Whether a raw cell value is a pandas/duckdb missing marker.

    A column of all-empty values can come back from DuckDB as ``pd.NA``
    rather than Python ``None``, and ``bool(pd.NA)`` itself raises
    ``TypeError`` -- so this checks ``pd.isna()`` on anything that is not
    already a plain Python ``None``, rather than relying on truthiness.
    """
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _row_float(row: pd.Series[Any], column: str, default: float) -> float:
    if column not in row or _is_missing(row[column]):
        return default
    return float(row[column])


def _row_str(row: pd.Series[Any], column: str) -> str:
    if column not in row or _is_missing(row[column]):
        return ""
    return str(row[column])


def comparison_evidence(
    records: Iterable[Record],
    fields: tuple[str, ...],
    pairs: Iterable[tuple[str, str]],
    *,
    prior: float = defaults.DEFAULT_PRIOR,
) -> dict[tuple[str, str], PairEvidence]:
    """Return real, field-level comparison evidence for the given pairs.

    Reruns the same deterministic Splink prediction :mod:`splink_backend`
    runs, with ``retain_intermediate_calculation_columns=True`` so every
    gamma/Bayes-factor/term-frequency column is present, then reduces the
    result to the requested ``(left_id, right_id)`` pairs (each reordered so
    ``left < right``, matching :func:`splink_backend.SplinkBackend.score_pairs`'s
    contract). Only a pair Splink's own blocking rules generate can appear
    here -- the same set that ``score_pairs`` can surface for review, so a
    pair asked for that Splink never blocked on is silently absent from the
    result rather than raising, which callers should treat as
    "no evidence available" and withhold, not as a bug.
    """
    record_list = list(records)
    wanted = {tuple(sorted(pair)) for pair in pairs}
    if len(record_list) < 2 or not wanted:
        return {}

    comparisons = defaults.comparisons_for(fields)
    levels_by_field: dict[str, list[dict[str, Any]]] = {
        c["output_column_name"]: c["comparison_levels"] for c in comparisons
    }

    frame = _records_to_frame(record_list, fields)
    settings = {
        "link_type": "dedupe_only",
        "probability_two_random_records_match": prior,
        "blocking_rules_to_generate_predictions": [
            block_on(column) for column in defaults.blocking_rules_for(fields)
        ],
        "comparisons": comparisons,
        "retain_intermediate_calculation_columns": True,
        "retain_matching_columns": True,
    }
    try:
        linker = Linker(frame, settings, db_api=DuckDBAPI())
        predictions = linker.inference.predict(threshold_match_probability=0.0)
    except Exception as exc:
        raise MatcherError(f"splink evidence prediction failed: {exc}") from exc
    predicted = predictions.as_pandas_dataframe()  # type: ignore[no-untyped-call]

    evidence: dict[tuple[str, str], PairEvidence] = {}
    for _, row in predicted.iterrows():
        left = str(row["unique_id_l"])
        right = str(row["unique_id_r"])
        if left > right:
            left, right = right, left
        key = (left, right)
        if key not in wanted:
            continue

        field_evidence: list[FieldEvidence] = []
        for field_name in fields:
            levels = levels_by_field.get(field_name)
            gamma_col = f"gamma_{field_name}"
            if levels is None or gamma_col not in row:
                continue
            gamma = int(row[gamma_col])
            level = _level_for_gamma(levels, gamma)
            is_null = bool(level.get("is_null_level", False))
            tf_col = f"bf_tf_adj_{field_name}"
            tf_bf = None if tf_col not in row else _row_float(row, tf_col, 1.0)
            field_evidence.append(
                FieldEvidence(
                    field=field_name,
                    left_value=_row_str(row, f"{field_name}_l"),
                    right_value=_row_str(row, f"{field_name}_r"),
                    level_label=str(level.get("label_for_charts", "")),
                    m_probability=0.0 if is_null else float(level.get("m_probability", 0.0)),
                    u_probability=0.0 if is_null else float(level.get("u_probability", 0.0)),
                    bayes_factor=_row_float(row, f"bf_{field_name}", 1.0),
                    is_null_level=is_null,
                    tf_adjustment_bayes_factor=tf_bf,
                )
            )
        evidence[key] = PairEvidence(
            left_id=left,
            right_id=right,
            match_probability=float(row["match_probability"]),
            match_weight=float(row["match_weight"]),
            fields=tuple(field_evidence),
        )
    return evidence
