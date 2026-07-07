"""Tests for strict recipe loading and the ``reconcile validate`` command.

The recipe is the one input a non-technical operator edits by hand, so it must
fail closed the way every other surface does: an unknown section or key raises
with the nearest valid name instead of silently running at a default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from constituent_reconciler.cli import main
from constituent_reconciler.config import load_recipe

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

MINIMAL = (
    '[input]\nincoming = "incoming.csv"\n\n[mapping]\nfirst_name = "first"\nlast_name = "last"\n'
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "recipe.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_every_example_recipe_still_loads() -> None:
    # Strictness must not reject any recipe the project ships.
    recipes = sorted(EXAMPLES.glob("*/*.toml"))
    assert recipes
    for recipe_path in recipes:
        load_recipe(recipe_path)


def test_misspelled_threshold_key_is_refused_with_the_nearest_name(tmp_path: Path) -> None:
    # The FIX-04 motivating case: auto_threshold would silently run at 0.97.
    path = _write(tmp_path, MINIMAL + "\n[thresholds]\nauto_threshold = 0.99\n")
    with pytest.raises(ValueError, match=r"auto_threshold.*did you mean 'auto'"):
        load_recipe(path)


def test_misspelled_section_is_refused_with_the_nearest_name(tmp_path: Path) -> None:
    # A misspelled [consnet] section must not silently disable consent mapping.
    path = _write(tmp_path, MINIMAL + '\n[consnet]\ncolumn = "Consent"\n')
    with pytest.raises(ValueError, match=r"consnet.*did you mean 'consent'"):
        load_recipe(path)


def test_unknown_mapping_field_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, MINIMAL + 'middle_name = "middle"\n')
    with pytest.raises(ValueError, match="middle_name"):
        load_recipe(path)


def test_bare_top_level_key_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, "incoming = 'x.csv'\n" + MINIMAL)
    with pytest.raises(ValueError, match="unknown section"):
        load_recipe(path)


def test_validate_command_accepts_the_demo_recipe(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["validate", "--config", str(EXAMPLES / "intake-demo" / "recipe.toml")])
    out = capsys.readouterr().out
    assert code == 0
    assert "recipe is valid" in out
    # The active policy switches are reported without running anything.
    assert "policy pack:      default" in out
    assert "mapped fields:" in out


def test_validate_command_rejects_a_bad_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, MINIMAL + "\n[thresholds]\nauto_threshold = 0.99\n")
    code = main(["validate", "--config", str(path)])
    err = capsys.readouterr().err
    assert code == 2
    assert "auto_threshold" in err


def test_validate_command_reports_a_missing_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, MINIMAL)  # incoming.csv is never created
    code = main(["validate", "--config", str(path)])
    err = capsys.readouterr().err
    assert code == 1
    assert "does not exist" in err
