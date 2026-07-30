"""A workbook's unit is the sheet.

A spreadsheet is not prose, and the heading-unitizer that reads a render must
not be pointed at one. Converted, a workbook comes back as a stack of tables
and nothing else -- no headings at all -- so the prose path would make the
whole workbook a single section. Worse, its defences would misfire: a table row
that repeats on three sheets looks exactly like a running header to a rule that
drops short lines recurring across a document, and dropping a row of a
spreadsheet is losing data, not furniture.

So sheets are unitized directly. The converter says which sheet each table came
from -- it numbers sheets as pages -- and that is the whole of the structure a
workbook has: sheet 1, sheet 2, in order. One sheet is one section, its page
number is its own, and nothing is inferred.

The sheet's *name* is the one thing the conversion drops, and it is the only
title a sheet has. It is read back out of the workbook, which is a zip
container whose ``xl/workbook.xml`` lists the sheets in order -- the standard
library opens both, so this costs no dependency.
"""
from __future__ import annotations

import importlib.util
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree

from .mdunit import Section

SPREADSHEET_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"})
# The legacy binary format. The layout converter lists it among its inputs but
# cannot actually open it -- its Excel backend reads only the zip container --
# while xlrd reads the binary one directly, sheet names included. And a grid
# needs no layout reconstruction, so the converter detour would buy nothing
# even if it worked.
LEGACY_SUFFIXES = frozenset({".xls"})
# The namespace every OOXML workbook declares for its own elements.
_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# The OLE2 container magic every legacy Office file opens with.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# How often a column must be occupied for a region to read as a table. Below
# this a column is something a form happened to put a value in, not a column.
COLUMN_STEADY = 0.7
_RUN_OF_SPACES = re.compile(r"[ \t 　]{2,}")


@dataclass
class SheetReport:
    sheets: int = 0
    named: int = 0
    regions: int = 0
    tables: int = 0
    blocks: int = 0
    rows: int = 0
    columns_dropped: int = 0
    empty_sheets: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        named = (
            f"{self.named} named from the workbook"
            if self.named
            else "no sheet names in the file; numbered instead"
        )
        empty = f", {self.empty_sheets} empty" if self.empty_sheets else ""
        dropped = (
            f", {self.columns_dropped} empty column(s) dropped"
            if self.columns_dropped
            else ""
        )
        return (
            f"{self.sheets} sheet(s) ({named}); "
            f"{self.regions} region(s) -- {self.tables} table(s), "
            f"{self.blocks} field block(s) -- over {self.rows} row(s)"
            f"{dropped}{empty}"
        )

    def as_dict(self) -> dict:
        return {
            "sheets": self.sheets,
            "sheets_named": self.named,
            "regions": self.regions,
            "tables": self.tables,
            "field_blocks": self.blocks,
            "rows": self.rows,
            "columns_dropped": self.columns_dropped,
            "empty_sheets": self.empty_sheets,
            "notes": list(self.notes),
        }


def is_spreadsheet(path: Path) -> bool:
    return path.suffix.lower() in SPREADSHEET_SUFFIXES


def needs_converter(path: Path) -> bool:
    """Whether reading this workbook involves the layout converter at all."""
    return path.suffix.lower() not in LEGACY_SUFFIXES


def can_read_legacy() -> bool:
    return importlib.util.find_spec("xlrd") is not None


def _is_ole2(source: Path | BinaryIO) -> bool:
    if isinstance(source, (str, Path)):
        try:
            with open(source, "rb") as handle:
                return handle.read(8) == _OLE2_MAGIC
        except OSError:
            return False
    head = source.read(8)
    source.seek(0)
    return head == _OLE2_MAGIC


def _legacy_names(source: Path | BinaryIO) -> list[str]:
    try:
        import xlrd
    except ImportError:
        return []
    try:
        if isinstance(source, (str, Path)):
            book = xlrd.open_workbook(str(source), on_demand=True)
        else:
            book = xlrd.open_workbook(file_contents=source.read())
        return [str(name).strip() for name in book.sheet_names()]
    except Exception:
        # A corrupt container reads as no names, exactly like the zip path:
        # the caller numbers the sheets and says so.
        return []


def sheet_names(source: Path | BinaryIO) -> list[str]:
    """The workbook's sheet names, in order, or [] if they cannot be read.

    The OOXML container carries them in its own manifest; a legacy binary
    workbook is recognized by its OLE2 magic and read through xlrd. A format
    that keeps its names anywhere else (``.ods``) gets numbered sheets --
    which is what a reader sees in that case anyway.

    ``source`` is a path or an open binary stream: the app's web fallback
    holds an upload's bytes and no path, and the names are worth showing
    before anything is staged to disk.
    """
    if _is_ole2(source):
        return _legacy_names(source)
    try:
        with zipfile.ZipFile(source) as archive:
            workbook = archive.read("xl/workbook.xml")
    except (KeyError, OSError, zipfile.BadZipFile):
        return []
    try:
        root = ElementTree.fromstring(workbook)
    except ElementTree.ParseError:
        return []
    sheets = root.find(f"{_MAIN_NS}sheets")
    if sheets is None:
        return []
    return [
        (element.get("name") or "").strip()
        for element in sheets.findall(f"{_MAIN_NS}sheet")
    ]


def _cell_text(cell) -> str:
    """One cell as text, kept on one line so a table row stays a row.

    Runs of spaces are collapsed. A spreadsheet spaces a label out to fill a
    merged cell -- a title set as ``見  積  書``, a field label as ``상   호`` --
    and that padding is layout, not content: left in, it is what stops the
    label being found by the word it is.
    """
    text = cell.get("text") if isinstance(cell, dict) else cell
    text = str(text or "").replace("\n", " ").replace("|", "\\|")
    return _RUN_OF_SPACES.sub(" ", text).strip()


def _as_rows(grid) -> list[list[str]]:
    """A converter's grid, or plain strings, as rows of cell text."""
    return [[_cell_text(cell) for cell in row] for row in grid if row]


def _occupied(rows: list[list[str]]) -> list[set[int]]:
    return [{index for index, cell in enumerate(row) if cell} for row in rows]


def trim(rows: list[list[str]]) -> tuple[list[list[str]], int]:
    """Drop columns nothing occupies, and blank rows at either end.

    A form's grid is mostly empty: the columns exist because a cell was merged
    across them, not because anything was put there. Kept, they turn every row
    into a run of empty pipes that a reader has to look past and a search
    snippet has no room for.
    """
    if not rows:
        return [], 0
    width = max(len(row) for row in rows)
    filled = set().union(*_occupied(rows)) if rows else set()
    keep = [index for index in range(width) if index in filled]
    dropped = width - len(keep)
    squeezed = [[row[index] if index < len(row) else "" for index in keep] for row in rows]
    while squeezed and not any(squeezed[0]):
        squeezed.pop(0)
    while squeezed and not any(squeezed[-1]):
        squeezed.pop()
    return squeezed, dropped


def split_regions(rows: list[list[str]]) -> list[list[list[str]]]:
    """Runs of rows separated by blank ones: a sheet's own paragraphing.

    A blank row is the one structural mark a spreadsheet has. A quotation form
    uses it to part the title from the addressee block, that from the totals,
    and those from the line items -- four things a reader sees as four, and one
    grid sees as one.
    """
    regions: list[list[list[str]]] = []
    current: list[list[str]] = []
    for row in rows:
        if any(row):
            current.append(row)
        elif current:
            regions.append(current)
            current = []
    if current:
        regions.append(current)
    return regions


def is_table(rows: list[list[str]]) -> bool:
    """Whether a region is a table rather than a block of labelled fields.

    The test is column alignment, not cell count: a table fills the same
    columns row after row, while a form scatters a label here and a value
    there. Measured on a real quotation form, the addressee block filled one
    column consistently and the rest a third of the time; its line-item table
    filled four columns in nine rows out of ten.
    """
    if len(rows) < 2:
        return False
    occupied = _occupied(rows)
    width = max((len(row) for row in rows), default=0)
    steady = sum(
        1
        for column in range(width)
        if sum(column in row for row in occupied) >= len(rows) * COLUMN_STEADY
    )
    return steady >= 2


def markdown_table(grid) -> str:
    """A grid of cells as a Markdown table, its first row the header.

    Rendered here rather than taken from the converter's Markdown, because the
    block stream is the contract: it says which sheet each table belongs to,
    and the render does not.
    """
    rows = _as_rows(grid)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    head, rest = rows[0], rows[1:]
    lines = [
        "| " + " | ".join(head) + " |",
        "|" + "|".join(["---"] * width) + "|",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rest]
    return "\n".join(lines)


def _field_lines(rows: list[list[str]]) -> str:
    """A block of fields as lines, since it is not a table.

    The cells of a row are joined, in the order they sit, and nothing more is
    claimed about them. Reading two cells as a label and its value was tried
    and is wrong: a form often runs two field groups side by side, so the left
    cell of a row is one group's *value* and the right cell is the other
    group's *label*, and a colon between them asserts a pair that is not
    there. A separator says only what is true -- these were on one row.
    """
    lines = []
    for row in rows:
        cells = [cell for cell in row if cell]
        if cells:
            lines.append(" · ".join(cells))
    return "\n".join(lines)


def render_sheet(regions: list[list[list[str]]], report: "SheetReport") -> str:
    """One sheet's regions, each rendered as what it turned out to be."""
    parts = []
    for region in regions:
        rows, dropped = trim(_as_rows(region))
        report.columns_dropped += dropped
        if not rows:
            continue
        report.regions += 1
        report.rows += len(rows)
        if is_table(rows):
            report.tables += 1
            parts.append(markdown_table(rows))
        else:
            report.blocks += 1
            parts.append(_field_lines(rows))
    return "\n\n".join(part for part in parts if part)


def _sheet_bodies(document: dict, report: SheetReport) -> dict[int, list[str]]:
    """Each sheet's content, in the order the converter emitted it.

    The converter already parts a sheet into regions -- it emits one table per
    run of occupied rows -- so each of those is offered to the same renderer
    the legacy path uses, and a one-cell "table" holding a title comes out as
    the line it is rather than a table with a header rule under it.
    """
    bodies: dict[int, list[str]] = {}
    for table in document.get("tables") or []:
        prov = (table.get("prov") or [{}])[0]
        page = prov.get("page_no")
        if page is None:
            continue
        grid = (table.get("data") or {}).get("grid") or []
        rendered = render_sheet([grid], report)
        if not rendered:
            continue
        bodies.setdefault(int(page), []).append(rendered)
    for text in document.get("texts") or []:
        prov = (text.get("prov") or [{}])[0]
        page = prov.get("page_no")
        content = (text.get("orig") or text.get("text") or "").strip()
        if page is None or not content:
            continue
        bodies.setdefault(int(page), []).append(content)
    return bodies


def _trim_float(value: float) -> str:
    """A whole number without its trailing ``.0``; anything else as written.

    A cell holding 1200000 comes out of the binary format as 1200000.0, and a
    price rendered with a decimal point it never had reads as an error.
    """
    return str(int(value)) if value == int(value) else str(value)


def read_xls(path: Path) -> tuple[list[Section], SheetReport]:
    """One section per sheet, read straight from the legacy binary workbook.

    No converter is involved: the layout converter cannot open a ``.xls`` at
    all, and a grid needs no layout reconstruction, so there is nothing for it
    to add. xlrd reads the cells, the sheet names, and the date epoch; the
    sections come out shaped exactly as the converter path shapes them.
    """
    try:
        import xlrd
    except ImportError as exc:
        raise SystemExit(
            "Reading a legacy .xls workbook needs xlrd:\n"
            "  python -m pip install xlrd\n"
            "or save it as .xlsx and add that instead."
        ) from exc

    def display(kind: int, value, datemode: int) -> str:
        if kind == xlrd.XL_CELL_NUMBER:
            return _trim_float(value)
        if kind == xlrd.XL_CELL_DATE:
            moment = xlrd.xldate.xldate_as_datetime(value, datemode)
            if (moment.hour, moment.minute, moment.second) == (0, 0, 0):
                return moment.date().isoformat()
            return moment.isoformat(sep=" ")
        if kind == xlrd.XL_CELL_BOOLEAN:
            return "TRUE" if value else "FALSE"
        if kind == xlrd.XL_CELL_ERROR:
            return ""
        return str(value)

    book = xlrd.open_workbook(str(path))
    report = SheetReport()
    sections: list[Section] = []
    for index, sheet in enumerate(book.sheets(), start=1):
        # The blank rows are kept at this stage: they are where the sheet
        # parts one region from the next, which is the only structure it has.
        grid = [
            [
                display(
                    sheet.cell_type(row_index, col),
                    sheet.cell_value(row_index, col),
                    book.datemode,
                )
                for col in range(sheet.ncols)
            ]
            for row_index in range(sheet.nrows)
        ]
        rendered = render_sheet(split_regions(_as_rows(grid)), report)
        name = (sheet.name or "").strip()
        if name:
            report.named += 1
        title = name or f"Sheet {index}"
        if not rendered:
            report.empty_sheets += 1
        sections.append(
            Section(order=index, level=1, title=title, parent=title, body=rendered)
        )
    report.sheets = len(sections)
    report.notes.append(
        "legacy binary workbook, read directly with xlrd; the layout converter "
        "cannot open this format"
    )
    return sections, report


def unitize(blocks_path: Path, names: list[str]) -> tuple[list[Section], SheetReport]:
    """One section per sheet, titled by the sheet's own name.

    ``Section.order`` is the sheet's number, which is also its page, so the
    manifest's page range says "sheet 2" and means it.
    """
    report = SheetReport()
    document = json.loads(Path(blocks_path).read_text(encoding="utf-8"))
    bodies = _sheet_bodies(document, report)
    pages = sorted(set(bodies) | {index + 1 for index, _ in enumerate(names)})
    sections: list[Section] = []
    for page in pages:
        name = names[page - 1].strip() if page - 1 < len(names) else ""
        if name:
            report.named += 1
        title = name or f"Sheet {page}"
        body = "\n\n".join(bodies.get(page, []))
        if not body:
            report.empty_sheets += 1
        sections.append(
            Section(order=page, level=1, title=title, parent=title, body=body)
        )
    report.sheets = len(sections)
    if not names:
        report.notes.append(
            "sheet names could not be read from the workbook; sheets are numbered"
        )
    return sections, report
