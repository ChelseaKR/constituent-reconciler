"""`constituent-reconcile init`, and the guesses it must refuse to make.

The scaffold's value is not that it fills a file in. It is that a person can
trust the file it filled in, which means every test below is really about one of
three refusals: it never reads past the header, it never maps on anything but an
exact documented alias, and it never chooses a policy pack.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from constituent_reconciler import scaffold
from constituent_reconciler.cli import main
from constituent_reconciler.config import load_recipe
from constituent_reconciler.models import CANONICAL_FIELDS
from constituent_reconciler.policy import PolicyViolation

DEMO_HEADERS = ["id", "First Name", "Last Name", "DOB", "Email", "Phone", "Consent"]


def _csv(path: Path, headers: list[str], rows: list[list[str]] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows or [])
    return path


def _build(tmp_path: Path, headers: list[str], **kwargs: object) -> scaffold.Scaffold:
    incoming = _csv(tmp_path / "incoming.csv", headers)
    return scaffold.build(incoming=incoming, recipe_dir=tmp_path, **kwargs)  # type: ignore[arg-type]


# --- it maps only what the operator's own headers say -------------------------------


def test_documented_aliases_map_and_nothing_else_does(tmp_path: Path) -> None:
    result = _build(tmp_path, DEMO_HEADERS)
    assert result.mapping == {
        "first_name": "First Name",
        "last_name": "Last Name",
        "dob": "DOB",
        "email": "Email",
        "phone": "Phone",
    }
    # The demo export has no address column, so address is a stub, not a guess.
    assert set(result.stubs) == {"address"}


def test_a_header_with_no_alias_is_a_stub_not_a_guess(tmp_path: Path) -> None:
    """The acceptance criterion, and the whole reason the alias table is closed."""
    result = _build(tmp_path, ["Client Given", "Client Family", "Contact Point", "Born"])
    assert result.mapping == {}
    assert set(result.stubs) == set(CANONICAL_FIELDS)
    assert all("no column header matched" in reason for reason in result.stubs.values())
    # And every one of them is reported rather than dropped.
    assert set(result.unmapped_columns) == {
        "Client Given",
        "Client Family",
        "Contact Point",
        "Born",
    }


def test_case_and_whitespace_differences_still_match(tmp_path: Path) -> None:
    result = _build(tmp_path, ["FIRST  NAME", "lastname", "E-Mail"])
    assert result.mapping == {
        "first_name": "FIRST  NAME",
        "last_name": "lastname",
        "email": "E-Mail",
    }


def test_punctuation_is_significant(tmp_path: Path) -> None:
    """`first_name` is not `first name`: normalization collapses whitespace only."""
    result = _build(tmp_path, ["first-name"])
    assert result.mapping == {}
    assert "first-name" in result.unmapped_columns


def test_two_columns_claiming_one_field_leaves_a_stub_naming_both(tmp_path: Path) -> None:
    """A tie is an ambiguity a person resolves, not one this module breaks."""
    result = _build(tmp_path, ["First Name", "fname", "Last Name"])
    assert "first_name" not in result.mapping
    assert "'First Name'" in result.stubs["first_name"]
    assert "'fname'" in result.stubs["first_name"]
    assert result.mapping["last_name"] == "Last Name"
    # Neither candidate is quietly dropped from the report either.
    assert "First Name" in result.unmapped_columns
    assert "fname" in result.unmapped_columns


def test_no_data_row_is_ever_read(tmp_path: Path) -> None:
    """The refusal that matters most: values never influence the mapping.

    Two files with identical headers and wildly different data must scaffold
    byte-identically. A generator that sniffed values would fail this.
    """
    dates = _csv(tmp_path / "a" / "in.csv", ["Notes"], [["1988-03-09"], ["1990-01-02"]])
    words = _csv(tmp_path / "b" / "in.csv", ["Notes"], [["hello"], ["world"]])
    first = scaffold.build(incoming=dates, recipe_dir=tmp_path / "a")
    second = scaffold.build(incoming=words, recipe_dir=tmp_path / "b")
    assert first.text == second.text
    assert first.mapping == {} and "Notes" in first.unmapped_columns


def test_an_excel_byte_order_mark_does_not_break_the_first_column(tmp_path: Path) -> None:
    path = tmp_path / "incoming.csv"
    path.write_text("﻿First Name,Last Name\n", encoding="utf-8")
    result = scaffold.build(incoming=path, recipe_dir=tmp_path)
    assert result.mapping["first_name"] == "First Name"


# --- what it says about what it looked at -------------------------------------------


def test_a_directory_contributes_every_csv_and_names_what_it_skipped(tmp_path: Path) -> None:
    folder = tmp_path / "intake"
    _csv(folder / "monday.csv", ["First Name", "Last Name"])
    _csv(folder / "tuesday.csv", ["First Name", "Email"])
    (folder / "notes.xlsx").write_bytes(b"not inspected")
    (folder / "readme.txt").write_text("not inspected", encoding="utf-8")

    inspection = scaffold.read_headers(folder)
    assert [f.name for f in inspection.files] == ["monday.csv", "tuesday.csv"]
    assert inspection.headers == ("First Name", "Last Name", "Email")
    assert inspection.skipped_suffixes == (".txt", ".xlsx")

    result = scaffold.build(incoming=folder, recipe_dir=tmp_path)
    assert "Also present and NOT inspected: .txt, .xlsx files." in result.text


def test_a_directory_with_no_csv_says_so_rather_than_scaffolding_from_nothing(
    tmp_path: Path,
) -> None:
    """An empty inspection is reported. It is not a source with no columns."""
    folder = tmp_path / "intake"
    folder.mkdir()
    (folder / "notes.xlsx").write_bytes(b"not a csv")
    result = scaffold.build(incoming=folder, recipe_dir=tmp_path)
    assert "no .csv file found here" in result.text
    assert result.mapping == {}


def test_a_missing_input_path_is_an_error_not_an_empty_scaffold(tmp_path: Path) -> None:
    with pytest.raises(scaffold.ScaffoldError, match="does not exist"):
        scaffold.build(incoming=tmp_path / "nope.csv", recipe_dir=tmp_path)


def test_a_non_utf8_export_is_refused_with_the_fix_named(tmp_path: Path) -> None:
    path = tmp_path / "incoming.csv"
    path.write_bytes("First Name,Renée\n".encode("latin-1"))
    with pytest.raises(scaffold.ScaffoldError, match="export it as UTF-8 CSV first"):
        scaffold.build(incoming=path, recipe_dir=tmp_path)


def test_paths_are_written_relative_to_the_recipe_when_they_can_be(tmp_path: Path) -> None:
    existing = _csv(tmp_path / "clients.csv", DEMO_HEADERS)
    incoming = _csv(tmp_path / "batch" / "new.csv", DEMO_HEADERS)
    result = scaffold.build(incoming=incoming, existing=existing, recipe_dir=tmp_path)
    assert 'existing = "clients.csv"' in result.text
    assert 'incoming = "batch/new.csv"' in result.text


def test_output_is_deterministic(tmp_path: Path) -> None:
    incoming = _csv(tmp_path / "incoming.csv", DEMO_HEADERS)
    first = scaffold.build(incoming=incoming, recipe_dir=tmp_path).text
    second = scaffold.build(incoming=incoming, recipe_dir=tmp_path).text
    assert first == second


# --- the file it writes does not run ------------------------------------------------


def test_the_scaffold_refuses_to_load_until_a_policy_pack_is_chosen(tmp_path: Path) -> None:
    """Not a validation nicety: the pack decides whether PII may leave the machine."""
    incoming = _csv(tmp_path / "incoming.csv", DEMO_HEADERS)
    result = scaffold.build(incoming=incoming, recipe_dir=tmp_path)
    out = tmp_path / "recipe.toml"
    scaffold.write(result, out)
    assert 'pack = ""' in out.read_text(encoding="utf-8")
    with pytest.raises(PolicyViolation):
        load_recipe(out)


def test_choosing_the_pack_is_all_the_demo_export_needs(tmp_path: Path) -> None:
    """The acceptance criterion: fill the documented lines and the recipe loads."""
    _csv(tmp_path / "existing.csv", DEMO_HEADERS)
    _csv(tmp_path / "incoming.csv", DEMO_HEADERS)
    result = scaffold.build(
        incoming=tmp_path / "incoming.csv",
        existing=tmp_path / "existing.csv",
        recipe_dir=tmp_path,
    )
    out = tmp_path / "recipe.toml"
    scaffold.write(result, out)
    out.write_text(
        out.read_text(encoding="utf-8").replace('pack = ""', 'pack = "default"'),
        encoding="utf-8",
    )
    recipe = load_recipe(out)
    assert recipe.fields == ("first_name", "last_name", "dob", "email", "phone")
    assert recipe.policy_pack == "default"
    assert recipe.prior == 0.01
    assert recipe.auto_threshold == 0.97
    assert recipe.review_threshold == 0.80


def test_init_refuses_to_overwrite_an_existing_recipe(tmp_path: Path) -> None:
    incoming = _csv(tmp_path / "incoming.csv", DEMO_HEADERS)
    result = scaffold.build(incoming=incoming, recipe_dir=tmp_path)
    out = tmp_path / "recipe.toml"
    out.write_text("# hand-written; do not clobber\n", encoding="utf-8")
    with pytest.raises(scaffold.ScaffoldError, match="will not overwrite"):
        scaffold.write(result, out)
    assert out.read_text(encoding="utf-8") == "# hand-written; do not clobber\n"


# --- the outstanding-decision hint --------------------------------------------------


def test_unfilled_stubs_lists_the_todos_and_forgets_the_pack_once_chosen(
    tmp_path: Path,
) -> None:
    incoming = _csv(tmp_path / "incoming.csv", DEMO_HEADERS)
    text = scaffold.build(incoming=incoming, recipe_dir=tmp_path).text
    outstanding = scaffold.unfilled_stubs(text)
    assert any(item.startswith("pack --") for item in outstanding)
    assert any(item.startswith("address --") for item in outstanding)

    chosen = text.replace('pack = ""', 'pack = "dv"')
    assert not any(item.startswith("pack --") for item in scaffold.unfilled_stubs(chosen))


def test_the_hint_is_only_offered_about_files_init_actually_wrote() -> None:
    """A hand-written recipe with a `# CHOOSE:` in it is none of this code's business."""
    assert scaffold.unfilled_stubs('# CHOOSE: pack -- mine, not yours\npack = ""\n') == []


# --- the CLI ------------------------------------------------------------------------


def test_init_writes_reports_and_then_validate_explains_what_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _csv(tmp_path / "existing.csv", DEMO_HEADERS)
    _csv(tmp_path / "incoming.csv", DEMO_HEADERS)
    out = tmp_path / "recipe.toml"

    assert (
        main(
            [
                "init",
                "--existing",
                str(tmp_path / "existing.csv"),
                "--incoming",
                str(tmp_path / "incoming.csv"),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    written = capsys.readouterr().out
    assert "wrote scaffold:" in written
    assert "first_name <- 'First Name'" in written
    assert "left for you to choose: address" in written
    assert "does not run yet" in written

    # As written it fails, and says which decisions are outstanding.
    assert main(["validate", "--config", str(out)]) == 2
    captured = capsys.readouterr()
    assert "unknown policy pack ''" in captured.err
    assert "pack --" in captured.err
    assert "address --" in captured.err

    # With the pack chosen it passes, and the unmapped field is a note, not a failure:
    # a canonical field the data has no column for is a normal end state.
    out.write_text(
        out.read_text(encoding="utf-8").replace('pack = ""', 'pack = "default"'),
        encoding="utf-8",
    )
    assert main(["validate", "--config", str(out)]) == 0
    captured = capsys.readouterr()
    assert "recipe is valid." in captured.out
    assert "address --" in captured.out


def test_init_exits_two_on_a_missing_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["init", "--incoming", str(tmp_path / "nope.csv"), "--out", str(tmp_path / "r.toml")]
    )
    assert code == 2
    assert "does not exist" in capsys.readouterr().err
    assert not (tmp_path / "r.toml").exists()


def test_init_exits_two_rather_than_overwriting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _csv(tmp_path / "incoming.csv", DEMO_HEADERS)
    out = tmp_path / "recipe.toml"
    out.write_text("# mine\n", encoding="utf-8")
    code = main(["init", "--incoming", str(tmp_path / "incoming.csv"), "--out", str(out)])
    assert code == 2
    assert "will not overwrite" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "# mine\n"


# --- the alias table is documented, not just implemented ----------------------------


def test_every_alias_is_published_in_the_adoption_kit() -> None:
    """An undocumented alias is a hidden rule about someone's data."""
    kit = (Path(__file__).resolve().parents[1] / "docs" / "ADOPTION-KIT.md").read_text(
        encoding="utf-8"
    )
    for field, aliases in scaffold.ALIASES.items():
        for alias in aliases:
            assert f"`{alias}`" in kit, f"{field} alias {alias!r} is not in docs/ADOPTION-KIT.md"
    for alias in scaffold.CONSENT_ALIASES + scaffold.ID_ALIASES:
        assert f"`{alias}`" in kit, f"alias {alias!r} is not in docs/ADOPTION-KIT.md"


def test_every_canonical_field_has_at_least_one_alias() -> None:
    assert set(scaffold.ALIASES) == set(CANONICAL_FIELDS)
    assert all(scaffold.ALIASES[field] for field in CANONICAL_FIELDS)


def test_no_alias_is_claimed_by_two_fields() -> None:
    """An alias in two tables would make the mapping depend on iteration order."""
    seen: dict[str, str] = {}
    for field, aliases in scaffold.ALIASES.items():
        for alias in aliases:
            assert alias not in seen, f"{alias!r} is claimed by both {seen.get(alias)} and {field}"
            seen[alias] = field
    for alias in scaffold.CONSENT_ALIASES + scaffold.ID_ALIASES:
        assert alias not in seen, f"{alias!r} is claimed by both {seen.get(alias)} and a non-field"


def test_every_alias_is_already_in_comparison_form() -> None:
    """An alias with capitals or double spaces could never match anything."""
    all_aliases = [a for aliases in scaffold.ALIASES.values() for a in aliases]
    all_aliases += list(scaffold.CONSENT_ALIASES + scaffold.ID_ALIASES)
    for alias in all_aliases:
        assert scaffold.normalize_header(alias) == alias, f"{alias!r} is not in comparison form"
