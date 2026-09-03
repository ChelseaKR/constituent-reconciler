"""The destruction inventory must account for every artifact the code writes.

``destruction.PII_ARTIFACTS`` is a hand-maintained list, and a hand-maintained
list of what to delete drifts the moment a new command writes a new file. It
already had: ``constituent-reconcile ai-propose-corrections`` shipped writing
``ai_ocr_proposals.json`` (raw field values, proposed values, and a verbatim
quote out of the intake document) and ``constituent-reconcile run`` shipped writing
``household_suggestions.csv`` (a standardized street address and a surname per
candidate household), and neither name reached the list, so ``constituent-reconcile
destroy`` exited 0 and issued destruction certificates while leaving both files
on disk.

The older tests could not have caught that. Every one of them plants its
sentinel in a file whose name it takes *from* ``PII_ARTIFACTS``, so they prove
that what is on the list gets destroyed and can say nothing at all about what
is missing from it.

This module closes that by deriving the question from the source. It parses
every module in the package for the filenames the package joins onto a
directory, and requires each discovered name to appear on exactly one of
``PII_ARTIFACTS`` or ``NOT_DESTROYED``. Adding a writer without classifying its
artifact fails here, in the merge-blocking gate, rather than years later in
somebody's out directory.

What this file proves and what it does not: it proves no artifact is
*unclassified*. It cannot prove a classification is *correct*, because whether
a file carries personal data is a fact about its content, not its name. That
half is proved empirically in ``tests/test_destruction_leaves_nothing.py``,
which runs the real writers over sentinel-laced input and greps what survives
a destruction pass.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import constituent_reconciler
from constituent_reconciler.destruction import NOT_DESTROYED, PII_ARTIFACTS

PACKAGE_ROOT = Path(constituent_reconciler.__file__).resolve().parent

#: Data-file extensions an out-directory artifact can plausibly carry. Kept
#: wider than the artifacts that exist today so a new writer choosing, say,
#: ``.tsv`` is discovered rather than silently skipped.
_ARTIFACT_SUFFIXES = ("csv", "json", "jsonl", "tsv", "md", "txt")
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9_-]+\.(?:" + "|".join(_ARTIFACT_SUFFIXES) + r")$")

#: The vendored telemetry tree is bound by its own reviewed manifest and writes
#: nothing into a run's out directory, so it is not part of this inventory.
_EXCLUDED_PARTS = frozenset({"_vendor"})


def _literals_joined_onto_a_path(tree: ast.AST) -> list[tuple[str, int]]:
    """Every filename literal one module builds a path out of, with its line.

    Two forms cover how this package names an artifact, and both are how a
    filename actually reaches the filesystem here:

    * ``some_dir / "name.csv"``, the ``pathlib`` join idiom, read off the right
      operand of a division;
    * ``SOME_FILENAME = "name.csv"``, the module-level constant several modules
      define and then join in the first form elsewhere.

    A filename that reaches disk by neither form is not discovered. The stage
    cache is the one such case in the package today: its entry filenames are
    computed content digests, and ``destruction._cache_entries`` covers the
    whole cache tree by shape instead of by name.
    """

    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                found.append((right.value, node.lineno))
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            value = node.value.value
            if not isinstance(value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith("_FILENAME"):
                    found.append((value, node.lineno))
    return [(name, line) for name, line in found if _ARTIFACT_NAME.match(name)]


def discovered_artifacts() -> dict[str, list[str]]:
    """Every artifact filename in the package's own source, to where it appears."""

    discovered: dict[str, list[str]] = {}
    for module in sorted(PACKAGE_ROOT.rglob("*.py")):
        if _EXCLUDED_PARTS & set(module.parts):
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for name, line in _literals_joined_onto_a_path(tree):
            where = f"{module.relative_to(PACKAGE_ROOT).as_posix()}:{line}"
            discovered.setdefault(name, []).append(where)
    return discovered


def test_the_scan_finds_the_artifacts_we_already_know_about() -> None:
    """Guard the guard: a scan that silently found nothing would pass everything.

    Without this, a refactor that moved artifact naming to a form
    ``_literals_joined_onto_a_path`` does not recognize would turn the whole
    module into a no-op that still reported green.
    """

    discovered = discovered_artifacts()
    for known in ("resolved.csv", "review_queue.csv", "provenance.jsonl", "repair_plan.json"):
        assert known in discovered, f"the source scan no longer finds {known}"
    assert len(discovered) >= 20, f"the source scan found only {len(discovered)} artifact names"


def test_every_artifact_the_code_writes_is_classified() -> None:
    """No filename the package builds a path to may be left unclassified.

    This is the check that would have caught both live omissions. It reads the
    filenames out of the package's source, not out of a second list somebody
    would have to remember to update alongside the first.
    """

    discovered = discovered_artifacts()
    classified = set(PII_ARTIFACTS) | set(NOT_DESTROYED)
    unclassified = {name: where for name, where in discovered.items() if name not in classified}
    assert not unclassified, (
        "these artifact filenames appear in the package source but are on neither "
        "destruction.PII_ARTIFACTS nor destruction.NOT_DESTROYED, so `reconcile "
        "destroy` will leave them behind without saying so. Decide which list each "
        f"belongs on and record the reason: {unclassified}"
    )


def test_no_artifact_is_classified_both_ways() -> None:
    """A name on both lists means destruction and its own rationale disagree."""

    both = set(PII_ARTIFACTS) & set(NOT_DESTROYED)
    assert not both, f"classified as both destroyed and retained: {sorted(both)}"


def test_the_classification_carries_no_stale_entries() -> None:
    """Every classified name must still be one the code actually names.

    A leftover entry is not dangerous the way an omission is, but it is a
    claim about this codebase that has stopped being true, and the honest
    move is to notice and drop it.
    """

    discovered = set(discovered_artifacts())
    stale = (set(PII_ARTIFACTS) | set(NOT_DESTROYED)) - discovered
    assert not stale, (
        "these names are classified in destruction.py but no longer appear in the "
        f"package source; drop them or restore their writer: {sorted(stale)}"
    )


def test_every_retained_artifact_states_why_it_is_retained() -> None:
    """``NOT_DESTROYED`` is a record of reasoning, not a set of exemptions."""

    for name, reason in NOT_DESTROYED.items():
        assert reason.strip(), f"{name} is excluded from destruction with no reason given"
        assert len(reason.split()) >= 5, (
            f"{name}'s exclusion reason is too thin to review: {reason}"
        )


def test_the_two_newly_found_artifacts_are_on_the_destroyed_list() -> None:
    """Both omissions this inventory was written for, pinned by name.

    ``ai_ocr_proposals.json`` was reported from outside the project (#121) and
    ``household_suggestions.csv`` came out of auditing every writer for the
    same class of miss. Naming them here keeps a later edit from quietly
    reversing either one.
    """

    assert "ai_ocr_proposals.json" in PII_ARTIFACTS
    assert "household_suggestions.csv" in PII_ARTIFACTS
