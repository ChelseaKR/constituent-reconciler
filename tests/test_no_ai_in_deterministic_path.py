"""Proves the offline-first deterministic pipeline never imports the AI
assistant package, and runs unchanged even when ``anthropic`` and ``boto3``
are unavailable.

Two complementary checks:

1. A static AST walk of ``pipeline.py``, ``decisions.py``, and every
   module-level import in ``cli.py`` (every ``constituent_reconciler.
   assistant`` import in ``cli.py`` lives inside a function body, not at
   module level -- see ``cli.py``'s own module docstring) confirms none of
   them names ``constituent_reconciler.assistant``, ``anthropic``, or
   ``boto3``.
2. A subprocess runs ``reconcile run`` against the bundled demo recipe with
   ``anthropic`` and ``boto3`` sabotaged out of ``sys.modules`` (the standard
   "assign None" trick, which makes any ``import anthropic`` raise
   ``ImportError``) and asserts the run still succeeds and produces the
   normal output -- real proof, not just an import-graph inference.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "constituent_reconciler"
EXAMPLES = REPO_ROOT / "examples" / "intake-demo"

_FORBIDDEN_MODULES = ("constituent_reconciler.assistant", "anthropic", "boto3")


def _module_level_import_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:  # module level only -- deliberately not ast.walk()
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_pipeline_module_never_imports_the_assistant_package_or_its_sdks() -> None:
    names = _module_level_import_names(SRC / "pipeline.py")
    for forbidden in _FORBIDDEN_MODULES:
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in names)


def test_decisions_module_never_imports_the_assistant_package_or_its_sdks() -> None:
    names = _module_level_import_names(SRC / "decisions.py")
    for forbidden in _FORBIDDEN_MODULES:
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in names)


def test_cli_module_level_imports_never_name_the_assistant_package_or_its_sdks() -> None:
    """Every AI import in cli.py must be inside a function body (a
    ``_cmd_ai_*`` command), never at module level -- this is what makes
    ``ai-explain``/``ai-ask``/``ai-propose-corrections`` opt-in rather than
    something every ``reconcile`` invocation pays for.
    """
    names = _module_level_import_names(SRC / "cli.py")
    for forbidden in _FORBIDDEN_MODULES:
        assert not any(name == forbidden or name.startswith(forbidden + ".") for name in names)


def test_run_succeeds_in_a_subprocess_with_anthropic_and_boto3_unavailable() -> None:
    script = textwrap.dedent(
        f"""
        import sys
        sys.modules["anthropic"] = None
        sys.modules["boto3"] = None
        sys.path.insert(0, {str(REPO_ROOT / "src")!r})

        from constituent_reconciler.cli import main

        code = main([
            "run",
            "--config", {str(EXAMPLES / "recipe.toml")!r},
            "--out", sys.argv[1],
        ])
        sys.exit(code)
        """
    )

    with tempfile.TemporaryDirectory() as out_dir:
        result = subprocess.run(  # noqa: S603 - argv is fixed; no shell, no untrusted input
            [sys.executable, "-c", script, out_dir],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (Path(out_dir) / "resolved.csv").exists()
        # Sanity: this genuinely exercised the sabotage, not a no-op --
        # importing anthropic directly in the same interpreter must fail.
        probe = subprocess.run(  # noqa: S603 - argv is fixed; no shell, no untrusted input
            [
                sys.executable,
                "-c",
                'import sys; sys.modules["anthropic"] = None; import anthropic',
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert probe.returncode != 0
        # CPython raises ModuleNotFoundError (a subclass of ImportError) for
        # the sys.modules-is-None sabotage; either name proves the sabotage
        # actually blocked the import.
        assert "ImportError" in probe.stderr or "ModuleNotFoundError" in probe.stderr
