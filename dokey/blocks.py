"""Take pages from the source a render came from, instead of inventing them.

A Markdown render carries no page numbers, so the flow-document path gives each
section a synthetic one -- its own ordinal. That is honest as far as it goes,
but it reads as a claim that every section is one page long, and a document of
fifteen pages comes out claiming thirteen sections of one page each. The pages
are not gone, though: the converter had them and wrote them down. Docling's
block stream carries ``prov[].page_no`` for every block it emitted, and it sits
beside the render it produced -- ``M-165-2013.json`` next to ``M-165-2013.md``.

So when the blocks are there, the pages come from them:

*Sections are located by their titles, in order.* The render and the block
stream are the same document seen twice, in the same sequence, so the search
for each section's opening block starts where the previous one ended. A title
dokey repaired (``1. 목`` rejoined to ``1. 목적``) still matches, because a
block matching the start of a title is as good as one matching all of it.

*A section that cannot be located keeps the page of the one before it* and is
counted, not silently placed. Interpolating is a guess and is reported as one.

*Page text comes from the body layer.* The blocks say which of them are
furniture, so the page text a search index is built from can exclude the
running headers without the guesswork the Markdown path has to do.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_WS = re.compile(r"\s+")
# A title has to overlap a block by this much before the two are called the
# same place; below it, a short heading would match any block that starts with
# the same word.
MIN_TITLE_OVERLAP = 4


@dataclass(frozen=True)
class Block:
    page: int
    label: str
    layer: str
    text: str

    @property
    def is_body(self) -> bool:
        return self.layer != "furniture"


@dataclass
class PageReport:
    located: int = 0
    interpolated: int = 0
    pages: int = 0
    blocks: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sections_located": self.located,
            "sections_interpolated": self.interpolated,
            "source_pages": self.pages,
            "source_blocks": self.blocks,
            "notes": list(self.notes),
        }


def _flat(text: str) -> str:
    return _WS.sub("", text)


def read_blocks(path: Path) -> list[Block]:
    """Read a DoclingDocument's text blocks in reading order.

    ``orig`` is preferred over ``text``: the renderer strips an item's own
    marker into a separate field and re-numbers what is left, so ``text`` can
    be missing the ``(1)`` the document actually printed.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    blocks: list[Block] = []
    for item in document.get("texts", []):
        prov = item.get("prov") or []
        page = prov[0].get("page_no") if prov else None
        if page is None:
            continue
        content = item.get("orig") or item.get("text") or ""
        if not content.strip():
            continue
        layer = str(item.get("content_layer", "body")).lower()
        blocks.append(
            Block(
                page=int(page),
                label=str(item.get("label", "text")),
                layer="furniture" if "furniture" in layer else "body",
                text=content,
            )
        )
    return blocks


def find_source_blocks(render: Path) -> Path | None:
    """The block stream beside a render, if the converter left one there."""
    candidate = render.with_suffix(".json")
    return candidate if candidate.is_file() else None


def running_marks(blocks: list[Block]) -> set[str]:
    """Blocks that recur across the document like a page mark.

    The converter labels furniture, but not consistently -- the same running
    header is furniture in one document and body text in the next -- so the
    label is taken as a hint and the document is asked directly. A short block
    that appears on three pages or more, and reaches across the document rather
    than sitting inside one passage, is a mark. It is the same test the
    Markdown path uses, with the advantage that here "reach" is measured in
    pages rather than lines.
    """
    seen: dict[str, set[int]] = {}
    for block in blocks:
        text = block.text.strip()
        if len(text) > 40:
            continue
        seen.setdefault(_flat(text), set()).add(block.page)
    last = max((block.page for block in blocks), default=1)
    return {
        key
        for key, pages in seen.items()
        if len(pages) >= 3 and (max(pages) - min(pages) + 1) / max(1, last) >= 0.4
    }


def page_texts(blocks: list[Block]) -> list[dict]:
    """The text of each page, page furniture excluded, for the search index."""
    marks = running_marks(blocks)
    by_page: dict[int, list[str]] = {}
    for block in blocks:
        if not block.is_body or _flat(block.text) in marks:
            continue
        by_page.setdefault(block.page, []).append(block.text)
    return [
        {"page": page, "text": "\n".join(lines)}
        for page, lines in sorted(by_page.items())
    ]


def locate_sections(
    sections, blocks: list[Block], report: PageReport | None = None
) -> list[tuple[int, int]]:
    """Give each section the page range it occupies in the source document."""
    tally = report if report is not None else PageReport()
    tally.blocks = len(blocks)
    tally.pages = max((block.page for block in blocks), default=0)
    if not blocks or not sections:
        return [(index + 1, index + 1) for index, _ in enumerate(sections)]

    starts: list[int | None] = []
    cursor = 0
    for section in sections:
        wanted = _flat(section.title)
        found = None
        if len(wanted) >= MIN_TITLE_OVERLAP:
            for position in range(cursor, len(blocks)):
                candidate = _flat(blocks[position].text)
                if candidate.startswith(wanted[:MIN_TITLE_OVERLAP]) and (
                    candidate.startswith(wanted) or wanted.startswith(candidate)
                ):
                    found = position
                    break
        if found is None:
            starts.append(None)
            tally.interpolated += 1
        else:
            starts.append(found)
            cursor = found + 1
            tally.located += 1

    # A section nobody could find sits where the last located one left off.
    last = 0
    for index, start in enumerate(starts):
        if start is None:
            starts[index] = last
        else:
            last = starts[index]

    # A section ends at its last block of content -- not at whatever block
    # precedes the next section, which is often the running header sitting at
    # the top of the following page and would push the range a page too far.
    marks = running_marks(blocks)
    ranges: list[tuple[int, int]] = []
    for index, start in enumerate(starts):
        first_page = blocks[start].page
        following = starts[index + 1] if index + 1 < len(starts) else len(blocks)
        content = [
            block.page
            for block in blocks[start:following]
            if block.is_body and _flat(block.text) not in marks
        ]
        last_page = max(content) if content else first_page
        ranges.append((first_page, max(first_page, last_page)))
    if tally.interpolated:
        tally.notes.append(
            f"{tally.interpolated} section(s) could not be located in the "
            "block stream and carry the page of the section before them"
        )
    return ranges
