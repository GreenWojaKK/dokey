from __future__ import annotations

from typing import Any

from .models import TocEntry
from .toc import fill_parents, mark_leaf_entries


def read_outline_toc(reader: Any, max_level: int = 1) -> list[TocEntry]:
    entries: list[TocEntry] = []
    collect_outline_entries(reader, reader.outline, entries, level=0, max_level=max_level)
    if not entries:
        raise ValueError("No PDF outline entries found.")
    return mark_leaf_entries(fill_parents(entries))


def collect_outline_entries(
    reader: Any,
    items: list[Any],
    entries: list[TocEntry],
    level: int,
    max_level: int,
) -> None:
    for item in items:
        if isinstance(item, list):
            collect_outline_entries(reader, item, entries, level + 1, max_level)
            continue

        if level <= max_level:
            title = getattr(item, "title", str(item)).strip()
            page = reader.get_destination_page_number(item) + 1
            entries.append(TocEntry(level=level, title=title, page=page))
