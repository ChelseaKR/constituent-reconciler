"""The README's Quickstart, run from an installed wheel outside the repository.

Every other test here runs against the checkout: ``make install`` puts the
package in editable mode, so a file that exists in the tree is found whether
or not the wheel carries it. That is the one path no other test can see, and
it is where ``examples/`` was missing until 2026-09-02: the sdist and the
Docker image copied it, the wheel did not, and an operator who installed a
release got ``constituent-reconcile --help`` and then a ``FileNotFoundError`` traceback
from the Quickstart's first real command.

So this test builds the wheel, installs it into a fresh virtual environment,
checks that the environment imports the wheel's package rather than this
checkout, and runs the Quickstart block parsed from ``README.md`` itself from
a directory that is not the repository. The ``review`` line is interactive (it
serves the browser queue and waits), so it is asserted present and not run;
``tests/test_review.py`` covers it in process.

The harness fails rather than skips when it cannot do its job: no ``uv`` on
PATH, a build or install that fails, a README with no Quickstart to run. Each
of those messages says the wheel path was NOT examined, so it reads differently
from the Quickstart itself failing. A skipped test here would be a green mark
over the one path nobody looked at.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from constituent_reconciler.cli import PROG, main

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
EXAMPLES = ROOT / "examples"

NOT_EXAMINED = "the installed-wheel path was NOT examined"
HARNESS_TIMEOUT_SECONDS = 900

#: Quickstart lines this test runs from the wheel. ``review`` is interactive.
RUNNABLE = ("demo", "run")


def _uv() -> str:
    found = shutil.which("uv")
    if found is None:
        pytest.fail(f"uv is not on PATH; {NOT_EXAMINED}")
    return found


def _clean_env() -> dict[str, str]:
    """The environment for the build and the fresh venv: none of this checkout's.

    ``uv run`` and an activated ``.venv`` both export the project environment
    to children; left in place, the nested ``uv`` calls would resolve against
    this checkout and the Quickstart would run the editable install this test
    exists to avoid.
    """

    dropped = {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONHOME"}
    return {key: value for key, value in os.environ.items() if key not in dropped}


def _harness(argv: list[str], cwd: Path | None = None) -> str:
    """Run one harness step; a non-zero exit is the not-examined state."""

    result = subprocess.run(  # noqa: S603 -- argv is built here from known paths
        argv,
        cwd=cwd,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"`{' '.join(argv[:2])} ...` exited {result.returncode}; {NOT_EXAMINED}\n"
            f"{result.stderr}"
        )
    return result.stdout


def quickstart_commands(readme_text: str) -> list[list[str]]:
    """The console script's invocations in the README's Quickstart, as argv tails.

    Reads the first ``sh`` block under ``## Quickstart``, drops ``#`` comments
    and the ``make`` line, and requires every remaining line to start with
    :data:`~constituent_reconciler.cli.PROG` -- read from the code, so the
    rename that made ``reconcile`` a deprecated alias cannot leave this test
    asserting a stale name. A line this test does not understand is a
    failure, not a silent omission.
    """

    heading = readme_text.find("\n## Quickstart\n")
    if heading < 0:
        pytest.fail(f"README.md has no `## Quickstart` section; {NOT_EXAMINED}")
    fence = "```sh\n"
    start = readme_text.find(fence, heading)
    end = readme_text.find("```", start + len(fence)) if start >= 0 else -1
    if start < 0 or end < 0:
        pytest.fail(f"README.md Quickstart has no closed ```sh block; {NOT_EXAMINED}")
    commands: list[list[str]] = []
    for line in readme_text[start + len(fence) : end].splitlines():
        tokens = shlex.split(line, comments=True)
        if not tokens or tokens[0] == "make":
            continue
        if tokens[0] != PROG:
            pytest.fail(f"Quickstart line is not a `{PROG}` command: {line!r}")
        commands.append(tokens[1:])
    if not commands:
        pytest.fail(f"README.md Quickstart names no `{PROG}` command; {NOT_EXAMINED}")
    return commands


def test_quickstart_parser_drops_comments_and_the_make_line() -> None:
    text = (
        "# Title\n\n## Quickstart\n\n```sh\n"
        "make install    # uv sync\n"
        f"{PROG} demo  # the bundled examples/\n"
        f"{PROG} review --config examples/intake-demo/recipe.toml "
        '--reviewer "your name" --out out\n'
        "```\n"
    )

    assert quickstart_commands(text) == [
        ["demo"],
        [
            "review",
            "--config",
            "examples/intake-demo/recipe.toml",
            "--reviewer",
            "your name",
            "--out",
            "out",
        ],
    ]


def test_the_readme_quickstart_still_names_the_commands_this_test_runs() -> None:
    commands = quickstart_commands(README.read_text(encoding="utf-8"))

    assert [argv[0] for argv in commands] == ["demo", "run", "review"]


def _operand(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_the_documented_quickstart_runs_from_an_installed_wheel(tmp_path: Path) -> None:
    uv = _uv()
    dist = tmp_path / "dist"
    venv = tmp_path / "venv"
    outside = tmp_path / "outside"
    outside.mkdir()

    _harness(
        [uv, "build", "--wheel", "--out-dir", str(dist), "--python", sys.executable, str(ROOT)]
    )
    wheels = sorted(dist.glob("constituent_reconciler-*.whl"))
    if len(wheels) != 1:
        pytest.fail(f"expected one wheel, found {len(wheels)}; {NOT_EXAMINED}")
    _harness([uv, "venv", "--python", sys.executable, str(venv)])
    bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    _harness([uv, "pip", "install", "--python", str(python), str(wheels[0])])

    # The denominator: the interpreter about to run the Quickstart imports the
    # wheel's package, not this checkout's.
    located = Path(
        _harness(
            [str(python), "-c", "import constituent_reconciler as p; print(p.__file__)"],
            cwd=outside,
        ).strip()
    ).resolve()
    assert located.is_relative_to(venv.resolve()), (
        f"the fresh venv imported {located}, not the installed wheel; {NOT_EXAMINED}"
    )

    reconcile = bin_dir / (f"{PROG}.exe" if os.name == "nt" else PROG)
    commands = quickstart_commands(README.read_text(encoding="utf-8"))
    ran: list[str] = []
    for argv in commands:
        if argv[0] not in RUNNABLE:
            continue
        result = subprocess.run(  # noqa: S603 -- argv comes from the README block
            [str(reconcile), *argv],
            cwd=outside,
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=HARNESS_TIMEOUT_SECONDS,
            check=False,
        )
        assert result.returncode == 0, (
            f"`{PROG} {' '.join(argv)}` exited {result.returncode} from an installed "
            f"wheel, outside the repository:\n{result.stderr}"
        )
        ran.append(argv[0])
    assert ran == list(RUNNABLE)

    # What the wheel wrote is the committed examples/ tree, byte for byte.
    for path in sorted(EXAMPLES.rglob("*")):
        relative = path.relative_to(EXAMPLES)
        if path.is_file() and not any(part.startswith(".") for part in relative.parts):
            assert (outside / "examples" / relative).read_bytes() == path.read_bytes(), relative

    # And the wheel's run agrees with one made in process from this checkout.
    run_argv = next(argv for argv in commands if argv[0] == "run")
    produced = outside / _operand(run_argv, "--out")
    expected = tmp_path / "expected"
    assert (
        main(
            [
                "run",
                "--config",
                str(EXAMPLES / "intake-demo" / "recipe.toml"),
                "--out",
                str(expected),
            ]
        )
        == 0
    )
    for name in ("resolved.csv", "review_queue.csv"):
        assert (produced / name).read_bytes() == (expected / name).read_bytes(), name
