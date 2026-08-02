"""The PDF's own bookmarks, and the question of whether to believe them.

An outline is the cheapest table of contents a PDF can offer, and where it is
real it is also the best: its destinations are physical pages, so no offset has
to be estimated and no smoke test has to verify it. But a bookmark is metadata,
and metadata is not always about the document. Files come out of authoring
tools carrying a single bookmark left over from editing -- one measured case is
a 210-page report whose entire outline is ``빈 페이지`` ("blank page") pointing
at page 2, which is neither a heading nor a division but a note someone made to
themselves. Read as a table of contents it yields one section holding the whole
document.

So an outline is asked to show that it divides the document before it is used.
The test is coverage, not vocabulary: no list of titles can be checked against
the words a document happens to use, but any table of contents can be checked
against the thing it claims to describe.
"""
from __future__ import annotations

from typing import Any

from .models import TocEntry
from .toc import fill_parents, mark_leaf_entries

# An outline whose widest entry governs more of the document than this is not
# dividing it. Half is deliberately loose: the point is to catch an outline
# that leaves the document in one piece, not to judge how evenly a real one
# splits.
MAX_ENTRY_SHARE = 0.5


def largest_share(
    entries: list[TocEntry], page_count: int, *, count_tail: bool = True
) -> float:
    """The fraction of the document its single widest entry governs.

    Pages before the first entry are not counted against it: an outline that
    starts at chapter 1 and leaves the front matter alone is doing its job.

    ``count_tail`` measures the last entry as running to the end of the
    document. That holds for entries that carry physical pages, and it is what
    catches the single stray bookmark. It does not hold for a printed contents
    page, whose numbers are the document's own folios: those do not say where the
    document ends, so the tail there would measure the page offset rather than
    the entry.
    """
    if page_count <= 0 or not entries:
        return 1.0
    starts = sorted({entry.page for entry in entries})
    spans = [later - earlier for earlier, later in zip(starts, starts[1:])]
    if count_tail:
        spans.append(page_count + 1 - starts[-1])
    if not spans:
        return 1.0
    return max(spans) / page_count


def divides_document(
    entries: list[TocEntry], page_count: int, *, count_tail: bool = True
) -> bool:
    """Whether a table of contents actually partitions the document."""
    return largest_share(entries, page_count, count_tail=count_tail) <= MAX_ENTRY_SHARE


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
