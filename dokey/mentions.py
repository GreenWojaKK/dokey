"""Where an identifier occurs, and under which address.

A plant's documents are held together by tags. ``T-101`` is a tank, and the
sentence that says it was damaged, the drawing that shows it, the quotation that
prices its repair and the sheet that lists its material are four documents that
share nothing else -- not a title, not a heading, not a vocabulary. The tag is
the join.

So dokey records where the tag occurs, with the address of the passage it occurs
in, and stops there. It does not decide that T-101 *is* a tank, that the tank was
damaged, or that this document is about it; those are readings of the words, and
the line dokey draws everywhere else puts them on the consumer's side. What a
consumer gets is the anchor: every occurrence, addressed the way a clause is
addressed, so a model built on top of it can be checked against the text.

Recognition is by form alone -- a short letter code, a hyphen, a number, an
optional item letter -- which is the shape ISA-5.1 style tag numbers take and
which no vocabulary is needed to see. The same shape is worn by things that are
not tags, so two guards apply: a third dashed part disqualifies (``C-79-2015``
is a document number), and so does an immediately preceding letter or digit.
What survives is counted, not interpreted.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# The same shape docname.py claims in a filename, applied to running text. A
# following year -- "M-181 - 2014", spaced or not -- makes it a document
# number rather than a tag, which is what a standard's own running header
# carries on every page.
TAG = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]{1,4})-(\d{2,5})([A-Z])?(?![A-Za-z0-9])(?!\s*-\s*\d)"
)
# How much of the surrounding line to keep, so an occurrence can be read
# without opening the section.
CONTEXT = 60


@dataclass(frozen=True)
class Mention:
    tag: str
    section_index: int
    section: str
    page: int
    char_start: int
    context: str
    named: bool = False

    def as_dict(self) -> dict:
        return {
            "tag": self.tag,
            "section_index": self.section_index,
            "section": self.section,
            "page": self.page,
            "char_start": self.char_start,
            "context": self.context,
            # The document's own filename states this identifier too. Nothing
            # here says what it is, but a document named for T-101 that also
            # mentions T-101 is corroborating itself, and a consumer sorting
            # equipment from alloy grades will want that first.
            "in_document_name": self.named,
        }


@dataclass
class MentionReport:
    mentions: int = 0
    tags: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.mentions:
            return "no tag-shaped identifiers"
        shown = ", ".join(self.tags[:6])
        if len(self.tags) > 6:
            shown += ", ..."
        return f"{self.mentions} mention(s) of {len(self.tags)} identifier(s): {shown}"

    def as_dict(self) -> dict:
        return {"mentions": self.mentions, "tags": list(self.tags)}


def _context(body: str, start: int, end: int) -> str:
    left = max(0, start - CONTEXT // 2)
    right = min(len(body), end + CONTEXT // 2)
    snippet = body[left:right].replace("\n", " ").strip()
    return re.sub(r"\s{2,}", " ", snippet)


def find(
    sections: list, document_stem: str = "", named: tuple[str, ...] = ()
) -> tuple[list[Mention], MentionReport]:
    """Every tag-shaped identifier in the sections, with its address.

    ``document_stem`` excludes the document's own number: a standard called
    ``M-181-2014`` prints ``M-181`` in its running header on every page, and
    counting a document's references to itself as mentions of equipment was
    the loudest false positive in the measured corpus.

    ``named`` is what the filename stated (see ``docname``). A tag the
    document is named for, occurring in the document, is a document
    corroborating itself -- which is worth marking and not worth interpreting.
    """
    mentions: list[Mention] = []
    seen: list[str] = []
    for index, section in enumerate(sections, start=1):
        body = section.body or ""
        for match in TAG.finditer(body):
            tag = match.group(0)
            if document_stem and tag not in named and tag in document_stem:
                continue
            if tag not in seen:
                seen.append(tag)
            mentions.append(
                Mention(
                    tag=tag,
                    section_index=index,
                    section=section.title,
                    page=section.order,
                    char_start=match.start(),
                    context=_context(body, match.start(), match.end()),
                    named=tag in named,
                )
            )
    return mentions, MentionReport(mentions=len(mentions), tags=seen)


def write_mentions(output_dir: Path, mentions: list[Mention]) -> Path:
    silver = Path(output_dir) / "silver"
    silver.mkdir(parents=True, exist_ok=True)
    path = silver / "mentions.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for mention in mentions:
            handle.write(json.dumps(mention.as_dict(), ensure_ascii=False) + "\n")
    return path
