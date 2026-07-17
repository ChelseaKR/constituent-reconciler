"""Tests for the source-hygiene gate (tools/hygiene.py).

Violating fixture strings are assembled by concatenation so this file does
not itself trip the gate, which scans tests/ along with src/ and tools/.
"""

from __future__ import annotations

from pathlib import Path

from tools.hygiene import scan_file, scan_line, scan_tree

# Assembled tokens: writing them literally would flag this very file.
_TODO = "TO" + "DO"
_HASH_NOQA = "# no" + "qa"
_HASH_TYPE_IGNORE = "# type" + ": ignore"
_HASH_PRAGMA = "# pragma" + ": no cover"
_HASH_NOSEMGREP = "# nosem" + "grep"


def test_clean_lines_produce_no_complaints() -> None:
    assert scan_line("x = 1") == []
    assert scan_line("# a perfectly ordinary comment") == []
    # Prose mention without an adjacent hash mark is not a directive.
    assert scan_line("# operator-configured url, see noqa above") == []


def test_coded_and_explained_suppressions_pass() -> None:
    assert scan_line(f"x = call()  {_HASH_NOQA}: S310 - reviewed") == []
    assert scan_line(f"y = z  {_HASH_TYPE_IGNORE}[attr-defined]") == []
    assert scan_line(f"return  {_HASH_PRAGMA} - Windows") == []
    assert scan_line(f"{_HASH_NOSEMGREP}: dynamic-urllib-use-detected (reviewed)") == []


def test_debt_marker_is_flagged() -> None:
    complaints = scan_line(f"# {_TODO}: revisit later")
    assert len(complaints) == 1
    assert "debt marker" in complaints[0]


def test_bare_noqa_is_flagged() -> None:
    assert any("bare noqa" in c for c in scan_line(f"x = call()  {_HASH_NOQA}"))


def test_bare_type_ignore_is_flagged() -> None:
    assert any("type: ignore" in c for c in scan_line(f"y = z  {_HASH_TYPE_IGNORE}"))


def test_unexplained_pragma_is_flagged() -> None:
    assert any("coverage exclusion" in c for c in scan_line(f"return  {_HASH_PRAGMA}"))


def test_bare_nosemgrep_is_flagged() -> None:
    assert any("nosemgrep" in c for c in scan_line(f"x = fetch(url)  {_HASH_NOSEMGREP}"))


def test_scan_file_reports_path_and_line(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(f"x = 1\n# {_TODO}: later\n", encoding="utf-8")
    complaints = scan_file(bad)
    assert len(complaints) == 1
    assert complaints[0].startswith(f"{bad}:2:")


def test_scan_tree_skips_the_vendored_tree(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "_vendor").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "src" / "pkg" / "flagged.py").write_text(f"# {_TODO}: x\n", encoding="utf-8")
    (tmp_path / "src" / "_vendor" / "excluded.py").write_text(f"# {_TODO}: x\n", encoding="utf-8")
    complaints = scan_tree(tmp_path)
    assert len(complaints) == 1
    assert "flagged.py" in complaints[0]
    assert not any("excluded.py" in c for c in complaints)


def test_the_repository_is_clean() -> None:
    # The gate's promise: the tree this test runs in has no findings. This is
    # the same scan `make hygiene` (and so `make verify` and CI) runs.
    root = Path(__file__).resolve().parents[1]
    assert scan_tree(root) == []
