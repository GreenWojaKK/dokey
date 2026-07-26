from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TocEntry:
    level: int
    title: str
    page: int
    parent: str | None = None
    # Physical PDF page this entry is known to start on, pinned by a
    # verification pass. When set it overrides page + offset, so a drifting
    # offset (plates, part dividers) cannot misplace the section.
    pdf_page: int | None = None


@dataclass(frozen=True)
class SectionRange:
    index: int
    parent_index: int
    parent_item_index: int
    parent: str
    parent_folder: str
    title: str
    content_start_page: int
    content_end_page: int
    pdf_start_page: int
    pdf_end_page: int
    page_count: int
    output_file: str
