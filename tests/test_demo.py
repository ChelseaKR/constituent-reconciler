"""``reconcile demo`` and the packaged copy of ``examples/`` it writes.

Two things are pinned here. First, the package-data tree under
``constituent_reconciler/examples`` is byte-identical to the committed
``examples/`` at the repository root, in both directions, so the copy the
wheel carries cannot drift from the one the README, the Makefile and the
Dockerfile use. Second, the command that writes it is fail-closed: a demo an
operator edited is never reverted, and nothing is written until every file
has been checked.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from constituent_reconciler import demo
from constituent_reconciler.cli import main
from constituent_reconciler.demo import DemoError, packaged_files, write_demo

REPO_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not any(
            part.startswith(".") or part == "__pycache__" for part in path.relative_to(root).parts
        )
    }


def test_the_packaged_demos_are_the_committed_examples_byte_for_byte() -> None:
    packaged = packaged_files()
    committed = _tree(REPO_EXAMPLES)

    assert sorted(packaged) == sorted(committed)
    for relative, data in committed.items():
        assert packaged[relative] == data, relative
    assert "intake-demo/recipe.toml" in packaged


def test_demo_writes_the_tree_and_the_documented_next_command_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """From an empty directory, the README's relative paths work as written."""

    monkeypatch.chdir(tmp_path)

    assert main(["demo"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    expected = packaged_files()
    assert f"demo: {len(expected)} file(s) written to examples, 0 already present" in captured.out
    assert "reconcile run --config examples/intake-demo/recipe.toml --out out" in captured.out
    assert _tree(tmp_path / "examples") == expected
    assert main(["run", "--config", "examples/intake-demo/recipe.toml", "--out", "out"]) == 0
    assert (tmp_path / "out" / "resolved.csv").is_file()
    assert (tmp_path / "out" / "review_queue.csv").is_file()


def test_demo_leaves_an_identical_tree_alone_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "somewhere"
    assert main(["demo", "--dir", str(target)]) == 0
    capsys.readouterr()
    before = {path: path.stat().st_mtime_ns for path in target.rglob("*") if path.is_file()}

    assert main(["demo", "--dir", str(target)]) == 0

    captured = capsys.readouterr()
    assert (
        f"0 file(s) written to {target.as_posix()}, {len(before)} already present" in captured.out
    )
    assert {path: path.stat().st_mtime_ns for path in before} == before


def test_demo_refuses_a_differing_file_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "examples"
    edited = target / "intake-demo" / "recipe.toml"
    edited.parent.mkdir(parents=True)
    edited.write_text("[input]\nincoming = 'mine.csv'\n", encoding="utf-8")

    assert main(["demo", "--dir", str(target)]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "demo error: intake-demo/recipe.toml" in captured.err
    assert "differs from the packaged file" in captured.err
    assert "nothing was written" in captured.err
    assert "Traceback" not in captured.err
    assert list(_tree(target)) == ["intake-demo/recipe.toml"]
    assert edited.read_text(encoding="utf-8").startswith("[input]\nincoming = 'mine.csv'")


def test_demo_refuses_a_symbolic_link_without_following_it(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.csv"
    elsewhere.write_bytes(packaged_files()["intake-demo/existing.csv"])
    target = tmp_path / "examples"
    (target / "intake-demo").mkdir(parents=True)
    (target / "intake-demo" / "existing.csv").symlink_to(elsewhere)

    with pytest.raises(DemoError, match="symbolic link"):
        write_demo(target)

    assert list(_tree(target)) == ["intake-demo/existing.csv"]


def test_demo_refuses_a_path_it_cannot_compare(tmp_path: Path) -> None:
    target = tmp_path / "examples"
    (target / "intake-demo" / "incoming.csv").mkdir(parents=True)

    with pytest.raises(DemoError, match="could not be compared"):
        write_demo(target)

    assert _tree(target) == {}


def test_demo_reports_a_target_it_cannot_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")

    assert main(["demo", "--dir", str(blocker / "examples")]) == 2

    captured = capsys.readouterr()
    assert "demo error: could not write" in captured.err


def test_demo_fails_when_the_installation_has_no_packaged_demos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo, "EXAMPLES_ROOT", tmp_path / "absent")

    with pytest.raises(DemoError, match="missing from this installation"):
        write_demo(tmp_path / "examples")

    assert not (tmp_path / "examples").exists()


def test_packaged_files_skips_hidden_entries_and_caches(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "intake-demo" / "__pycache__").mkdir(parents=True)
    (root / "intake-demo" / "recipe.toml").write_bytes(b"x")
    (root / "intake-demo" / ".DS_Store").write_bytes(b"junk")
    (root / "intake-demo" / "__pycache__" / "stale.pyc").write_bytes(b"junk")

    assert packaged_files(root) == {"intake-demo/recipe.toml": b"x"}
    assert os.path.isdir(root)
