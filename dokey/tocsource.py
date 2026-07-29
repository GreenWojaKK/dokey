"""Decide where a PDF's table of contents comes from, without ingesting it.

The cascade -- embedded outline, printed contents page, the document's own
numbered headings, OCR as a last resort -- used to live inside the ingest,
which meant the only way to learn what dokey would do with a document was to
let it build a whole lake. That is fine for a script and wrong for a person:
how deep to split changes what the sections are, and a reader should be able to
look before committing. So the cascade lives here, and both the ingest and the
app's preview call it. A preview running different code would be a different
answer.

Order is by cost and by how much each source actually knows:

*An embedded outline* states its own destinations, so its pages are physical
and need no offset. It is used only if it divides the document -- a single
bookmark covering the whole file is metadata about the file, not a table of
contents -- and when it does not, a printed contents page with more entries
takes over.

*A printed contents page* states the book's own folios, which is not the same
as a PDF page: that path needs the offset calibrated and every start verified.

*The body's own numbered headings* cost a text read and carry physical pages.
This is what answers a contents page printed without page numbers, which the
title-and-page reader cannot see as a contents page at all.

*OCR* renders each page of the front matter through a model. Minutes of work,
so it is asked only when the text layer had nothing -- and a preview refuses it
outright by passing no client.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import bodytoc
from . import outline as outlinelib
from .models import TocEntry
from .outline import read_outline_toc
from .tocpage import read_page_toc


@dataclass(frozen=True)
class TocResult:
    entries: list[TocEntry]
    source: str  # outline | printed | derived | ocr | none
    physical_pages: bool  # pages are PDF pages already, so no offset applies
    note: str = ""  # why the obvious source was passed over, when it was

    @property
    def found(self) -> bool:
        return bool(self.entries)

    @property
    def label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)


SOURCE_LABELS = {
    "outline": "the embedded PDF outline",
    "printed": "the printed contents page(s)",
    "derived": "the document's own numbered headings",
    "ocr": "the printed contents page(s), read by OCR",
    "none": "nothing readable",
}


def _printed(pdf_path: Path, toc_pages, ocr_client=None) -> list[TocEntry]:
    try:
        return read_page_toc(pdf_path, toc_pages=toc_pages, ocr_client=ocr_client)
    except ValueError:
        return []


def resolve(
    reader,
    pdf_path: Path,
    *,
    max_level: int = 1,
    profile: str | None = "auto",
    toc_pages: list[int] | None = None,
    ocr_client=None,
    allow_printed: bool = True,
) -> TocResult:
    """Find the document's table of contents, cheapest source first."""
    pages = len(reader.pages)
    try:
        outline = read_outline_toc(reader, max_level=max_level)
    except ValueError:
        outline = []

    if outline:
        if outlinelib.divides_document(outline, pages):
            return TocResult(outline, "outline", True)
        share = outlinelib.largest_share(outline, pages)
        counted = (
            "entry leaves" if len(outline) == 1 else f"{len(outline)} entries leave"
        )
        note = (
            f"the embedded outline's {counted} {share:.0%} of the document under "
            "one heading, which is not a division of it"
        )
        # Give up the outline only for something demonstrably better: more
        # entries, and a division by the same test the outline just failed.
        printed = _printed(pdf_path, toc_pages) if allow_printed else []
        if (
            printed
            and len(printed) > len(outline)
            and outlinelib.divides_document(printed, pages, count_tail=False)
        ):
            return TocResult(printed, "printed", False, note)
        return TocResult(
            outline,
            "outline",
            True,
            note + "; nothing better on the printed pages",
        )

    if allow_printed:
        printed = _printed(pdf_path, toc_pages)
        if printed:
            return TocResult(printed, "printed", False)

    derived = bodytoc.derive_toc(
        [page.extract_text() or "" for page in reader.pages],
        profile=profile,
        max_level=max_level,
    )
    if derived:
        return TocResult(
            derived,
            "derived",
            True,
            "no contents page with page numbers",
        )

    if ocr_client is not None and allow_printed:
        read = _printed(pdf_path, toc_pages, ocr_client=ocr_client)
        if read:
            return TocResult(read, "ocr", False)

    return TocResult([], "none", False)
