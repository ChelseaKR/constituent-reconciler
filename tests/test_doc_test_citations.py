"""Every test this repository's prose cites must exist, and be where prose says.

The threat model, the model and data cards, the retention model and the ADRs
all make their claims checkable the same way: a mitigation bullet ends by
naming the test that pins it. That convention is only worth the reader's
trust while the names are real. A renamed or deleted test leaves the prose
asserting a guarantee nothing enforces any more, and the prose reads exactly
as it did the day it was true. Nothing was checking the names.

Two shapes are scanned, both unambiguous enough to resolve mechanically:

* ``tests/test_something.py`` -- the file must exist;
* ``tests/test_something.py::test_a_specific_case`` -- the file must exist
  and must define that test function.

A bare ``test_a_specific_case`` written in prose with no file beside it is
deliberately not checked. Documents quote such names historically, including
one (``test_consent_blocks_export``) that ``docs/CLAIMS-AUDIT.md`` cites
precisely to record that it never existed, and a checker that failed on those
would be pushing the docs to rewrite their own history.

There is no allowlist, and the cost of that lands on prose describing a
citation that was once wrong: it has to describe the mistake rather than
reproduce it. That is a smaller price than an allowlist, which would need
maintaining and would be the obvious place for a real stale citation to hide.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

#: A ``tests/`` path, optionally followed by pytest's ``::`` node separator
#: and one test function name.
_CITATION = re.compile(r"tests/([A-Za-z0-9_/-]+\.py)(?:::([A-Za-z0-9_]+))?")


def _markdown_files() -> list[Path]:
    """Every prose file in the repository, and nothing from a build tree.

    Enumerated from explicit roots rather than an ``rglob`` over the whole
    checkout, so ``node_modules``, ``.venv`` and the pytest cache cannot
    quietly widen or narrow the scan depending on what a developer has
    installed locally.
    """

    files = sorted(REPO_ROOT.glob("*.md"))
    files += sorted(REPO_ROOT.glob("docs/**/*.md"))
    files += sorted(REPO_ROOT.glob(".github/**/*.md"))
    return files


def _defined_tests(path: Path) -> set[str]:
    """The names of every test function one test module defines."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    }


def citations(text: str) -> list[tuple[str, str | None]]:
    """Every ``(relative path, test name or None)`` a piece of prose cites."""

    return [(match.group(1), match.group(2)) for match in _CITATION.finditer(text)]


def unresolvable(text: str) -> list[str]:
    """The citations in ``text`` that no file or test function answers."""

    problems: list[str] = []
    for rel, name in citations(text):
        target = TESTS_DIR / rel
        if not target.is_file():
            problems.append(f"tests/{rel} does not exist")
        elif name is not None and name not in _defined_tests(target):
            problems.append(f"tests/{rel} does not define {name}")
    return problems


def test_every_cited_test_file_and_function_exists() -> None:
    """The check itself: no document may name a test that is not there."""

    problems = [
        f"{path.relative_to(REPO_ROOT)}: {problem}"
        for path in _markdown_files()
        for problem in unresolvable(path.read_text(encoding="utf-8"))
    ]
    assert not problems, "documentation cites tests that do not exist:\n" + "\n".join(problems)


def test_the_scan_reaches_the_documents_it_is_meant_to_cover() -> None:
    """Guard the guard: a narrowed scan must not pass by finding nothing.

    Without this, deleting a glob above, or tightening the citation pattern
    until it matches nothing, would leave the check green while it enforced
    nothing at all. The counts are floors well under today's numbers, so
    ordinary editing does not trip them.
    """

    scanned = _markdown_files()
    names = {path.name for path in scanned}
    assert {"THREAT-MODEL.md", "DATA-FLOW-AND-RETENTION.md", "CLAUDE.md"} <= names
    assert len(scanned) >= 30

    found = [
        citation for path in scanned for citation in citations(path.read_text(encoding="utf-8"))
    ]
    assert len(found) >= 40
    assert sum(1 for _, name in found if name is not None) >= 5


def test_an_invented_citation_is_reported() -> None:
    """Negative control: the scanner must fail something, not just pass everything."""

    assert unresolvable("see `tests/test_no_such_module.py`") == [
        "tests/test_no_such_module.py does not exist"
    ]
    assert unresolvable("see `tests/test_consent.py::test_no_such_case`") == [
        "tests/test_consent.py does not define test_no_such_case"
    ]


def test_a_real_citation_is_not_reported() -> None:
    """Positive control: a citation that resolves must stay quiet."""

    assert unresolvable("see `tests/test_consent.py`") == []
    assert (
        unresolvable(
            "see `tests/test_assistant_consent_filter.py"
            "::test_dv_pack_forbids_the_assistant_entirely`"
        )
        == []
    )
