"""A workbook read from its own file, in the order its evidence is stated.

A spreadsheet is not a document that lost its structure and needs a layout
engine to get it back. The structure is *in the file*: every cell is named by
its coordinate, typed, and dated; merges are declared; the author's own
paragraphing -- blank rows -- is right there in which rows are occupied. A
layout converter reconstructs; here there is nothing to reconstruct, only to
read. So this module reads the file itself, and the converter is kept for the
one case it earns: a format no reader here opens.

The reading order follows what depends on what:

1. **The coordinate space first.** Cells with their coordinates, types and
   merges. Everything else in a workbook is anchored to this space, so it is
   read first and *kept*: ``bronze/cells.jsonl`` records every cell under its
   own address, which is what makes the rendered sections checkable the way
   ``items.jsonl`` makes prose checkable. Trimming and joining are rendering
   choices on top; they no longer destroy the addresses.

2. **The author's marks part the sheet.** A blank row is the one paragraphing
   mark a grid has, and a merge is the author stating "these coordinates are
   one cell". A sheet is parted at its blank rows; a row whose single value
   sits on a merge spanning its region is a banner, not a table row.

3. **A region is what its addressing says it is.** A cell means something
   either through its *column* (the column is a field, each row a record: a
   table) or through its *neighbour* (the label beside it names it: a form).
   Column steadiness tells the two apart, and a table's header is *proven*,
   not assumed: a row of text over rows of numbers is evidence; first-row
   position is only a fallback, and which one decided is recorded.

The legacy binary format takes the same path through xlrd, which hands over
the same evidence (cells, types, merges). Only a format neither reader opens
(``.ods``, ``.xlsb``) still goes to the layout converter, whose block stream
is then read by the same region logic.
"""
from __future__ import annotations

import importlib.util
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree

from .mdunit import Section

SPREADSHEET_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xlsb", ".xls", ".ods"})
# Read natively: the OOXML container is a zip of XML that states its own
# structure, and the standard library opens both.
NATIVE_SUFFIXES = frozenset({".xlsx", ".xlsm"})
# Read through xlrd: the legacy binary container.
LEGACY_SUFFIXES = frozenset({".xls"})

# How often a column must be occupied for a region to read as a table. Below
# this a column is something a form happened to put a value in, not a column.
COLUMN_STEADY = 0.7
# How much of a region's width a merge must span for its row to read as a
# banner. Not 1.0: a banner is often set beside a narrow margin column that
# the merge does not reach.
BANNER_SHARE = 0.8

_RUN_OF_SPACES = re.compile(r"[ \t 　]{2,}")
# The OLE2 container magic every legacy Office file opens with.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")

# OOXML namespaces, spelled once.
_SS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Number-format ids the spec reserves for dates and times, including the CJK
# locale range -- Korean workbooks lean on 27-36 and 50-58.
_BUILTIN_DATE_FORMATS = frozenset(
    range(14, 23)
) | frozenset(range(27, 37)) | frozenset(range(45, 48)) | frozenset(range(50, 59))


@dataclass
class SheetReport:
    sheets: int = 0
    named: int = 0
    regions: int = 0
    tables: int = 0
    blocks: int = 0
    banners: int = 0
    rows: int = 0
    merges: int = 0
    columns_dropped: int = 0
    header_types: int = 0
    header_converter: int = 0
    header_position: int = 0
    charts: int = 0
    images: int = 0
    shapes: int = 0
    empty_sheets: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        named = (
            f"{self.named} named from the workbook"
            if self.named
            else "no sheet names in the file; numbered instead"
        )
        parts = [
            f"{self.sheets} sheet(s) ({named})",
            f"{self.regions} region(s) -- {self.tables} table(s), "
            f"{self.blocks} field block(s), {self.banners} banner(s) -- "
            f"over {self.rows} row(s)",
        ]
        proven = self.header_types + self.header_converter
        if self.tables:
            parts.append(
                f"headers {proven} proven, {self.header_position} assumed"
            )
        if self.merges:
            parts.append(f"{self.merges} merge(s)")
        if self.charts or self.images or self.shapes:
            parts.append(
                f"objects: {self.charts} chart(s), {self.images} image(s), "
                f"{self.shapes} text shape(s)"
            )
        if self.columns_dropped:
            parts.append(f"{self.columns_dropped} empty column(s) dropped")
        if self.empty_sheets:
            parts.append(f"{self.empty_sheets} empty sheet(s)")
        return "; ".join(parts)

    def as_dict(self) -> dict:
        return {
            "sheets": self.sheets,
            "sheets_named": self.named,
            "regions": self.regions,
            "tables": self.tables,
            "field_blocks": self.blocks,
            "banners": self.banners,
            "rows": self.rows,
            "merges": self.merges,
            "columns_dropped": self.columns_dropped,
            "headers": {
                "types": self.header_types,
                "converter": self.header_converter,
                "position": self.header_position,
            },
            "charts": self.charts,
            "images": self.images,
            "shapes": self.shapes,
            "empty_sheets": self.empty_sheets,
            "notes": list(self.notes),
        }


def is_spreadsheet(path: Path) -> bool:
    return path.suffix.lower() in SPREADSHEET_SUFFIXES


def needs_converter(path: Path) -> bool:
    """Whether reading this workbook involves the layout converter at all.

    Only the formats no reader here opens. The OOXML zip and the legacy
    binary both carry their structure and are read from the file directly.
    """
    return path.suffix.lower() not in (NATIVE_SUFFIXES | LEGACY_SUFFIXES)


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


def is_legacy_workbook(path: Path) -> bool:
    """Recognized by its container magic, not its suffix: files in the wild
    wear extensions their bytes do not honour."""
    return _is_ole2(path)


def is_native_workbook(source: Path | BinaryIO) -> bool:
    """A zip that carries a workbook manifest is an OOXML workbook."""
    try:
        with zipfile.ZipFile(source) as archive:
            return "xl/workbook.xml" in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


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
    sheets = root.find(f"{_SS}sheets")
    if sheets is None:
        return []
    return [
        (element.get("name") or "").strip()
        for element in sheets.findall(f"{_SS}sheet")
    ]


# --------------------------------------------------------------------------
# The coordinate space: cells as the file states them.


@dataclass(frozen=True)
class Cell:
    """One cell as stated: its text, its kind, and the formula if written.

    ``kind`` is what the file said the value is -- text, number, date, bool,
    error -- not an inference. It is what lets a header be proven rather than
    assumed: a row of text over rows of numbers is the file's own evidence.
    """

    text: str
    kind: str = "text"
    formula: str | None = None


@dataclass
class SheetGrid:
    """A sheet as read: named, sparse, with its merges as declared."""

    name: str
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    merges: list[tuple[int, int, int, int]] = field(default_factory=list)


def _col_letters(col: int) -> str:
    out = ""
    while col:
        col, rem = divmod(col - 1, 26)
        out = chr(65 + rem) + out
    return out


def _col_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index


def cell_ref(row: int, col: int) -> str:
    return f"{_col_letters(col)}{row}"


def _parse_ref(ref: str) -> tuple[int, int] | None:
    match = _CELL_REF.match(ref)
    if match is None:
        return None
    return int(match.group(2)), _col_index(match.group(1))


# --------------------------------------------------------------------------
# Rendering primitives. These are choices about presentation; the addresses
# they fold away survive in the cell records.


def _cell_text(cell) -> str:
    """One cell as text, kept on one line so a table row stays a row.

    Runs of spaces are collapsed. A spreadsheet spaces a label out to fill a
    merged cell, and that padding is layout, not content: left in, it is what
    stops the label being found by the word it is.
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
    across them, not because anything was put there. This is a rendering
    choice -- the cell records keep every address the fold removes.
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

    A blank row is the one paragraphing mark a spreadsheet has. A form uses
    it to part the title from the addressee block, that from the totals, and
    those from the line items -- four things a reader sees as four, and one
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
    there. This is the difference in how a cell gets its meaning -- from its
    column, or from its neighbour.
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
    """A grid of cells as a Markdown table, its first row the header."""
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
    group's *label*. A separator says only what is true -- these were on one
    row.
    """
    lines = []
    for row in rows:
        cells = [cell for cell in row if cell]
        if cells:
            lines.append(" · ".join(cells))
    return "\n".join(lines)


def _count_basis(report: SheetReport, basis: str) -> None:
    if basis == "types":
        report.header_types += 1
    elif basis == "converter":
        report.header_converter += 1
    else:
        report.header_position += 1


def _grid_flags_header(grid) -> bool:
    """Whether the converter marked the first row as a header.

    The converter's flag is a statement already made; guessing over it would
    be reconstructing an answer the file arrived with.
    """
    first = grid[0] if grid else []
    return any(isinstance(cell, dict) and cell.get("column_header") for cell in first)


def render_sheet(regions, report: SheetReport) -> str:
    """Regions from a converter's block stream, each as what it turns out to be."""
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
            _count_basis(report, "converter" if _grid_flags_header(region) else "position")
            parts.append(markdown_table(rows))
        else:
            report.blocks += 1
            parts.append(_field_lines(rows))
    return "\n\n".join(part for part in parts if part)


# --------------------------------------------------------------------------
# Assembling sections from the coordinate space.


def _kind_basis(cell_rows: list[list[Cell | None]]) -> str | None:
    """Proof that the first row is a header, from the file's own types.

    A header names its columns and a data row realizes them, so a first row
    of text standing over rows that carry numbers or dates is the file's own
    evidence. No contrast, no proof -- position then stands, and is counted
    as an assumption rather than dressed up as a finding.
    """
    if len(cell_rows) < 2:
        return None
    head = [cell for cell in cell_rows[0] if cell is not None and cell.text]
    if not head:
        return None
    if any(cell.kind in ("number", "date") for cell in head):
        return None
    for row in cell_rows[1:]:
        if any(
            cell is not None and cell.text and cell.kind in ("number", "date")
            for cell in row
        ):
            return "types"
    return None


def _is_banner(
    row: int,
    cells_in_row: dict[int, Cell],
    merges: list[tuple[int, int, int, int]],
    span: int,
) -> bool:
    """A row whose single value sits on a merge spanning its region.

    The merge is the author's own statement that the coordinates are one
    cell; when that one cell is as wide as the region, the row is a title or
    a totals banner, not a table row. A banner standing alone between blank
    rows makes its region's occupied width degenerate, so the merge's own
    width -- also the author's statement -- is what carries the claim there.
    """
    if len(cells_in_row) != 1:
        return False
    col = next(iter(cells_in_row))
    for r1, c1, r2, c2 in merges:
        if r1 == row == r2 and c1 <= col <= c2:
            width = c2 - c1 + 1
            if width >= 2 and width >= span * BANNER_SHARE:
                return True
    return False


def _render_run(
    run: list[int],
    rows_map: dict[int, dict[int, Cell]],
    merges: list[tuple[int, int, int, int]],
    sheet_index: int,
    report: SheetReport,
    region_records: list[dict],
) -> list[str]:
    min_col = min(min(rows_map[row]) for row in run)
    max_col = max(max(rows_map[row]) for row in run)
    span = max_col - min_col + 1

    segments: list[tuple[str, list[int]]] = []
    current: list[int] = []
    for row in run:
        if _is_banner(row, rows_map[row], merges, span):
            if current:
                segments.append(("rows", current))
                current = []
            segments.append(("banner", [row]))
        else:
            current.append(row)
    if current:
        segments.append(("rows", current))

    parts: list[str] = []
    for kind, rows in segments:
        if kind == "banner":
            row = rows[0]
            text = " · ".join(
                rows_map[row][col].text for col in sorted(rows_map[row])
            )
            report.regions += 1
            report.banners += 1
            report.rows += 1
            region_records.append({"sheet": sheet_index, "rows": [row, row], "kind": "banner"})
            parts.append(text)
            continue
        cell_rows = [
            [rows_map[row].get(col) for col in range(min_col, max_col + 1)]
            for row in rows
        ]
        text_rows = [
            [(cell.text if cell else "") for cell in row] for row in cell_rows
        ]
        trimmed, dropped = trim(text_rows)
        report.columns_dropped += dropped
        if not trimmed:
            continue
        report.regions += 1
        report.rows += len(trimmed)
        record: dict = {"sheet": sheet_index, "rows": [rows[0], rows[-1]]}
        if is_table(trimmed):
            report.tables += 1
            basis = _kind_basis(cell_rows) or "position"
            _count_basis(report, basis)
            record.update(kind="table", header_basis=basis)
            parts.append(markdown_table(trimmed))
        else:
            report.blocks += 1
            record["kind"] = "fields"
            parts.append(_field_lines(trimmed))
        region_records.append(record)
    return parts


def _render_grid(
    grid: SheetGrid,
    sheet_index: int,
    report: SheetReport,
    region_records: list[dict],
) -> str:
    rows_map: dict[int, dict[int, Cell]] = {}
    for (row, col), cell in grid.cells.items():
        if cell.text:
            rows_map.setdefault(row, {})[col] = cell

    parts: list[str] = []
    run: list[int] = []
    for row in sorted(rows_map):
        if run and row == run[-1] + 1:
            run.append(row)
        else:
            if run:
                parts.extend(
                    _render_run(run, rows_map, grid.merges, sheet_index, report, region_records)
                )
            run = [row]
    if run:
        parts.extend(
            _render_run(run, rows_map, grid.merges, sheet_index, report, region_records)
        )
    return "\n\n".join(part for part in parts if part)


@dataclass
class WorkbookRead:
    """Everything a workbook read yields, evidence first.

    ``cells`` is the address layer -- every cell under its own reference, the
    record the rendered ``sections`` can be checked against. ``regions`` says
    what each stretch of rows was read as, and on what basis its header was
    decided.
    """

    sections: list
    report: SheetReport
    cells: list[dict] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    objects: list[dict] = field(default_factory=list)
    media: dict[str, bytes] = field(default_factory=dict)


def sections_from_grids(grids: list[SheetGrid]) -> WorkbookRead:
    """One sheet, one section; every cell kept under its own address."""
    report = SheetReport()
    sections: list[Section] = []
    cell_records: list[dict] = []
    region_records: list[dict] = []
    for index, grid in enumerate(grids, start=1):
        name = (grid.name or "").strip()
        if name:
            report.named += 1
        title = name or f"Sheet {index}"
        report.merges += len(grid.merges)
        merge_at = {
            (r1, c1): f"{cell_ref(r1, c1)}:{cell_ref(r2, c2)}"
            for r1, c1, r2, c2 in grid.merges
        }
        for (row, col) in sorted(grid.cells):
            cell = grid.cells[(row, col)]
            record = {
                "sheet": index,
                "sheet_name": title,
                "ref": cell_ref(row, col),
                "row": row,
                "col": col,
                "kind": cell.kind,
                "text": cell.text,
            }
            if cell.formula:
                record["formula"] = cell.formula
            if (row, col) in merge_at:
                record["merge"] = merge_at[(row, col)]
            cell_records.append(record)
        body = _render_grid(grid, index, report, region_records)
        if not body:
            report.empty_sheets += 1
        sections.append(
            Section(order=index, level=1, title=title, parent=title, body=body)
        )
    report.sheets = len(sections)
    return WorkbookRead(
        sections=sections, report=report, cells=cell_records, regions=region_records
    )


# --------------------------------------------------------------------------
# The native reader: OOXML, through the standard library.


def _rels(archive: zipfile.ZipFile, part: str) -> dict[str, str]:
    """A part's relationships, targets resolved against the part's folder."""
    folder, _, name = part.rpartition("/")
    rels_part = f"{folder}/_rels/{name}.rels" if folder else f"_rels/{name}.rels"
    try:
        data = archive.read(rels_part)
    except KeyError:
        return {}
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return {}
    resolved: dict[str, str] = {}
    for rel in root.findall(f"{_PKG_REL}Relationship"):
        target = rel.get("Target") or ""
        if target.startswith("/"):
            target = target.lstrip("/")
        else:
            base = folder.split("/") if folder else []
            for piece in target.split("/"):
                if piece == "..":
                    if base:
                        base.pop()
                elif piece and piece != ".":
                    base.append(piece)
            target = "/".join(base)
        resolved[rel.get("Id") or ""] = target
    return resolved


def _workbook_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(name, worksheet part) in the workbook's own order."""
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = _rels(archive, "xl/workbook.xml")
    sheets = []
    container = root.find(f"{_SS}sheets")
    for element in container.findall(f"{_SS}sheet") if container is not None else []:
        rid = element.get(f"{_DOC_REL}id") or ""
        part = rels.get(rid)
        if part:
            sheets.append(((element.get("name") or "").strip(), part))
    return sheets


def _uses_1904_epoch(archive: zipfile.ZipFile) -> bool:
    try:
        root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    except (KeyError, ElementTree.ParseError):
        return False
    props = root.find(f"{_SS}workbookPr")
    return props is not None and (props.get("date1904") or "").lower() in ("1", "true")


def _rich_text(element) -> str:
    """The text of a shared or inline string, runs joined, phonetics skipped."""
    parts = []
    for child in element:
        if child.tag == f"{_SS}t":
            parts.append(child.text or "")
        elif child.tag == f"{_SS}r":
            t = child.find(f"{_SS}t")
            if t is not None:
                parts.append(t.text or "")
        # rPh / phoneticPr carry furigana, which would double the text.
    return "".join(parts)


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        data = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return []
    return [_rich_text(si) for si in root.findall(f"{_SS}si")]


def _looks_like_date_format(code: str) -> bool:
    """Whether a custom number format renders a date or time.

    Quoted literals and bracketed sections (colors, locales, elapsed markers)
    are not format tokens and are stripped before looking.
    """
    code = re.sub(r'"[^"]*"', "", code)
    code = re.sub(r"\[[^\]]*\]", "", code)
    code = code.replace("\\", "")
    if re.search(r"[dyhDYH]", code):
        return True
    return "m" in code.lower() and any(ch in code for ch in ":/-년월일")


def _date_styles(archive: zipfile.ZipFile) -> set[int]:
    """Indexes of cell formats that render as dates."""
    try:
        data = archive.read("xl/styles.xml")
    except KeyError:
        return set()
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return set()
    date_ids = set(_BUILTIN_DATE_FORMATS)
    formats = root.find(f"{_SS}numFmts")
    for fmt in formats.findall(f"{_SS}numFmt") if formats is not None else []:
        if _looks_like_date_format(fmt.get("formatCode") or ""):
            date_ids.add(int(fmt.get("numFmtId") or -1))
    styles: set[int] = set()
    xfs = root.find(f"{_SS}cellXfs")
    for index, xf in enumerate(xfs.findall(f"{_SS}xf") if xfs is not None else []):
        if int(xf.get("numFmtId") or 0) in date_ids:
            styles.add(index)
    return styles


def _trim_float(value: float) -> str:
    """A whole number without its trailing ``.0``; anything else as written.

    The formats store every number as a float, and a price rendered with a
    decimal point it never had reads as an error.
    """
    return str(int(value)) if value == int(value) else str(value)


def _date_text(serial: float, date1904: bool) -> str:
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    moment = base + timedelta(days=serial)
    if moment.microsecond >= 500_000:
        moment += timedelta(seconds=1)
    moment = moment.replace(microsecond=0)
    if (moment.hour, moment.minute, moment.second) == (0, 0, 0):
        return moment.date().isoformat()
    return moment.isoformat(sep=" ")


def _parse_worksheet(
    data: bytes, shared: list[str], date_styles: set[int], date1904: bool
) -> tuple[dict[tuple[int, int], Cell], list[tuple[int, int, int, int]], str | None]:
    root = ElementTree.fromstring(data)
    cells: dict[tuple[int, int], Cell] = {}
    row_counter = 0
    for row_el in root.iter(f"{_SS}row"):
        row_counter = int(row_el.get("r") or row_counter + 1)
        col_counter = 0
        for c_el in row_el.findall(f"{_SS}c"):
            ref = c_el.get("r")
            parsed = _parse_ref(ref) if ref else None
            if parsed is not None:
                row, col = parsed
            else:
                row, col = row_counter, col_counter + 1
            col_counter = col

            kind = "text"
            text = ""
            t = c_el.get("t") or "n"
            v_el = c_el.find(f"{_SS}v")
            f_el = c_el.find(f"{_SS}f")
            value = v_el.text if v_el is not None and v_el.text is not None else ""
            if t == "s":
                index = int(value) if value else -1
                text = shared[index] if 0 <= index < len(shared) else ""
            elif t == "inlineStr":
                is_el = c_el.find(f"{_SS}is")
                text = _rich_text(is_el) if is_el is not None else ""
            elif t == "str":
                text = value
            elif t == "b":
                kind = "bool"
                text = "TRUE" if value.strip() == "1" else "FALSE"
            elif t == "e":
                kind = "error"
                text = ""
            else:  # a number, dated or not, by its style
                if value.strip():
                    serial = float(value)
                    style = int(c_el.get("s") or 0)
                    if style in date_styles:
                        kind = "date"
                        text = _date_text(serial, date1904)
                    else:
                        kind = "number"
                        text = _trim_float(serial)
            formula = f_el.text if f_el is not None and f_el.text else None
            if not text and not formula:
                continue
            cells[(row, col)] = Cell(
                text=_RUN_OF_SPACES.sub(" ", text.replace("\n", " ")).strip(),
                kind=kind,
                formula=formula,
            )

    merges: list[tuple[int, int, int, int]] = []
    merge_container = root.find(f"{_SS}mergeCells")
    for merge in (
        merge_container.findall(f"{_SS}mergeCell") if merge_container is not None else []
    ):
        ref = merge.get("ref") or ""
        if ":" not in ref:
            continue
        start, end = ref.split(":", 1)
        a, b = _parse_ref(start), _parse_ref(end)
        if a and b:
            merges.append((a[0], a[1], b[0], b[1]))

    drawing_el = root.find(f"{_SS}drawing")
    drawing_rid = drawing_el.get(f"{_DOC_REL}id") if drawing_el is not None else None
    return cells, merges, drawing_rid


def read_xlsx(path: Path) -> WorkbookRead:
    """Read an OOXML workbook from the file itself. No converter involved.

    The zip states everything this needs: cells named by coordinate, types on
    the cells, dates by their number formats, merges declared, sheets named
    in the manifest. The standard library opens all of it.
    """
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(
            f"Not a workbook this reader can open: {path.name}. If it came "
            "from another program, open it in a spreadsheet and save it as "
            ".xlsx."
        ) from exc
    with archive:
        shared = _shared_strings(archive)
        date_styles = _date_styles(archive)
        date1904 = _uses_1904_epoch(archive)
        grids: list[SheetGrid] = []
        drawing_rids: list[tuple[int, str, str | None]] = []
        for index, (name, part) in enumerate(_workbook_sheets(archive), start=1):
            cells, merges, drawing_rid = _parse_worksheet(
                archive.read(part), shared, date_styles, date1904
            )
            grids.append(SheetGrid(name=name, cells=cells, merges=merges))
            drawing_rids.append((index, part, drawing_rid))
        read = sections_from_grids(grids)
        _read_objects(archive, grids, drawing_rids, read)
    return read


# --------------------------------------------------------------------------
# The legacy reader: the same evidence, through xlrd.


def read_xls(path: Path) -> WorkbookRead:
    """Read the legacy binary workbook directly. No converter involved.

    The layout converter cannot open a ``.xls`` at all; xlrd reads the cells,
    the types, the merges, the sheet names and the date epoch, which is the
    same evidence the native reader collects. What xlrd does not expose --
    charts and pictures -- is said in the notes rather than silently absent.
    """
    try:
        import xlrd
    except ImportError as exc:
        raise SystemExit(
            "Reading a legacy .xls workbook needs xlrd:\n"
            "  python -m pip install xlrd\n"
            "or save it as .xlsx and add that instead."
        ) from exc

    def display(kind: int, value, datemode: int) -> tuple[str, str]:
        if kind == xlrd.XL_CELL_NUMBER:
            return _trim_float(value), "number"
        if kind == xlrd.XL_CELL_DATE:
            moment = xlrd.xldate.xldate_as_datetime(value, datemode)
            if (moment.hour, moment.minute, moment.second) == (0, 0, 0):
                return moment.date().isoformat(), "date"
            return moment.isoformat(sep=" "), "date"
        if kind == xlrd.XL_CELL_BOOLEAN:
            return ("TRUE" if value else "FALSE"), "bool"
        if kind == xlrd.XL_CELL_ERROR:
            return "", "error"
        return str(value), "text"

    try:
        book = xlrd.open_workbook(str(path), formatting_info=True)
    except Exception:
        book = xlrd.open_workbook(str(path))

    grids: list[SheetGrid] = []
    for sheet in book.sheets():
        cells: dict[tuple[int, int], Cell] = {}
        for row in range(sheet.nrows):
            for col in range(sheet.ncols):
                text, kind = display(
                    sheet.cell_type(row, col), sheet.cell_value(row, col), book.datemode
                )
                if not text:
                    continue
                cells[(row + 1, col + 1)] = Cell(
                    text=_RUN_OF_SPACES.sub(" ", text.replace("\n", " ")).strip(),
                    kind=kind,
                )
        merges = [
            (r1 + 1, c1 + 1, r2, c2)
            for r1, r2, c1, c2 in getattr(sheet, "merged_cells", [])
        ]
        grids.append(SheetGrid(name=sheet.name, cells=cells, merges=merges))

    read = sections_from_grids(grids)
    read.report.notes.append(
        "legacy binary workbook, read directly with xlrd; the layout converter "
        "cannot open this format"
    )
    read.report.notes.append(
        "charts and pictures in a legacy workbook are not exposed by xlrd; "
        "save it as .xlsx to carry them"
    )
    return read


# --------------------------------------------------------------------------
# The converter fallback: a block stream, read by the same region logic.


def _sheet_bodies(document: dict, report: SheetReport) -> dict[int, list[str]]:
    """Each sheet's content, in the order the converter emitted it.

    The converter already parts a sheet into regions -- it emits one table per
    run of occupied rows -- so each of those is offered to the same renderer,
    and a one-cell "table" holding a title comes out as the line it is rather
    than a table with a header rule under it.
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


def unitize(blocks_path: Path, names: list[str]) -> tuple[list[Section], SheetReport]:
    """One section per sheet, from a converter's block stream.

    The fallback for formats no reader here opens: the block stream says
    which sheet each table belongs to, and the region logic does the rest.
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


# --------------------------------------------------------------------------
# Declared objects: what the workbook says hangs on its coordinate space.

_XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
_DML = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_CHART = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_ANCHOR_TAGS = tuple(
    f"{_XDR}{name}" for name in ("oneCellAnchor", "twoCellAnchor", "absoluteAnchor")
)


def _drawing_texts(element) -> str:
    parts = [t.text or "" for t in element.iter(f"{_DML}t")]
    return _RUN_OF_SPACES.sub(" ", "".join(parts)).strip()


def _chart_statement(archive: zipfile.ZipFile, part: str) -> tuple[str, list[str]]:
    """What a chart declares: its title, and the cells it plots.

    A chart is not an image. The file states which ranges each series draws
    from, so the visual-to-data edge is written down, not inferred.
    """
    try:
        root = ElementTree.fromstring(archive.read(part))
    except (KeyError, ElementTree.ParseError):
        return "", []
    title_el = root.find(f".//{_CHART}title")
    title = _drawing_texts(title_el) if title_el is not None else ""
    plots: list[str] = []
    for formula in root.iter(f"{_CHART}f"):
        if formula.text and formula.text not in plots:
            plots.append(formula.text)
    return title, plots


def _anchor_of(anchor_el) -> tuple[int | None, int | None, str | None]:
    from_el = anchor_el.find(f"{_XDR}from")
    if from_el is None:
        return None, None, None
    col_el = from_el.find(f"{_XDR}col")
    row_el = from_el.find(f"{_XDR}row")
    if col_el is None or row_el is None:
        return None, None, None
    row = int(row_el.text or 0) + 1
    col = int(col_el.text or 0) + 1
    return row, col, cell_ref(row, col)


def _stash_media(media: dict[str, bytes], name: str, data: bytes) -> str:
    """Keep the bytes under a stable name, renaming only a true collision."""
    if name in media and media[name] != data:
        stem, dot, suffix = name.rpartition(".")
        base = stem or name
        counter = 2
        candidate = f"{base}_{counter}{dot}{suffix}" if dot else f"{name}_{counter}"
        while candidate in media and media[candidate] != data:
            counter += 1
            candidate = f"{base}_{counter}{dot}{suffix}" if dot else f"{name}_{counter}"
        name = candidate
    media[name] = data
    return name


def _drawing_objects(
    archive: zipfile.ZipFile,
    part: str,
    sheet_index: int,
    sheet_name: str,
    media: dict[str, bytes],
) -> list[dict]:
    try:
        root = ElementTree.fromstring(archive.read(part))
    except (KeyError, ElementTree.ParseError):
        return []
    rels = _rels(archive, part)
    objects: list[dict] = []
    for anchor_el in root:
        if anchor_el.tag not in _ANCHOR_TAGS:
            continue
        row, col, ref = _anchor_of(anchor_el)
        record: dict = {
            "sheet": sheet_index,
            "sheet_name": sheet_name,
            "anchor_ref": ref,
            "anchor_row": row,
            "anchor_col": col,
        }
        pic = anchor_el.find(f".//{_XDR}pic")
        chart_el = anchor_el.find(f".//{_CHART}chart")
        if chart_el is not None:
            rid = chart_el.get(f"{_DOC_REL}id") or ""
            title, plots = _chart_statement(archive, rels.get(rid, ""))
            record.update(kind="chart", title=title, plots=plots)
            objects.append(record)
        elif pic is not None:
            blip = pic.find(f".//{_DML}blip")
            rid = blip.get(f"{_DOC_REL}embed") if blip is not None else None
            target = rels.get(rid or "")
            if not target:
                continue
            try:
                data = archive.read(target)
            except KeyError:
                continue
            name = _stash_media(media, target.rpartition("/")[2], data)
            record.update(kind="image", media=f"artifacts/media/{name}")
            objects.append(record)
        else:
            text = _drawing_texts(anchor_el)
            if text:
                record.update(kind="shape", text=text)
                objects.append(record)
    return objects


def _read_objects(
    archive: zipfile.ZipFile,
    grids: list[SheetGrid],
    drawing_rids: list[tuple[int, str, str | None]],
    read: WorkbookRead,
) -> None:
    for index, part, rid in drawing_rids:
        if not rid:
            continue
        target = _rels(archive, part).get(rid)
        if not target:
            continue
        name = (grids[index - 1].name or "").strip() or f"Sheet {index}"
        read.objects.extend(
            _drawing_objects(archive, target, index, name, read.media)
        )
    for record in read.objects:
        if record["kind"] == "chart":
            read.report.charts += 1
        elif record["kind"] == "image":
            read.report.images += 1
        else:
            read.report.shapes += 1


# --------------------------------------------------------------------------
# Lake writers for the evidence layers.


def write_cells(output_dir: Path, records: list[dict]) -> Path:
    """The address layer: every cell under its own reference."""
    bronze = Path(output_dir) / "bronze"
    bronze.mkdir(parents=True, exist_ok=True)
    path = bronze / "cells.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def write_objects(output_dir: Path, objects: list[dict]) -> Path:
    """What the workbook declares hangs on its grid, anchors included."""
    silver = Path(output_dir) / "silver"
    silver.mkdir(parents=True, exist_ok=True)
    path = silver / "objects.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in objects:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def write_media(output_dir: Path, media: dict[str, bytes]) -> Path:
    """The opaque payloads, carried as bytes; reading them is a VLM's job."""
    folder = Path(output_dir) / "artifacts" / "media"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in media.items():
        (folder / name).write_bytes(data)
    return folder
