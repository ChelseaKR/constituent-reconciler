"""Deterministic, non-model defense against a merge recommendation ever reaching a reviewer.

``prompts.REFUSAL_RULES`` is the belt: every assistant-package system prompt
instructs the model never to recommend a merge, claim two records are the
same person, tell a reviewer which record to keep, or claim certainty about
a match. This module is the suspenders: a plain pattern scanner that runs on
every model response, in English and Spanish, before it is shown to anyone.
A response the scanner flags is replaced wholesale with
``prompts.SCRUBBED_RESPONSE`` -- the reviewer never sees the model's actual
text, flagged or not, so a prompt-level failure alone can never surface a
recommendation.

This is intentionally a blunt instrument, not a classifier. It is biased
toward over-triggering: a false positive here costs a reviewer one generic
redirect message, which is always a safe outcome for this feature. A false
negative costs a merge recommendation reaching a human under an advisory
label, which is the one outcome the adversarial eval
(``eval/ai/adversarial_refusal.py``) exists to drive to zero. Treat any eval
failure as a signal to add a pattern here, not to relax the eval.
"""

from __future__ import annotations

import re

from constituent_reconciler.assistant.prompts import SCRUBBED_RESPONSE

# Each pattern is deliberately broad and polarity-insensitive: a claim that
# two records ARE the same person is exactly as prohibited as a claim they
# ARE NOT, and an instruction to merge is exactly as prohibited as an
# instruction not to -- prompts.REFUSAL_RULES bars stating a merge verdict
# in either direction, not just an affirmative one, so the scanner does not
# try to distinguish direction either.
_PROHIBITED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "merge_instruction",
        re.compile(
            r"\b(merge|combine|consolidat\w*)\b[^.]{0,40}\b"
            r"(them|these|it|both|the (two )?records?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "merge_instruction_es",
        re.compile(
            r"\b(fusiona\w*|combina\w*|consolida\w*|un(e|amos|irlos|irlas))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "same_person_claim",
        re.compile(
            r"\b(is|are|isn'?t|aren'?t|is not|are not|was|were)\b[^.]{0,25}\b"
            r"the same (person|individual|constituent|people)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "same_person_claim_es",
        re.compile(r"\b(son|es|no son|no es)\b[^.]{0,15}\bla misma persona\b", re.IGNORECASE),
    ),
    (
        "recommend_merge",
        re.compile(
            r"\b(i recommend|you should|i'?d suggest|i suggest|my recommendation is|"
            r"go ahead and|it'?s (safe|fine|ok) to)\b[^.]{0,60}"
            r"\b(merg\w*|combin\w*|keep|discard|not merg\w*)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "certainty_claim",
        re.compile(
            r"\b(definitely|certainly|100%|without a doubt|guaranteed?|for certain|"
            r"clearly (the same|a match|different people))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "certainty_claim_es",
        re.compile(
            r"\b(definitivamente|con (toda )?certeza|sin duda|garantizado)\b", re.IGNORECASE
        ),
    ),
    (
        "safe_to_merge",
        re.compile(r"\bsafe(ly)? (to )?merge\b", re.IGNORECASE),
    ),
    (
        "keep_or_discard_instruction",
        re.compile(
            r"\b(keep|discard|delete)\b[^.]{0,25}\b"
            r"(record|this one|the (left|right|first|second|newer|older))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "decide_for_reviewer",
        re.compile(
            r"\b(i'?ll|i will|let me) (decide|choose|merge)\b[^.]{0,25}\bfor you\b",
            re.IGNORECASE,
        ),
    ),
    (
        "yes_no_merge_answer",
        re.compile(
            r"^\s*(yes|no|s[ií]|no)[,.:]?\s+(merge|combine|they are|these are|it is|"
            r"fus[ií]on|combinar)",
            re.IGNORECASE,
        ),
    ),
)


def scan_for_prohibited_language(text: str) -> tuple[str, ...]:
    """Return the names of every prohibited-language pattern found in ``text``.

    An empty tuple means the text is clean by this scanner's rules; any
    non-empty result means the text must never be shown to a reviewer as-is.
    """

    return tuple(name for name, pattern in _PROHIBITED_PATTERNS if pattern.search(text))


def enforce(text: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(safe_text, matched_pattern_names)``.

    When the scanner finds nothing, ``safe_text`` is ``text`` unchanged.
    When it finds anything, ``safe_text`` is ``prompts.SCRUBBED_RESPONSE``
    and the caller must display that instead of the model's actual output,
    and must count the substitution (the adversarial eval's scoring depends
    on every substitution being counted, not silently absorbed).
    """

    hits = scan_for_prohibited_language(text)
    if hits:
        return SCRUBBED_RESPONSE, hits
    return text, ()
