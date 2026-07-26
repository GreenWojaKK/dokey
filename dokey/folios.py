"""Recover true printed page numbers (folios) from a book's own text.

When a lake is built from a PDF outline, the manifest's ``pdf_start_page`` is the
physical PDF page, but the book's printed folio differs by an offset that drifts
across the book (front matter, dropped blank leaves, part dividers). If the PDF
has a text-extractable Table of Contents — most born-digital books do — the
printed page of every numbered section is right there in the text. Joining that
against the outline-derived manifest (by section number) yields the exact
printed page per section, with no OCR and no constant-offset assumption.

This is the default, dependency-light path (pypdf only). For scanned PDFs whose
TOC is not extractable, fall back to the image OCR pipeline in ``dokey.ocr``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .pdf import open_reader

_ENTRY = re.compile(r"^(?P<title>.+?),\s*(?P<page>\d{1,4})\s*$")
# Section keys: "1", "1.3", "6.10", or an appendix form like "A.7".
_NUMKEY = re.compile(r"^([A-Za-z]?\.?\d+(?:\.\d+)*)")

_FOLIO_KEYS = ("printed_start_page", "printed_end_page", "folio_source")


@dataclass(frozen=True)
class FolioStats:
    toc_pages: tuple[int, ...]
    toc_entries: int
    matched: int
    derived: int
    front_matter: int
    total: int
    offset_min: int | None
    offset_max: int | None


def section_key(title: str) -> str | None:
    match = _NUMKEY.match(title.strip())
    return match.group(1) if match else None


def _entry_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if _ENTRY.match(line.strip()))


def find_toc_pages(
    reader, max_scan: int = 80, min_dense: int = 5, min_continue: int = 3
) -> list[int]:
    """Return the 1-indexed PDF pages of the first contiguous run of
    Table-of-Contents-like (entry-dense) pages near the front."""
    pages: list[int] = []
    started = False
    for index, page in enumerate(reader.pages[:max_scan], start=1):
        count = _entry_count(page.extract_text() or "")
        if not started and count >= min_dense:
            started = True
        if started:
            if count >= min_continue:
                pages.append(index)
            else:
                break
    return pages


def parse_toc_number_map(reader, toc_pages: list[int]) -> dict[str, int]:
    """Map each numbered section key to its printed page from the TOC text.
    Only numbered entries are used; repeated generic titles (e.g. "About the
    Author") are intentionally ignored because they are not uniquely resolvable."""
    number_map: dict[str, int] = {}
    for index in toc_pages:
        text = reader.pages[index - 1].extract_text() or ""
        for line in text.splitlines():
            match = _ENTRY.match(line.strip())
            if not match:
                continue
            key = section_key(match.group("title"))
            if key is not None:
                number_map.setdefault(key, int(match.group("page")))
    return number_map


def build_toc_map(pdf_path) -> tuple[dict[str, int], list[int]]:
    reader = open_reader(pdf_path)
    toc_pages = find_toc_pages(reader)
    return parse_toc_number_map(reader, toc_pages), toc_pages


def apply_folios(rows: list[dict], toc_map: dict[str, int]) -> FolioStats:
    """Add printed_start_page / printed_end_page / folio_source to each manifest
    row in place. Numbered sections are matched directly; other body sections are
    filled from the nearest matched section's (locally constant) offset; front
    matter before the first body page is left unresolved (None)."""
    for row in rows:
        for key in _FOLIO_KEYS:
            row.pop(key, None)

    matched: list[dict] = []
    for row in rows:
        key = section_key(row["title"])
        if key is not None and key in toc_map:
            printed = toc_map[key]
            row["printed_start_page"] = printed
            row["folio_source"] = "toc"
            row["_offset"] = int(row["pdf_start_page"]) - printed
            matched.append(row)
        else:
            row["printed_start_page"] = None
            row["folio_source"] = None

    if not matched:
        for row in rows:
            row.setdefault("printed_start_page", None)
            row["printed_end_page"] = None
            row["folio_source"] = "unresolved"
        return FolioStats(
            (), len(toc_map), 0, 0, len(rows), len(rows), None, None
        )

    matched.sort(key=lambda r: int(r["pdf_start_page"]))
    first_body_pdf = int(matched[0]["pdf_start_page"])

    def nearest_offset(pdf_start: int) -> int:
        return min(
            matched, key=lambda r: abs(int(r["pdf_start_page"]) - pdf_start)
        )["_offset"]

    derived = front_matter = 0
    for row in rows:
        if row["printed_start_page"] is None:
            if int(row["pdf_start_page"]) < first_body_pdf:
                row["folio_source"] = "front-matter"
                front_matter += 1
            else:
                offset = nearest_offset(int(row["pdf_start_page"]))
                row["printed_start_page"] = int(row["pdf_start_page"]) - offset
                row["folio_source"] = "derived"
                derived += 1

    for row in rows:
        row.pop("_offset", None)
        if row["printed_start_page"] is None:
            row["printed_end_page"] = None
        else:
            span = int(row["pdf_end_page"]) - int(row["pdf_start_page"])
            row["printed_end_page"] = row["printed_start_page"] + span

    offsets = [
        int(r["pdf_start_page"]) - r["printed_start_page"]
        for r in rows
        if r["printed_start_page"] is not None
    ]
    return FolioStats(
        toc_pages=(),
        toc_entries=len(toc_map),
        matched=len(matched),
        derived=derived,
        front_matter=front_matter,
        total=len(rows),
        offset_min=min(offsets) if offsets else None,
        offset_max=max(offsets) if offsets else None,
    )
