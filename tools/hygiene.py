"""Source-hygiene gate: no bare debt markers, no uncoded or unexplained suppressions.

The repository is clean of debt markers today; this gate keeps it that way
(remediation items CQ-34/CQ-35). It fails `make verify`, and therefore CI,
when a Python file under src/, tests/, or tools/ contains:

- a debt marker (the to-do, fix-me, triple-X, or hack tokens) anywhere. Real
  work belongs in the roadmap or an issue, not a comment that outlives its
  author;
- a ``noqa`` directive without an explicit rule code (a bare one silences
  every current and future rule on the line);
- a type-ignore comment without a bracketed error code, for the same reason;
- a ``pragma: no cover`` exclusion without a `` - reason`` suffix, so every
  coverage exclusion says why it is safe to exclude;
- a ``nosemgrep`` waiver without a rule id, so it names exactly what it
  waives.

The vendored ``_vendor`` tree is excluded: its runtime is bound by a reviewed
public-projection manifest, while its documentation can carry downstream scope
wording. The debt-marker tokens are assembled by concatenation below, and this
docstring avoids spelling any directive in its comment form, so the gate does
not trip on its own source.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCANNED_DIRS = ("src", "tests", "tools")
EXCLUDED_PARTS = frozenset({"_vendor"})

# Assembled, not written literally, so the gate does not flag its own source.
DEBT_MARKERS = ("TO" + "DO", "FIX" + "ME", "X" + "XX", "HA" + "CK")

_CHECKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(" + "|".join(DEBT_MARKERS) + r")\b"),
        "debt marker; move the work to the roadmap or an issue",
    ),
    (
        # A noqa directive (hash mark, then the token) not followed by a rule
        # code. The token inside ordinary comment prose, as in "see noqa
        # above", has no adjacent hash mark and is not matched.
        re.compile(r"#\s*noqa(?!:\s*[A-Z][A-Z0-9]*)"),
        "bare noqa; name the rule code being suppressed",
    ),
    (
        re.compile(r"#\s*type:\s*ignore(?!\[)"),
        "bare type: ignore; name the error code in brackets",
    ),
    (
        re.compile(r"#\s*pragma:\s*no cover(?!\s+-\s+\S)"),
        "unexplained coverage exclusion; append ' - reason'",
    ),
    (
        re.compile(r"#\s*nosemgrep(?!:\s*\S)"),
        "bare nosemgrep; name the rule id being waived",
    ),
)


def scan_line(line: str) -> list[str]:
    """Return the hygiene complaints for one source line (empty when clean)."""

    return [message for pattern, message in _CHECKS if pattern.search(line)]


def scan_file(path: Path) -> list[str]:
    """Return ``path:lineno: message`` complaints for one file."""

    complaints: list[str] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for message in scan_line(line):
            complaints.append(f"{path}:{lineno}: {message}")
    return complaints


def scan_tree(root: Path) -> list[str]:
    """Scan every non-vendored Python file under the gated directories."""

    complaints: list[str] = []
    for dirname in SCANNED_DIRS:
        for path in sorted((root / dirname).rglob("*.py")):
            if EXCLUDED_PARTS.intersection(path.parts):
                continue
            complaints.extend(scan_file(path))
    return complaints


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    complaints = scan_tree(root)
    for complaint in complaints:
        print(complaint, file=sys.stderr)
    if complaints:
        print(f"hygiene gate: {len(complaints)} finding(s)", file=sys.stderr)
        return 1
    print("hygiene gate: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
