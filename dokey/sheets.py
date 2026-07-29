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

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree

from .mdunit import Section

SPREADSHEET_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"})
# The namespace every OOXML workbook declares for its own elements.
_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


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


def sheet_names(source: Path | BinaryIO) -> list[str]:
    """The workbook's sheet names, in order, or [] if they cannot be read.

    Only the OOXML container is understood. A legacy ``.xls`` or an ``.ods``
    keeps its names somewhere else, and rather than guess, the sheets are
    numbered -- which is what a reader sees in that case anyway.

    ``source`` is a path or an open binary stream: the app's web fallback
    holds an upload's bytes and no path, and the names are worth showing
    before anything is staged to disk.
    """
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
