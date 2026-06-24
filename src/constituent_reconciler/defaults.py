"""Pre-tuned matching defaults.

This module is the contribution of the project as much as the orchestration is.
A small nonprofit cannot label training pairs or tune match weights, so the
matcher ships with hand-set m and u probabilities per comparison level. No
training, no labeled data, no expectation-maximisation: the model is fully
specified here and runs deterministically.

m_probability is read as "given two records are the same person, how often does
this level fire". u_probability is "given they are different people, how often
does this level fire". The values below encode ordinary survey-data intuition:
two records for the same person usually agree on name and date of birth, but
typos and nicknames happen; two records for different people rarely agree on all
of them at once. They are deliberately conservative on the side that matters,
so that an auto-merge needs strong agreement and weak agreement is sent to a
human instead.

These are defaults, not law. A recipe can override the prior and the thresholds;
a later version will expose per-field overrides. The numbers are documented in
docs/decisions/0001-matcher-and-defaults.md so a reviewer can see the reasoning,
not just the constants.
"""

from __future__ import annotations

from typing import Any

# Prior odds that two records drawn at random are the same person. Constituent
# files are mostly distinct people, so this is low. It is the base rate the
# per-field evidence updates from.
DEFAULT_PRIOR: float = 0.01

# The fail-closed band. A pair at or above AUTO is merged without a human; a pair
# in [REVIEW, AUTO) is sent to the review queue; below REVIEW it is dropped. The
# gap between the two thresholds is where uncertainty lives, and by design it
# goes to a person rather than to an automatic merge.
DEFAULT_AUTO_THRESHOLD: float = 0.97
DEFAULT_REVIEW_THRESHOLD: float = 0.80

# Jaro-Winkler similarity above which two names count as "close" (nickname,
# typo, transliteration) rather than equal or different.
_NAME_CLOSE: float = 0.88


def _name_comparison(column: str) -> dict[str, Any]:
    """Three-level name comparison: exact, close (Jaro-Winkler), else."""

    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL '
                f'OR "{column}_l" = \'\' OR "{column}_r" = \'\'',
                "label_for_charts": "null or empty",
                "is_null_level": True,
            },
            {
                "sql_condition": f'"{column}_l" = "{column}_r"',
                "label_for_charts": "exact",
                "m_probability": 0.92,
                "u_probability": 0.01,
            },
            {
                "sql_condition": f'jaro_winkler_similarity("{column}_l", "{column}_r") '
                f">= {_NAME_CLOSE}",
                "label_for_charts": "close",
                "m_probability": 0.07,
                "u_probability": 0.03,
            },
            {
                "sql_condition": "ELSE",
                "label_for_charts": "different",
                "m_probability": 0.01,
                "u_probability": 0.96,
            },
        ],
    }


def _exact_comparison(column: str, m_yes: float, u_yes: float) -> dict[str, Any]:
    """Two-level exact comparison (used for dob, email, phone)."""

    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL '
                f'OR "{column}_l" = \'\' OR "{column}_r" = \'\'',
                "label_for_charts": "null or empty",
                "is_null_level": True,
            },
            {
                "sql_condition": f'"{column}_l" = "{column}_r"',
                "label_for_charts": "exact",
                "m_probability": m_yes,
                "u_probability": u_yes,
            },
            {
                "sql_condition": "ELSE",
                "label_for_charts": "different",
                "m_probability": 1.0 - m_yes,
                "u_probability": 1.0 - u_yes,
            },
        ],
    }


# Per-field comparison builders for the canonical schema. Email and phone get
# strong exact weights because agreement on them is rare by chance; date of
# birth is strong but not decisive on its own.
_COMPARISON_BUILDERS = {
    "first_name": lambda: _name_comparison("first_name"),
    "last_name": lambda: _name_comparison("last_name"),
    "dob": lambda: _exact_comparison("dob", m_yes=0.90, u_yes=0.01),
    "email": lambda: _exact_comparison("email", m_yes=0.85, u_yes=0.005),
    "phone": lambda: _exact_comparison("phone", m_yes=0.80, u_yes=0.01),
}


def comparisons_for(fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Build the comparison list for the active canonical fields."""

    return [_COMPARISON_BUILDERS[f]() for f in fields if f in _COMPARISON_BUILDERS]


def blocking_rules_for(fields: tuple[str, ...]) -> list[str]:
    """Columns to block on when generating candidate pairs.

    Blocking limits comparisons to records that already agree on something, so
    the run stays cheap. Several rules are used because no single one catches
    every true duplicate: a date-of-birth change of format is caught by the
    surname rule, a surname change is caught by the date rule, and so on. A true
    duplicate that agrees on none of these is out of scope for v0.1 and is
    reported honestly as a blocking miss in the eval.
    """

    candidates = ["dob", "last_name", "email", "first_name"]
    return [c for c in candidates if c in fields]
