"""The console script's name, and the deprecated alias that shares its entry point.

``[project.scripts]`` declares two names for :func:`constituent_reconciler.cli.main`:
``constituent-reconcile`` and, until 0.10.0, ``reconcile``. The old name collides
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
import tomllib
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

#: The manifest, read for the version the *release* will carry. `__version__`
#: is installed metadata, which is a fact about the environment: a stale
#: editable install in a sibling checkout reports its own number and makes the
#: comparison below pass over a tree that fails it. `pyproject.toml` beside
#: this checkout is the tree's own declaration, and
#: `test_every_restatement_of_the_version_agrees_with_the_manifest` already
#: pins the two equal.
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _release(version: str) -> tuple[int, ...]:
    """``X.Y.Z`` as comparable integers, stopping at the first non-numeric part.

    Both values compared below are this project's own and are plain releases,
    so a full PEP 440 parse is not needed and ``packaging`` is a transitive
    dependency rather than a declared one. A pre-release or local suffix is
    dropped, which compares ``0.10.0rc1`` equal to ``0.10.0`` -- conservative
    in the direction that matters, since the check below refuses equality.
    """
    parts: list[int] = []
    for chunk in version.split("."):
        digits = ""
        for character in chunk:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            break
        parts.append(int(digits))
    assert parts, f"no numeric component in {version!r}"
    return tuple(parts)


def test_the_alias_promises_a_removal_that_has_not_already_happened() -> None:
    """A removal version that is not in the future is a promise already broken.

    The notice reads "will be removed in {DEPRECATED_PROG_REMOVED_IN}". Shipped
    with that value equal to the running version, it tells an operator that the
    command they are being warned about disappears in the release they are
    already running -- and it plainly has not, because they just used it. Set
    below the running version it is worse: a release that says it removed
    something it still installs.

    0.9.0 shipped in exactly that state. The alias, the notice and the
    changelog line naming 0.9.0 as the removal all went out inside 0.9.0.
    Nothing caught it because every check here asserts the value is *present*
    in the notice, and none asserts what the value has to be.

    The comparison is between two values neither of which is derived from the
    other -- the constant, and the version the distribution declares -- so
    moving either one alone can fail this. It opens by asserting the alias is
    actually installed, because an inequality between two constants passes just
    as happily when there is no alias left for either of them to describe:
    that is the day the notice, the constant and this check come out together.
    """
    assert DEPRECATED_PROG in _console_scripts(), (
        f"{DEPRECATED_PROG!r} is no longer an installed console script, so the "
        f"deprecation notice and DEPRECATED_PROG_REMOVED_IN have nothing left to "
        f"describe and belong in the changelog rather than in the code"
    )
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert _release(DEPRECATED_PROG_REMOVED_IN) > _release(declared), (
        f"the {DEPRECATED_PROG!r} alias is installed and its notice says it will be "
        f"removed in {DEPRECATED_PROG_REMOVED_IN}, and pyproject.toml declares "
        f"{declared}. A version that has arrived is not a removal that is coming: "
        f"either drop the alias from [project.scripts] and retire the notice, or name "
        f"a release that has not shipped."
    )


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
