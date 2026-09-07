"""Read an Excel workbook as a structured source: read-only, values only.

Most small nonprofits keep the spreadsheet side of intake in a workbook, not a
CSV, and exporting each sheet by hand before every run is where a column gets
dropped or renamed. This module reads ``.xlsx`` and ``.xlsm`` directly and
returns the same ``list[dict[str, str]]`` shape ``csv.DictReader`` yields, so
the pipeline's row-to-Record path is shared byte for byte between the two
readers and a workbook run resolves exactly as its CSV equivalent does.

Read-only by construction: the workbook is opened in openpyxl's streaming
read-only mode and never written. Values only, never formula text --
``data_only=True`` asks for the result Excel last cached, and a formula whose
result was never cached is refused by name rather than read as a blank cell,
because a missing read published as an empty value is this project's most
common defect class.

Every failure here is fail-closed and named: a missing sheet, a merged or blank
header, duplicate headers, an empty sheet, a header row past the end of the
data, an uncomputed formula, and a workbook that needs a password all raise
``WorkbookError`` instead of producing a partial run.
"""

from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from constituent_reconciler.config import RecipeError

WORKBOOK_SUFFIXES = frozenset({".xlsx", ".xlsm"})
"""The structured-source extensions this reader claims.

``.xlsb`` is absent on purpose: it is a binary format openpyxl cannot read, so
claiming it would turn an unsupported-extension skip, which the ingest report
names, into a crash.
"""

# The first eight bytes of an OLE2 compound file. A password-protected .xlsx is
# an OLE2 container wrapping the encrypted package rather than a zip, so this
# magic number is what separates "needs a password" from "corrupt file" -- two
# refusals an operator fixes in completely different ways.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# The OOXML parts below are parsed with the standard library's ElementTree.
# S314 flags that as untrusted-XML parsing, and the input genuinely is
# untrusted -- an intake workbook arrives from outside. Two things make it the
# right call here anyway, and neither is "the file is probably fine":
# ElementTree's expat parser refuses undefined entities outright rather than
# expanding them, which is what the entity-expansion attacks S314 exists for;
# and openpyxl has already parsed these same parts of this same file, through
# the same stdlib parser, before any of this code runs. Reaching for defusedxml
# here would harden the second reader of a file the first reader already
# opened, while adding a dependency to the offline install.
_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_RELS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class WorkbookError(RecipeError):
    """A workbook could not be read as the recipe describes it.

    Subclasses ``RecipeError`` so every refusal reaches the operator through
    the CLI's existing recipe-error handler as one named line and exit code 2,
    rather than as a traceback. Which sheet, which cell, and which header are
    always in the message: an operator fixes a workbook by opening it at the
    place named.
    """


def _fail(message: str) -> WorkbookError:
    return WorkbookError(message)


def _require_openpyxl() -> Any:
    """The openpyxl module, or a named refusal telling the operator what to install.

    openpyxl ships behind the ``excel`` extra, so importing it at module scope
    would make a spreadsheet parser a hard dependency of every installation,
    including the ones that only ever read CSVs. Routing only needs
    ``WORKBOOK_SUFFIXES``, which is why this module stays importable without it.
    """

    try:
        import openpyxl
    except ImportError as error:  # pragma: no cover - exercised by the install matrix
        raise WorkbookError(
            "reading .xlsx/.xlsm workbooks needs openpyxl, which ships behind an "
            'optional extra: install constituent-reconciler with the "excel" extra '
            "(pip install 'constituent-reconciler[excel]')."
        ) from error
    return openpyxl


def _guard_container(path: Path) -> None:
    """Refuse a workbook that is not a readable zip, naming why when we know.

    Read only the first eight bytes: enough to tell a password-protected
    workbook (an OLE2 container) from a truncated or corrupt one.
    """

    try:
        with path.open("rb") as handle:
            head = handle.read(len(_OLE2_MAGIC))
    except OSError as error:
        raise _fail(f"workbook could not be read: {path} ({error.strerror})") from error
    if head == _OLE2_MAGIC:
        raise _fail(
            f"workbook is password-protected and cannot be read: {path}. "
            "Save an unprotected copy, or export the sheet to CSV."
        )


def _load(path: Path, *, data_only: bool) -> Any:
    openpyxl = _require_openpyxl()
    _guard_container(path)
    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=data_only, keep_links=False)
    except zipfile.BadZipFile as error:
        raise _fail(f"workbook is not a readable .xlsx/.xlsm file: {path} ({error})") from error
    except OSError as error:
        raise _fail(f"workbook could not be read: {path} ({error.strerror})") from error


def resolve_sheet_name(path: Path, sheet: str | None) -> str:
    """Name the sheet a run will read, without reading any data from it.

    ``validate`` calls this so an operator can see which sheet a recipe
    selects before a run touches a record. A recipe that names no sheet gets
    the first one, which is what makes reporting the resolved name worth
    doing: sheet order is a property of the file, not of the recipe.
    """

    workbook = _load(path, data_only=True)
    try:
        names = list(workbook.sheetnames)
    finally:
        workbook.close()
    if not names:
        raise _fail(f"workbook has no sheets: {path}")
    if sheet is None:
        return str(names[0])
    if sheet not in names:
        available = ", ".join(repr(name) for name in names)
        raise _fail(f"workbook {path} has no sheet named {sheet!r}; it has: {available}")
    return str(sheet)


def _sheet_part(path: Path, sheet_name: str) -> str | None:
    """The zip member holding ``sheet_name``'s XML, or ``None`` if unmappable.

    Walks the OOXML package relationships rather than an openpyxl private
    attribute: ``xl/workbook.xml`` lists sheets in order with a relationship
    id, and ``xl/_rels/workbook.xml.rels`` maps that id to the part. Returns
    ``None`` only when the package omits one of those parts, in which case the
    caller falls back to the blank-header refusal.
    """

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
            return None
        book = ET.fromstring(archive.read("xl/workbook.xml"))  # noqa: S314 - see above
        rels = ET.fromstring(  # noqa: S314 - see above
            archive.read("xl/_rels/workbook.xml.rels")
        )

    targets = {
        rel.get("Id"): rel.get("Target", "") for rel in rels.findall(f"{{{_RELS_NS}}}Relationship")
    }
    for element in book.iter(f"{{{_SPREADSHEET_NS}}}sheet"):
        if element.get("name") != sheet_name:
            continue
        target = targets.get(element.get(f"{{{_DOC_RELS_NS}}}id", ""), "")
        if not target:
            return None
        member = target.lstrip("/")
        if not member.startswith("xl/"):
            member = f"xl/{member}"
        return member if member in names else None
    return None


def _merged_ranges_over_row(path: Path, part: str, row: int) -> list[str]:
    """A1-style merge refs on ``part`` whose row span covers ``row``.

    Streamed with ``iterparse`` and cleared as it goes, so a large sheet costs
    no more memory than a small one. This exists because openpyxl's read-only
    worksheets do not materialize merge ranges at all: a merged header cell
    reads as ``None``, indistinguishable from an empty one, which is the
    "blank" the issue's acceptance list refuses.
    """

    from openpyxl.utils.cell import range_boundaries

    refs: list[str] = []
    with zipfile.ZipFile(path) as archive, archive.open(part) as stream:
        for _, element in ET.iterparse(stream, events=("end",)):  # noqa: S314 - see above
            if element.tag != f"{{{_SPREADSHEET_NS}}}mergeCell":
                element.clear()
                continue
            ref = element.get("ref", "")
            element.clear()
            if not ref:
                continue
            try:
                _, min_row, _, max_row = range_boundaries(ref)
            except ValueError:
                continue
            if min_row is None or max_row is None:
                continue
            if min_row <= row <= max_row:
                refs.append(ref)
    return refs


def _cell_text(value: object) -> str:
    """Render one cell as the string a CSV of the same data would have held.

    Excel hands back typed values where a CSV hands back text, so the mapping
    layer downstream would otherwise see ``90210`` and ``"90210"`` as different
    inputs. Dates render ISO-8601 so a consent date column parses; a
    midnight-exact datetime renders as a plain date, because that is what a
    date-formatted cell is. Booleans render lowercase so ``TRUE`` in a consent
    column lands on the recognized ``"true"`` token instead of reading as an
    unrecognized status, which would fail closed to withheld.
    """

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dt.datetime):
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dt.time):
        return value.isoformat()
    return str(value)


def _is_formula(value: object) -> bool:
    """Whether a ``data_only=False`` read returned a formula rather than a value."""

    if isinstance(value, str):
        return value.startswith("=")
    return type(value).__name__ == "ArrayFormula"


def _read_grid(path: Path, sheet_name: str, *, data_only: bool) -> list[tuple[Any, ...]]:
    workbook = _load(path, data_only=data_only)
    try:
        if sheet_name not in workbook.sheetnames:
            available = ", ".join(repr(name) for name in workbook.sheetnames)
            raise _fail(f"workbook {path} has no sheet named {sheet_name!r}; it has: {available}")
        worksheet = workbook[sheet_name]
        return [tuple(row) for row in worksheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _headers(
    grid: list[tuple[Any, ...]],
    *,
    path: Path,
    sheet_name: str,
    header_row: int,
) -> list[str]:
    """The header names, refusing every shape that would drop a column silently."""

    from openpyxl.utils.cell import get_column_letter

    if header_row > len(grid):
        raise _fail(
            f"workbook {path} sheet {sheet_name!r} has no row {header_row} to read headers from "
            f"(the sheet holds {len(grid)} row(s))"
        )
    raw = list(grid[header_row - 1])
    while raw and _cell_text(raw[-1]).strip() == "":
        raw.pop()
    if not raw:
        raise _fail(f"workbook {path} sheet {sheet_name!r} has an empty header row {header_row}")

    headers: list[str] = []
    for index, value in enumerate(raw):
        text = _cell_text(value).strip()
        if not text:
            column = get_column_letter(index + 1)
            raise _fail(
                f"workbook {path} sheet {sheet_name!r} has a blank header at "
                f"{column}{header_row}; every column read must be named"
            )
        headers.append(text)

    duplicates = sorted({name for name in headers if headers.count(name) > 1})
    if duplicates:
        named = ", ".join(repr(name) for name in duplicates)
        raise _fail(
            f"workbook {path} sheet {sheet_name!r} header row {header_row} "
            f"repeats a column name: {named}"
        )
    return headers


def _guard_merged_header(path: Path, sheet_name: str, header_row: int) -> None:
    part = _sheet_part(path, sheet_name)
    if part is None:
        return
    merged = _merged_ranges_over_row(path, part, header_row)
    if merged:
        named = ", ".join(merged)
        raise _fail(
            f"workbook {path} sheet {sheet_name!r} has a merged cell across header row "
            f"{header_row} ({named}); unmerge it so every column has its own name"
        )


def _guard_uncomputed_formulas(
    path: Path,
    sheet_name: str,
    *,
    values: list[tuple[Any, ...]],
    header_row: int,
    width: int,
) -> None:
    """Refuse a data cell whose formula result Excel never cached.

    ``data_only=True`` returns ``None`` for such a cell, which is exactly what
    an empty cell returns. Reading it as blank would publish a value that was
    never computed, so the second, formula-visible pass is what tells the two
    apart. Only the columns under the header are checked: a scratch formula
    off to the side of the data is not part of the run's input.
    """

    from openpyxl.utils.cell import get_column_letter

    formulas = _read_grid(path, sheet_name, data_only=False)
    for row_index in range(header_row, min(len(values), len(formulas))):
        value_row = values[row_index]
        formula_row = formulas[row_index]
        for column_index in range(min(width, len(formula_row))):
            if column_index < len(value_row) and value_row[column_index] is not None:
                continue
            if _is_formula(formula_row[column_index]):
                cell = f"{get_column_letter(column_index + 1)}{row_index + 1}"
                raise _fail(
                    f"workbook {path} sheet {sheet_name!r} cell {cell} holds a formula whose "
                    "result was never saved; open and re-save the workbook in Excel, or "
                    "replace the formula with its value"
                )


def read_rows(
    path: Path,
    *,
    sheet: str | None = None,
    header_row: int = 1,
) -> list[dict[str, str]]:
    """Read one worksheet into the row dicts ``csv.DictReader`` would have made.

    Trailing wholly-blank rows are dropped: a workbook's used range routinely
    extends past its last real row, and minting a record from one would invent
    a person with no fields. Blank rows *between* data rows are kept, exactly
    as a CSV line of empty values is kept, so nothing between real rows is
    silently removed.
    """

    sheet_name = resolve_sheet_name(path, sheet)
    values = _read_grid(path, sheet_name, data_only=True)
    if not values:
        raise _fail(f"workbook {path} sheet {sheet_name!r} is empty")

    _guard_merged_header(path, sheet_name, header_row)
    headers = _headers(values, path=path, sheet_name=sheet_name, header_row=header_row)
    _guard_uncomputed_formulas(
        path,
        sheet_name,
        values=values,
        header_row=header_row,
        width=len(headers),
    )

    body = values[header_row:]
    while body and all(_cell_text(cell).strip() == "" for cell in body[-1][: len(headers)]):
        body.pop()

    rows: list[dict[str, str]] = []
    for row in body:
        rows.append(
            {
                header: _cell_text(row[index]) if index < len(row) else ""
                for index, header in enumerate(headers)
            }
        )
    return rows
