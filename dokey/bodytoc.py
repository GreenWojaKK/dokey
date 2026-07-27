"""Derive a table of contents from a PDF's own numbered headings.

dokey's TOC cascade asks the document three questions in turn: does it carry an
embedded outline, does it print a contents page, and -- failing both -- can OCR
find one. All three can come up empty on a document that is nonetheless
perfectly structured, and one shape makes that happen often: **a contents page
that lists titles without page numbers.**

    목차
    1. 적용범위
    2. 목적
    3. 용어의 정의

A reader has no trouble with this; the printed-contents reader does, because it
looks for title-and-page pairs and there are no pages to pair. The document is
not short of structure, only of a page column -- its clauses are numbered, and
every one of them appears in the body where it starts.

So this module reads the headings out of the body instead, the same way the
Markdown path reads a render: find the numbering, let the document's own ladder
say which rung each series is on (:mod:`dokey.ladder`), and keep the rungs at
the depth asked for. The page is simply the page the heading was found on, so
these entries are physical PDF pages like an outline's -- no offset applies.

Two traps are worth naming, because both are in real documents:

*The contents page lists the same headings.* Its entries would win by coming
first, and every section would start on page 1. A listing is recognizable
without knowing it is one: its entries sit on consecutive lines, while a body's
headings have paragraphs between them.

*A numbered line is not always a heading.* ``1. 적용범위`` heads a clause;
``1. 이 규칙은 …로 한다.`` is a list item that happens to open a sentence. A
heading is short and does not end like a sentence -- the same test the Markdown
path uses -- and clause numbers ascend, so a candidate that breaks the sequence
is not the clause it claims to be.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import ladder as ladderlib
from . import profiles as profileslib
from .models import TocEntry

# A heading is a title, not a sentence: beyond this it is prose that happens to
# start with a number.
MAX_HEADING_CHARS = 60
# Entries this close together are a listing, not a body: a contents page puts
# them on consecutive lines, a document puts paragraphs between them.
LISTING_MAX_LINE_GAP = 2
LISTING_MIN_ENTRIES = 3

_SENTENCE_END = re.compile(r"[.!?…]\s*$")


@dataclass(frozen=True)
class _Candidate:
    page: int  # 1-based PDF page
    line: int  # line index within that page
    title: str
    numbering: object


def _candidates(pages: list[str], profile) -> list[_Candidate]:
    found: list[_Candidate] = []
    for number, text in enumerate(pages, start=1):
        for index, raw in enumerate(text.splitlines()):
            line = raw.strip()
            if not line or len(line) > MAX_HEADING_CHARS:
                continue
            numbering = profile.numbering(line)
            if numbering is None or not numbering.ordinal:
                continue
            if _SENTENCE_END.search(line) or profile.is_sentence_tail(line):
                continue
            found.append(_Candidate(number, index, line, numbering))
    return found


def _drop_listings(found: list[_Candidate]) -> list[_Candidate]:
    """Remove runs that read as a contents listing rather than a body."""
    keep = [True] * len(found)
    start = 0
    while start < len(found):
        end = start
        while (
            end + 1 < len(found)
            and found[end + 1].page == found[end].page
            and found[end + 1].line - found[end].line <= LISTING_MAX_LINE_GAP
        ):
            end += 1
        if end - start + 1 >= LISTING_MIN_ENTRIES:
            for index in range(start, end + 1):
                keep[index] = False
        start = end + 1
    return [candidate for candidate, wanted in zip(found, keep) if wanted]


def derive_toc(
    pages: list[str], *, profile: str | None = "auto", max_level: int = 1
) -> list[TocEntry]:
    """Read a table of contents off the body text of ``pages``.

    ``pages`` is the text of each PDF page in order. Entries come back with the
    physical page they were found on, so they need no offset -- like an
    outline's, and unlike a printed contents page's.
    """
    active = profileslib.resolve(profile, "\n".join(pages[:20]))
    found = _candidates(pages, active)
    if not found:
        return []

    # The ladder is induced first, and the rungs it rejects are removed before
    # anything is read as a listing. Otherwise an unresolved auto-numbered list
    # -- every item rendered ``0.`` -- sits between two clause headings and
    # welds them into a run that looks like a contents page, taking the real
    # clauses down with it (measured: clauses 5 and 6 of a 10-clause rule).
    ladder = ladderlib.induce_from_lines(
        [candidate.title for candidate in found], active
    )
    wanted = [
        candidate
        for candidate in found
        if ladder.kind_of(candidate.numbering) != ladderlib.UNORDERED
        and ladder.depth(candidate.numbering) <= max_level
    ]

    entries: list[TocEntry] = []
    expected: dict[str, int] = {}
    for candidate in _drop_listings(wanted):
        numbering = candidate.numbering
        kind = ladder.kind_of(numbering)
        depth = ladder.depth(numbering)
        ordinal = numbering.ordinal
        if not ordinal:
            continue
        # Clause numbers ascend. A line that claims a number the document has
        # already passed is a quotation or a list item wearing the same shape.
        key = f"{kind}:{len(ordinal)}:{ordinal[:-1]}"
        if ordinal[-1] <= expected.get(key, 0):
            continue
        expected[key] = ordinal[-1]
        entries.append(
            TocEntry(level=depth, title=candidate.title, page=candidate.page)
        )
    return entries
