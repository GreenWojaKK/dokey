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


@dataclass
class SheetReport:
    sheets: int = 0
    named: int = 0
    tables: int = 0
    rows: int = 0
    empty_sheets: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        named = (
            f"{self.named} named from the workbook"
            if self.named
            else "no sheet names in the file; numbered instead"
        )
        empty = f", {self.empty_sheets} empty" if self.empty_sheets else ""
        return (
            f"{self.sheets} sheet(s) ({named}); "
            f"{self.tables} table(s), {self.rows} row(s){empty}"
        )

    def as_dict(self) -> dict:
        return {
            "sheets": self.sheets,
            "sheets_named": self.named,
            "tables": self.tables,
            "rows": self.rows,
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


def _cell_text(cell: dict) -> str:
    """One cell as text, kept on one line so a table row stays a row."""
    text = str(cell.get("text") or "")
    return text.replace("|", "\\|").replace("\n", " ").strip()


def markdown_table(grid: list[list[dict]]) -> str:
    """A grid of cells as a Markdown table.

    Rendered here rather than taken from the converter's Markdown, because the
    block stream is the contract: it says which sheet each table belongs to,
    and the render does not.
    """
    rows = [[_cell_text(cell) for cell in row] for row in grid if row]
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


def _sheet_bodies(document: dict, report: SheetReport) -> dict[int, list[str]]:
    """Each sheet's content, in the order the converter emitted it."""
    bodies: dict[int, list[str]] = {}
    for table in document.get("tables") or []:
        prov = (table.get("prov") or [{}])[0]
        page = prov.get("page_no")
        if page is None:
            continue
        grid = (table.get("data") or {}).get("grid") or []
        rendered = markdown_table(grid)
        if not rendered:
            continue
        report.tables += 1
        report.rows += len(grid)
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
        grid = []
        for row_index in range(sheet.nrows):
            row = [
                {
                    "text": display(
                        sheet.cell_type(row_index, col),
                        sheet.cell_value(row_index, col),
                        book.datemode,
                    )
                }
                for col in range(sheet.ncols)
            ]
            # A form sheet uses blank rows as spacing; an empty table row
            # carries nothing a search could hit.
            if any(cell["text"].strip() for cell in row):
                grid.append(row)
        rendered = markdown_table(grid)
        name = (sheet.name or "").strip()
        if name:
            report.named += 1
        title = name or f"Sheet {index}"
        if rendered:
            report.tables += 1
            report.rows += len(grid)
        else:
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
