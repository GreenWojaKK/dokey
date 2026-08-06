from __future__ import annotations

from pathlib import Path

from .models import SectionRange, TocEntry
from .names import ArtifactNamer


def build_ranges(
    entries: list[TocEntry],
    output_dir: Path,
    total_pdf_pages: int,
    pdf_page_offset: int,
    max_content_page: int | None,
    section_overlap: int | None = 0,
) -> list[SectionRange]:
    """Cut the document into per-section page ranges.

    ``section_overlap`` as an int is a global instruction: every boundary
    shares that many pages. ``None`` decides each boundary from the next
    section's own start page — a heading that opens a fresh page needs no
    shared page, one that begins mid-page leaves the previous section's tail
    there, so both keep it. A boundary that was never read stays shared.
    """
    rows = sorted(entries, key=lambda entry: entry.page)
    max_possible_content_page = total_pdf_pages - pdf_page_offset
    effective_max_content_page = (
        max_possible_content_page
        if max_content_page is None
        else min(max_content_page, max_possible_content_page)
    )

    parent_indexes: dict[str, int] = {}
    parent_item_counts: dict[str, int] = {}
    namer = ArtifactNamer()
    ranges: list[SectionRange] = []

    for index, entry in enumerate(rows):
        if entry.page > effective_max_content_page:
            continue

        parent = entry.parent or entry.title
        if parent not in parent_indexes:
            parent_indexes[parent] = len(parent_indexes) + 1
        parent_item_counts[parent] = parent_item_counts.get(parent, 0) + 1

        parent_index = parent_indexes[parent]
        parent_item_index = parent_item_counts[parent]

        next_entry = find_next_starting_entry(rows, index)
        next_start_page = None if next_entry is None else next_entry.page
        # The overlap extends a section's end into the next section's start
        # page(s). A boundary that falls mid-page truncates the earlier section
        # unless the shared page is kept by both; a clean break duplicates a
        # page for nothing.
        content_end_page = (
            effective_max_content_page
            if next_start_page is None
            else min(
                next_start_page - 1 + boundary_overlap(section_overlap, next_entry),
                effective_max_content_page,
            )
        )
        if content_end_page < entry.page:
            continue

        # PDF pages come from the entry's verified pin when one exists, else
        # from the printed page plus the constant offset. The end page derives
        # from the next section's PDF start (not the printed end plus offset)
        # so pinned entries with a drifting offset stay contiguous; with no
        # pins this reduces to the constant-offset arithmetic.
        pdf_cap = min(total_pdf_pages, effective_max_content_page + pdf_page_offset)
        pdf_start_page = resolve_pdf_start(entry, pdf_page_offset)
        next_pdf_entry = find_next_pdf_entry(rows, index, pdf_page_offset)
        pdf_end_page = (
            pdf_cap
            if next_pdf_entry is None
            else min(
                resolve_pdf_start(next_pdf_entry, pdf_page_offset)
                - 1
                + boundary_overlap(section_overlap, next_pdf_entry),
                pdf_cap,
            )
        )
        if pdf_end_page < pdf_start_page:
            continue
        page_count = pdf_end_page - pdf_start_page + 1
        # Page-independent name: the section's title, so re-ingesting the same
        # document (e.g. with a corrected offset) reuses the exact filename and
        # overwrites in place instead of leaving a differently-named duplicate.
        # Page ranges and ordinals live in the manifest fields, not in the name.
        parent_folder, filename = namer.name(
            title=entry.title, parent=parent, suffix=".pdf"
        )

        ranges.append(
            SectionRange(
                index=len(ranges) + 1,
                parent_index=parent_index,
                parent_item_index=parent_item_index,
                parent=parent,
                parent_folder=parent_folder,
                title=entry.title,
                content_start_page=entry.page,
                content_end_page=content_end_page,
                pdf_start_page=pdf_start_page,
                pdf_end_page=pdf_end_page,
                page_count=page_count,
                output_file=str(output_dir / "by_section" / parent_folder / filename),
            )
        )

    return ranges


def boundary_overlap(section_overlap: int | None, next_entry: TocEntry | None) -> int:
    """How many of the next section's start pages this section keeps.

    A global int is an instruction and applies to every boundary. Otherwise
    the next section's own start page decides: a heading that opened a fresh
    page leaves nothing of this section behind it (0), while a mid-page or
    unread break keeps the shared page in both sections (1).
    """
    if section_overlap is not None:
        return section_overlap
    if next_entry is not None and next_entry.clean_start:
        return 0
    return 1


def find_next_starting_entry(rows: list[TocEntry], index: int) -> TocEntry | None:
    current_page = rows[index].page
    for candidate in rows[index + 1 :]:
        if candidate.page > current_page:
            return candidate
    return None


def find_next_start_page(rows: list[TocEntry], index: int) -> int | None:
    candidate = find_next_starting_entry(rows, index)
    return None if candidate is None else candidate.page


def resolve_pdf_start(entry: TocEntry, pdf_page_offset: int) -> int:
    return entry.pdf_page if entry.pdf_page is not None else entry.page + pdf_page_offset


def find_next_pdf_entry(
    rows: list[TocEntry], index: int, pdf_page_offset: int
) -> TocEntry | None:
    current = resolve_pdf_start(rows[index], pdf_page_offset)
    for candidate in rows[index + 1 :]:
        if resolve_pdf_start(candidate, pdf_page_offset) > current:
            return candidate
    return None


def find_next_pdf_start(rows: list[TocEntry], index: int, pdf_page_offset: int) -> int | None:
    candidate = find_next_pdf_entry(rows, index, pdf_page_offset)
    return None if candidate is None else resolve_pdf_start(candidate, pdf_page_offset)
