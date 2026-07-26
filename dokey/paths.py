"""Cut a section into addressed items along the document's own numbering ladder.

A section is the unit a reader cites, but it is not the unit a document
*addresses*. Korean technical standards address a passage by a ladder of
numbering series::

    4.            절
    4.1           소절
    (1)           항
    (가)          목
    ①             세목
    ㉮            세세목

and the passage a rule lives in is named by its whole path -- ``4.1 (1) (가)``
-- not by the section alone. Anything reading these documents for their
content (a definition harvest, a norm extractor, a knowledge graph) anchors on
that path, because the enumerator is the one deterministic boundary in the
text: it marks where one item starts and the previous one ends, regardless of
how the sentence inside is worded.

Two properties of the ladder decide the implementation:

*The rungs are ranges, not partitions.* Text under ``(가)`` belongs to ``(가)``
and also, transitively, to the ``(1)`` and ``4.1`` enclosing it. So an
arbitrary position resolves to the deepest item covering it, and each item's
own text runs until the next token at its rung or shallower.

*A document may skip a rung.* ``4.1`` straight to ``(가)``, with no ``(1)``,
is common and is not an error. The missing rung is counted, never invented:
inventing it would put text at an address the document does not use, and a
citation to an address that does not exist is worse than a coarse one.

Series that sit off the canonical ladder (``1)`` where the document elsewhere
writes ``(1)``, ``가.`` for ``(가)``) are kept and marked ``irregular`` rather
than dropped -- a document's own inconsistency must not cost it items.

The ladder itself is language knowledge and lives in :mod:`dokey.profiles`;
this module only walks it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import profiles as profileslib

# A folded subheading still carries its marker in the body ("## 5.1 …"); the
# heading is part of the address ladder, so the marker is stripped before the
# line is read for numbering.
_LEADING_HASHES = re.compile(r"^\s{0,3}#{1,6}\s+")
_TABLE_ROW = re.compile(r"^ {0,3}\|")


@dataclass(frozen=True)
class Item:
    """One addressed passage: its path, its own text, and where it sits.

    ``path`` is the full address from the section's own number down to this
    item's token. ``char_start``/``char_end`` bound the item's whole *range*,
    children included, because the rungs are ranges: a position inside ``(가)``
    is also inside the ``(1)`` above it. ``text`` is the item's own words --
    everything before its first child -- and ``char_own_end`` bounds them, so a
    consumer can check the words really sit at the address they claim
    (``body[char_start:char_own_end].strip() == text``).
    """

    path: tuple[str, ...]
    label: str
    depth: int
    text: str
    char_start: int
    char_end: int
    char_own_end: int
    irregular: bool = False
    skipped: int = 0  # rungs the document jumped over to reach this one

    @property
    def address(self) -> str:
        return " ".join(self.path)


@dataclass
class SegmentReport:
    items: int = 0
    irregular: int = 0
    skipped_rungs: int = 0
    unaddressed_chars: int = 0  # text before the first enumerator
    series: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "items": self.items,
            "irregular": self.irregular,
            "skipped_rungs": self.skipped_rungs,
            "unaddressed_chars": self.unaddressed_chars,
            "series": dict(sorted(self.series.items())),
        }


@dataclass
class _Open:
    label: str
    depth: int
    start: int  # char offset in the body where this item's range begins
    own_end: int | None  # where its own words stop and its first child begins
    irregular: bool
    skipped: int


def segment(
    body: str,
    *,
    root: str | None = None,
    profile=None,
    report: SegmentReport | None = None,
) -> list[Item]:
    """Cut one section body into addressed items.

    ``root`` is the section's own numbering token (``4.`` for ``4. 작업 시작 전
    유의사항``), which becomes the first rung of every path. Text before the
    first enumerator belongs to the section itself and is reported, not
    silently dropped: an introductory paragraph under a clause is content the
    clause carries directly.
    """
    active = profile or profileslib.NEUTRAL
    tally = report if report is not None else SegmentReport()
    lines = body.splitlines(keepends=True)
    stack: list[_Open] = []
    items: list[Item] = []
    offset = 0
    preamble = 0

    def close(down_to: int, end: int) -> None:
        while stack and stack[-1].depth >= down_to:
            open_item = stack.pop()
            own_end = open_item.own_end if open_item.own_end is not None else end
            prefix = [] if root is None else [root]
            items.append(
                Item(
                    path=tuple(
                        [*prefix, *(part.label for part in stack), open_item.label]
                    ),
                    label=open_item.label,
                    depth=open_item.depth,
                    text=body[open_item.start : own_end].strip(),
                    char_start=open_item.start,
                    char_end=end,
                    char_own_end=own_end,
                    irregular=open_item.irregular,
                    skipped=open_item.skipped,
                )
            )

    for line in lines:
        stripped = _LEADING_HASHES.sub("", line)
        numbering = None
        if not _TABLE_ROW.match(line):
            numbering = active.numbering(stripped)
        if numbering is None:
            if not stack:
                preamble += len(line)
            offset += len(line)
            continue
        depth = numbering.depth
        close(depth, offset)
        # The section's own number occupies the first rung, so an item one rung
        # below it (4. -> 4.1) has skipped nothing.
        held = stack[-1].depth if stack else (1 if root is not None else 0)
        skipped = max(0, depth - held - 1)
        if skipped:
            tally.skipped_rungs += skipped
        if stack and stack[-1].own_end is None:
            stack[-1].own_end = offset
        stack.append(
            _Open(
                label=numbering.label.strip(),
                depth=depth,
                start=offset,
                own_end=None,
                irregular=numbering.irregular,
                skipped=skipped,
            )
        )
        tally.items += 1
        tally.series[numbering.kind] = tally.series.get(numbering.kind, 0) + 1
        if numbering.irregular:
            tally.irregular += 1
        offset += len(line)

    close(0, len(body))
    tally.unaddressed_chars += preamble
    items.sort(key=lambda item: (item.char_start, item.depth))
    return items


def segment_sections(sections, *, profile=None, report: SegmentReport | None = None):
    """Address every section of a document, yielding ``(section, items)``.

    The section's own numbering becomes the root rung, so an item's address is
    readable straight through: ``4. (1) (가)``. A section whose title carries no
    number (front matter, an appendix label) addresses its items from the
    enumerators alone.
    """
    active = profile or profileslib.NEUTRAL
    tally = report if report is not None else SegmentReport()
    for section in sections:
        numbering = active.numbering(section.title)
        root = numbering.label.strip() if numbering is not None else None
        yield section, segment(
            section.body, root=root, profile=active, report=tally
        )


def write_items_jsonl(rows: list[dict], output_dir: Path) -> Path:
    """Persist the addressed items beside the sections they came from."""
    import json

    path = output_dir / "silver" / "items.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path
