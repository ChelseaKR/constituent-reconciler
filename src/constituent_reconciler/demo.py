"""The bundled demos as package data, and the ``constituent-reconcile demo`` command.

``examples/`` at the repository root is what every ``--config`` path in the
README points into. It ships in the sdist and in the Docker image, and until
2026-09-02 it did not ship in the wheel, so an operator who installed a release
(``uvx --from git+...@v0.8.0``, or a downloaded wheel) had ``constituent-reconcile --help``
and then a bare ``FileNotFoundError`` from the Quickstart's first real command.
The same tree is now package data under ``constituent_reconciler/examples``,
and ``constituent-reconcile demo`` writes it to disk so the documented paths work from an
installed wheel exactly as they do from a clone. ``tests/test_demo.py`` pins
the packaged tree and the committed one byte-identical in both directions, so
the copy cannot drift from the one the README, the Makefile and the Dockerfile
use.

Writing is fail-closed and byte-exact. Every packaged file is read before any
is written. A file already at a target path is left alone when it is
byte-identical and reported as already present; one that differs, a symbolic
link, or anything that cannot be compared stops the whole command before it
writes a byte, because a demo an operator has edited must not be reverted
without their say.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

EXAMPLES_ROOT = Path(__file__).parent / "examples"
"""Where the packaged demos live, in a checkout and in an installed wheel."""

DEFAULT_TARGET = Path("examples")
"""The relative path every documented ``--config`` uses."""

NEXT_STEP = "constituent-reconcile run --config {recipe} --out out"
"""What to run once the demo is on disk, printed with the real recipe path."""


class DemoError(Exception):
    """The demo could not be written, and nothing was."""


@dataclasses.dataclass(frozen=True, slots=True)
class DemoReport:
    """What ``write_demo`` did: the target and the files it wrote or found."""

    target: Path
    written: tuple[str, ...]
    present: tuple[str, ...]

    @property
    def recipe(self) -> Path:
        """The intake demo's recipe, the path the README's commands use."""

        return self.target / "intake-demo" / "recipe.toml"


def _is_hidden(relative: Path) -> bool:
    return any(part.startswith(".") or part == "__pycache__" for part in relative.parts)


def packaged_files(root: Path | None = None) -> dict[str, bytes]:
    """Every packaged demo file by its POSIX path under ``root``, in sorted order.

    ``root`` defaults to the package's own copy and is read at call time so a
    test can point it somewhere else. An installation with no packaged demos is
    an error here, not an empty demo written successfully.
    """

    base = EXAMPLES_ROOT if root is None else root
    files: dict[str, bytes] = {}
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(base)
            if path.is_file() and not _is_hidden(relative):
                files[relative.as_posix()] = path.read_bytes()
    if not files:
        raise DemoError(
            f"the packaged demos are missing from this installation ({base}); "
            "reinstall the package, or clone the repository and use its examples/"
        )
    return files


def _needs_writing(target: Path, relative: str, data: bytes) -> bool:
    """Decide what the demo would do at ``target`` without touching it."""

    if target.is_symlink():
        raise DemoError(
            f"{relative}: a symbolic link is already at {target}; nothing was written. "
            "Move it aside, or pass --dir to write the demo somewhere else"
        )
    if not target.exists():
        return True
    try:
        existing = target.read_bytes()
    except OSError as error:
        raise DemoError(
            f"{relative}: {target} could not be compared with the packaged file "
            f"({error.strerror}); nothing was written"
        ) from error
    if existing != data:
        raise DemoError(
            f"{relative}: {target} differs from the packaged file; nothing was written. "
            "Move it aside, or pass --dir to write the demo somewhere else"
        )
    return False


def write_demo(target: Path) -> DemoReport:
    """Write the packaged demos under ``target``.

    Raises ``DemoError`` having written nothing when any file cannot be placed.
    """

    files = packaged_files()
    pending = {
        relative: data
        for relative, data in files.items()
        if _needs_writing(target / relative, relative, data)
    }
    try:
        for relative, data in pending.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    except OSError as error:
        raise DemoError(f"could not write {target}: {error.strerror}") from error
    return DemoReport(
        target=target,
        written=tuple(pending),
        present=tuple(relative for relative in files if relative not in pending),
    )
