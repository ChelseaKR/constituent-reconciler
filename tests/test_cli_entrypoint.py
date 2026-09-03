"""The console script's name, and the deprecated alias that shares its entry point.

``[project.scripts]`` declares two names for :func:`constituent_reconciler.cli.main`:
``constituent-reconcile`` and, until 0.9.0, ``reconcile``. The old name collides
with unrelated PyPI distributions that install a ``bin/reconcile`` of their own,
and two packages competing for one console-script name is last-install-wins
with no error, so it was retired before the first published release. The alias
has to keep working for one minor cycle and has to announce itself on stderr
only: a pipeline that captures stdout under the old name must see exactly what
it saw before.
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from constituent_reconciler import __version__
from constituent_reconciler.cli import (
    DEPRECATED_PROG,
    DEPRECATED_PROG_REMOVED_IN,
    PROG,
    deprecated_alias_notice,
    main,
)

ENTRY_FUNCTION = "constituent_reconciler.cli:main"


def _console_scripts() -> dict[str, str]:
    """This distribution's installed console scripts, name to ``module:attr``."""
    return {
        ep.name: ep.value
        for ep in entry_points(group="console_scripts")
        if ep.value.startswith("constituent_reconciler.")
    }


def test_both_console_scripts_resolve_to_the_same_entry_function() -> None:
    scripts = _console_scripts()
    assert set(scripts) == {PROG, DEPRECATED_PROG}
    assert scripts[PROG] == ENTRY_FUNCTION
    assert scripts[DEPRECATED_PROG] == ENTRY_FUNCTION
    loaded = {
        ep.name: ep.load() for ep in entry_points(group="console_scripts") if ep.name in scripts
    }
    assert loaded[PROG] is main
    assert loaded[DEPRECATED_PROG] is main


def test_the_alias_prints_one_stderr_line_and_nothing_extra_on_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", [f"/somewhere/bin/{DEPRECATED_PROG}", "--version"])
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == f"{PROG} {__version__}\n"
    err_lines = captured.err.splitlines()
    assert len(err_lines) == 1
    assert "deprecated" in err_lines[0]
    assert PROG in err_lines[0]
    assert DEPRECATED_PROG_REMOVED_IN in err_lines[0]


def test_the_new_name_prints_no_notice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", [f"/somewhere/bin/{PROG}", "--version"])
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == f"{PROG} {__version__}\n"
    assert captured.err == ""


def test_help_and_version_carry_the_new_name(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    assert capsys.readouterr().out.startswith(f"usage: {PROG} ")


@pytest.mark.parametrize(
    "argv0",
    [
        "reconcile",
        "/venv/bin/reconcile",
        "C:\\venv\\Scripts\\reconcile.exe",
        "reconcile.EXE",
    ],
)
def test_the_alias_is_detected_from_the_invoked_script_name(argv0: str) -> None:
    assert deprecated_alias_notice(argv0) is not None


@pytest.mark.parametrize(
    "argv0",
    [
        "constituent-reconcile",
        "/venv/bin/constituent-reconcile",
        "-c",
        "",
        "pytest",
        "/venv/bin/reconciler",
        "cli.py",
    ],
)
def test_other_invocations_are_not_the_alias(argv0: str) -> None:
    assert deprecated_alias_notice(argv0) is None


def _installed_script(name: str) -> Path:
    path = Path(sysconfig.get_path("scripts")) / name
    if sys.platform == "win32":
        path = path.with_suffix(".exe")
    assert path.exists(), f"{path} is not installed; run `make install`"
    return path


def test_the_installed_console_scripts_answer_version_from_a_subprocess() -> None:
    """The real installed scripts, not ``main`` in-process: this is the wiring an
    operator's shell resolves, so it is the wiring that has to be checked.
    """
    new = subprocess.run(  # noqa: S603 - argv is fixed; no shell, no untrusted input
        [str(_installed_script(PROG)), "--version"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert new.returncode == 0, new.stderr
    assert new.stdout == f"{PROG} {__version__}\n"
    assert new.stderr == ""

    old = subprocess.run(  # noqa: S603 - argv is fixed; no shell, no untrusted input
        [str(_installed_script(DEPRECATED_PROG)), "--version"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert old.returncode == 0, old.stderr
    assert old.stdout == new.stdout
    assert old.stderr.count("\n") == 1
    assert PROG in old.stderr
    assert DEPRECATED_PROG_REMOVED_IN in old.stderr
