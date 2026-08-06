"""Unitize a Markdown document into sections by its heading hierarchy.

This is dokey's flow-document seam, format-neutral: it turns Markdown -- from
whatever upstream produced it (an ``hwp2md`` conversion, a Docling/Marker PDF
render, or a Markdown file a user simply hands in) -- into the same section
manifest the PDF path produces. A flow document has no intrinsic pages, so the
unit is the *heading*, not the page: each section is given one synthetic page
(its sequence number) so the manifest, index, and ``page BETWEEN pdf_start AND
pdf_end`` search join all work unchanged.

Keeping this apart from ``hwp.py`` (which only shells out to a converter) lets
Markdown be a first-class dokey input in its own right: ``dokey auto doc.md``.

Rendered Markdown, though, is a *lossy* view of a laid-out document, and a
naive "one section per ``#`` line" reading of it produces sections that are not
in the document. Three losses matter, all measured on a corpus of 866 Korean
technical standards rendered by Docling (866 files, 33,269 headings):

*Hierarchy is flattened.* 852 of the 866 files use exactly one heading level
for every heading, ``##``, from the document title down to clause ``11.14``.
The nesting is carried by the *numbering* (``5.`` encloses ``5.1``), not by the
hash count, so this module derives levels from numbering whenever the file's
own levels are uniform, and applies the same depth cap the PDF outline path
uses (``--outline-max-level``, default 1).

*Page furniture survives as body text.* A running header is text on the page
like any other; the renderer keeps it, and it lands mid-paragraph where the
page broke. ``KOSHA GUIDE`` recurs as a heading 1,102 times across 777 files,
and a document code arrives split across five lines (``D``/``-``/``C``/``-``/
``10 - 2026``). Repetition within one document is the signal -- not vocabulary,
which was measured not to converge -- so a short line that recurs three times
or more, carries no sentence, and is not numbered is classified as a running
mark and dropped, with the count reported rather than silently absorbed.

*Prose fragments are promoted to headings.* When a page break splits a
sentence, the tail can arrive as its own block labelled a header: ``음을
유념한다.`` is a heading in the corpus. Such a fragment is demoted back to
prose -- its text is kept, only its status as a section is refused.

None of these is safe to apply by vocabulary, and none needs to be: repetition,
numbering, and sentence-endedness are all properties of the document itself.
The language-dependent parts (which numbering series exist, what a sentence
ending looks like) live in :mod:`dokey.profiles`.
"""
from __future__ import annotations

import difflib
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import ladder as ladderlib
from . import profiles as profileslib
from .models import SectionRange, TocEntry
from .names import ArtifactNamer

MARKDOWN_SUFFIXES = (".md", ".markdown")

# A running mark is short by nature -- a title, a code, a folio. Longer lines
# that repeat are quotations, boilerplate clauses, or table rows, and deleting
# those would cost content.
RUNNING_MARK_MAX_CHARS = 40
# Two occurrences are a coincidence (a title page and its first section); three
# are a pattern. This mirrors the block-level furniture vote, which requires a
# mark to appear on three pages before it is believed.
RUNNING_MARK_MIN_REPEATS = 3
# ... and it must reach across the document: first occurrence to last, at least
# this share of the file. Repeated content (a checklist's "none applicable", a
# continued table's caption) is local; a page mark is not.
RUNNING_MARK_MIN_REACH = 0.4
# A running mark broken into pieces spreads over a few lines, blank lines
# included -- a five-piece document code with a blank line between each piece
# reaches eight lines end to end -- and beyond that the neighbourhood is
# ordinary prose.
ABSORB_SPAN = 4
ABSORB_ROUNDS = 4
ABSORB_MAX_CHARS = 12
# The tail of a split title is a word or two; anything longer is a heading in
# its own right that happens to follow another one.
REJOIN_MAX_TAIL_CHARS = 8
# ...unless the first half is a bare label ("<별표 1>"), which names nothing on
# its own: what follows it is the whole caption, not a word's remainder.
REJOIN_LABEL_TAIL_CHARS = 60
# Two unnumbered titles this alike, in one document, are one title re-set --
# re-keyed with different spacing, a different middle dot, or a typo. Measured
# on 866 documents: 84 echoes of the document's own title, and every pair below
# 0.85 that was checked was two different titles.
ECHO_MIN_RATIO = 0.85
# Short titles collide by accident ("개요", "비고"); only titles long enough to
# be distinctive are compared.
ECHO_MIN_CHARS = 8
# A section is something a reader cites, so it has to be a size a reader can
# hold. Splitting at the document's top rung assumes that rung is the clause --
# true for a plain standard, false for a compilation, where the top rung is an
# annex and one "section" came out at 42,000 characters. So when no depth is
# asked for, dokey descends the ladder until the pieces are of citable size.
# The corpus median section is ~520 characters; this ceiling is generous.
SECTION_TARGET_CHARS = 4000


@dataclass(frozen=True)
class Section:
    order: int  # 1-based document sequence; also the synthetic page number
    level: int  # hierarchy depth, 1..6 (from the file's own levels or derived)
    title: str
    parent: str  # nearest shallower heading, or the section's own title at top
    body: str  # prose beneath this heading, up to the next heading (searchable)


@dataclass
class UnitizeReport:
    """What the unitizer did and what it refused to do.

    Reported rather than logged away: a document ingest that silently drops
    lines is indistinguishable from one that lost them. ``notes`` carries the
    known defects of this particular ingest in plain language.
    """

    profile: str = "none"
    headings: int = 0
    sections: int = 0
    derived_levels: bool = False
    max_level: int | None = None
    running_marks: Counter = field(default_factory=Counter)
    running_mark_lines: int = 0
    repeat_titles_demoted: int = 0
    fragments_demoted: int = 0
    subheadings_folded: int = 0
    titles_rejoined: int = 0
    title_echoes_demoted: int = 0
    empty_headings_demoted: int = 0
    echoes: list = field(default_factory=list)
    ladder: dict = field(default_factory=dict)
    heading_ladder: dict = field(default_factory=dict)
    furniture_tables_dropped: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "profile": self.profile,
            "headings": self.headings,
            "sections": self.sections,
            "derived_levels": self.derived_levels,
            "max_level": self.max_level,
            "running_mark_lines": self.running_mark_lines,
            "running_marks": [
                {"text": text, "count": count}
                for text, count in self.running_marks.most_common(20)
            ],
            "repeat_titles_demoted": self.repeat_titles_demoted,
            "fragments_demoted": self.fragments_demoted,
            "subheadings_folded": self.subheadings_folded,
            "titles_rejoined": self.titles_rejoined,
            "title_echoes_demoted": self.title_echoes_demoted,
            "title_echoes": [
                {"echo": echo, "of": first} for echo, first in self.echoes[:20]
            ],
            "empty_headings_demoted": self.empty_headings_demoted,
            "ladder": self.ladder,
            "heading_ladder": self.heading_ladder,
            "furniture_tables_dropped": self.furniture_tables_dropped,
            "known_defects": list(self.notes),
        }

    def summary(self) -> str:
        parts = [f"{self.sections} sections from {self.headings} headings"]
        if self.derived_levels:
            order = " > ".join(self.heading_ladder.get("order", ())) or "none"
            parts.append(f"levels from the document's ladder [{order}] (max {self.max_level})")
        if self.subheadings_folded:
            parts.append(f"{self.subheadings_folded} subheadings folded into parents")
        if self.running_mark_lines:
            parts.append(f"{self.running_mark_lines} running-mark lines dropped")
        if self.repeat_titles_demoted:
            parts.append(f"{self.repeat_titles_demoted} repeated titles demoted")
        if self.fragments_demoted:
            parts.append(f"{self.fragments_demoted} prose fragments demoted")
        if self.titles_rejoined:
            parts.append(f"{self.titles_rejoined} split titles rejoined")
        if self.title_echoes_demoted:
            parts.append(f"{self.title_echoes_demoted} title echoes demoted")
        if self.empty_headings_demoted:
            parts.append(f"{self.empty_headings_demoted} empty headings demoted")
        if self.furniture_tables_dropped:
            parts.append(f"{self.furniture_tables_dropped} furniture tables dropped")
        return "; ".join(parts)


@dataclass(frozen=True)
class UnitizeResult:
    sections: list[Section]
    report: UnitizeReport
    outline: list[TocEntry] = field(default_factory=list)
    ladder: object | None = None


def is_markdown(path: Path) -> bool:
    return path.suffix.lower() in MARKDOWN_SUFFIXES


# CommonMark: up to three leading spaces, one to six hashes, then either the
# end of the line or whitespace before the content. Four spaces would make it
# an indented code block, and "#tag" is not a heading.
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$")
# A closing sequence of hashes is decoration, not part of the title.
_CLOSING_HASHES = re.compile(r"\s+#+\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_TABLE_ROW = re.compile(r"^ {0,3}\|")
_TABLE_RULE = re.compile(r"^[\s|:-]+$")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BLANK_RUN = re.compile(r"\n{3,}")
_BULLET = re.compile(r"^ {0,3}[-*+]\s+\S")
# A glyph that introduces an item -- a checkbox on a form, a bullet, a bracketed
# cross-reference. Whatever follows it is content the document put there, so a
# line that opens with one is never read as furniture, however often it recurs.
_ITEM_MARKER = re.compile(
    r"^[■-◿•‣◦⁃∙·§※☐-☒"
    r"✔✗▶◆◇♦〈「【<＜\[]"
)
_TRAILING_PUNCT = re.compile(r"[.,;:!?…·]$")
# What closes a sentence: terminal punctuation, or the quote/bracket that
# follows it. A line ending on none of these was interrupted, not finished.
_SENTENCE_CLOSE = re.compile(r"""[.?!:;”’"')\]]$""")
_ALNUM = re.compile(r"[^\W_]", re.UNICODE)
_PURE_NUMBER = re.compile(r"^\d+$")
_DIGITS = re.compile(r"\d+")
# A heading that is only a bracketed label -- no words after the closing
# bracket -- such as "<별표 1>", "[부록 2]", "<부록>".
_BARE_LABEL = re.compile(r"^[<〈《【\[（(]\s*[^<>〈〉《》【】\[\]()]{1,14}\s*[>〉》】\］\])]$")


@dataclass
class _Head:
    line: int
    atx_level: int
    title: str
    numbering: object | None = None
    addressed: bool = False  # numbered *and* the number advances
    level: int = 1
    verdict: str = "section"  # section | running_mark | repeat | fragment | folded


@dataclass
class _Scan:
    lines: list[str]
    heads: list[_Head]
    fence: list[bool]
    table: list[bool]


def _scan(markdown: str) -> _Scan:
    """Split into lines, tracking the two contexts where ``#`` is not a heading.

    Inside a fenced code block a ``#`` line is code (a shell comment, a C
    preprocessor directive); inside a table it is a cell value. Both are common
    enough in real Markdown that reading them as headings invents sections.
    """
    lines = markdown.splitlines()
    heads: list[_Head] = []
    in_fence: str | None = None
    fence_flags: list[bool] = []
    table_flags: list[bool] = []
    for index, line in enumerate(lines):
        fence_match = _FENCE.match(line)
        if in_fence is not None:
            fence_flags.append(True)
            table_flags.append(False)
            if fence_match and fence_match.group(1)[0] == in_fence[0] and len(
                fence_match.group(1)
            ) >= len(in_fence):
                in_fence = None
            continue
        if fence_match:
            in_fence = fence_match.group(1)
            fence_flags.append(True)
            table_flags.append(False)
            continue
        fence_flags.append(False)
        is_table = bool(_TABLE_ROW.match(line))
        table_flags.append(is_table)
        if is_table:
            continue
        match = _HEADING.match(line)
        if match:
            title = _CLOSING_HASHES.sub("", (match.group(2) or "")).strip()
            if title:
                heads.append(_Head(index, len(match.group(1)), title))
    return _Scan(lines, heads, fence_flags, table_flags)


def _mark_candidate(text: str, profile) -> bool:
    """Could this line be page furniture rather than content?

    The test is deliberately about *shape*, not words: content carries a
    sentence, an enumerator, or punctuation that continues one. What is left --
    a bare title, a document code, a lone glyph -- is what a page repeats.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > RUNNING_MARK_MAX_CHARS:
        return False
    if not _ALNUM.search(stripped):
        return False  # punctuation alone; resolved by cluster absorption
    if _PURE_NUMBER.match(stripped):
        return False  # a folio needs sequence evidence, and a table cell looks the same
    if _TRAILING_PUNCT.search(stripped):
        return False  # a clause that continues is not a mark
    if _BULLET.match(stripped) or stripped.startswith(">"):
        return False
    if _ITEM_MARKER.match(stripped):
        return False
    if "<!--" in stripped:
        return False  # a renderer's placeholder marks content that is there
    if profile.numbering(stripped) is not None:
        return False  # numbered lines are addressed content, never furniture
    if profile.is_sentence_tail(stripped):
        return False
    return True


def _running_mark_keys(scan: _Scan, profile) -> set[str]:
    """Keys of short *body* lines that recur often enough to be page furniture.

    Only lines the renderer did not mark as headings vote here. A heading that
    repeats is a different phenomenon -- an analytical method restates
    ``농도계산`` under every analyte -- and deleting it would cost a real title;
    such headings are demoted to prose instead, keeping their text. A running
    header that the renderer marked as a heading on some pages and as body text
    on others still qualifies, through its body-text occurrences.

    Repetition alone would not be enough, because content repeats too: a
    checklist restates ``해당사항 없음`` under every item, and a table continued
    across pages restates its caption. What separates furniture from those is
    *reach* -- a page mark runs from the document's first page to its last,
    while repeated content stays inside the one passage that needs it -- so a
    key must also span a large share of the document before it is believed.
    """
    counts: Counter = Counter()
    reach: dict[str, list[int]] = {}
    heading_lines = {head.line for head in scan.heads}
    for index, line in enumerate(scan.lines):
        if scan.fence[index] or scan.table[index] or index in heading_lines:
            continue
        if _mark_candidate(line, profile):
            key = profile.key(line)
            if len(key) >= 2:  # a lone glyph is a split word until proven furniture
                counts[key] += 1
                reach.setdefault(key, []).append(index)
    width = max(1, len(scan.lines) - 1)
    return {
        key
        for key, count in counts.items()
        if count >= RUNNING_MARK_MIN_REPEATS
        and (reach[key][-1] - reach[key][0]) / width >= RUNNING_MARK_MIN_REACH
    }


def _near(index: int, dropped: set[int], span: int = ABSORB_SPAN) -> bool:
    return any(
        neighbour in dropped
        for neighbour in range(index - span, index + span + 1)
        if neighbour != index
    )


def _furniture_lines(scan: _Scan, keys: set[str], profile) -> tuple[set[int], Counter]:
    """Line indexes to drop, plus what was dropped and how often.

    Two passes. The first drops lines matching a running-mark key. The second
    grows each of those into the *cluster* it belongs to, because a renderer
    that meets a running header in a stretched layout emits it in pieces:
    ``D`` / ``-`` / ``C`` / ``-`` / ``10 - 2026``, five lines for one document
    code. A piece is absorbed only when it keeps that company habitually --
    at least three of its occurrences sit beside an already-dropped line, and
    those account for at least half of them. That distinction is what keeps a
    genuine one-syllable line (``것``, the tail of ``~할 것`` broken across a
    column) out of the drop set: it stands beside prose, not beside marks.
    """
    dropped: set[int] = set()
    seen: Counter = Counter()
    heading_lines = {head.line for head in scan.heads}
    for index, line in enumerate(scan.lines):
        if scan.fence[index] or scan.table[index]:
            continue
        text = line
        if index in heading_lines:
            match = _HEADING.match(line)
            text = _CLOSING_HASHES.sub("", (match.group(2) or "")) if match else line
        if not text.strip():
            continue
        if profile.key(text) in keys and _mark_candidate(text, profile):
            dropped.add(index)
            seen[text.strip()] += 1

    occurrences: dict[str, list[int]] = {}
    for index, line in enumerate(scan.lines):
        stripped = line.strip()
        if index in dropped or scan.fence[index] or scan.table[index]:
            continue
        if index in heading_lines or not stripped:
            continue
        if len(stripped) > ABSORB_MAX_CHARS:
            continue
        if profile.numbering(stripped) is not None:
            continue  # an enumerator addresses content; it is not debris
        if _TRAILING_PUNCT.search(stripped) or profile.is_sentence_tail(stripped):
            continue  # a clause tail keeps the company of marks by accident
        if _ITEM_MARKER.match(stripped) or _BULLET.match(stripped):
            continue  # an item the document introduced is not debris
        occurrences.setdefault(profile.key(stripped) or stripped, []).append(index)

    for _ in range(ABSORB_ROUNDS):
        grew = False
        for key, indexes in occurrences.items():
            beside = [
                index
                for index in indexes
                if index not in dropped and _near(index, dropped)
            ]
            if not beside:
                continue
            # Support is counted over *all* of the key's occurrences, the ones
            # already dropped included: a cluster is absorbed from its middle
            # outwards, and its outermost piece would otherwise be left behind
            # as the last of its kind.
            support = sum(
                1 for index in indexes if index in dropped or _near(index, dropped)
            )
            if support < RUNNING_MARK_MIN_REPEATS:
                continue
            # A piece that carries letters or digits could be a word the layout
            # broke off, so it must keep the marks' company as its *habit*, not
            # just three times out of many. A piece that is punctuation alone
            # carries nothing to lose.
            if _ALNUM.search(key) and support * 2 < len(indexes):
                continue
            for index in beside:
                dropped.add(index)
                seen[scan.lines[index].strip()] += 1
            grew = True
        if not grew:
            break
    return dropped, seen


def _furniture_tables(scan: _Scan, keys: set[str], profile) -> tuple[set[int], int]:
    """Whole tables whose every cell is a running mark.

    A renderer that meets a running header laid out in columns emits it as a
    one-row table (``| KOSHA GUIDE | KOSHA GUIDE |``), which no cell-level rule
    would catch because the row is not a repeated *line*.
    """
    dropped: set[int] = set()
    tables = 0
    index = 0
    while index < len(scan.lines):
        if not scan.table[index]:
            index += 1
            continue
        start = index
        while index < len(scan.lines) and scan.table[index]:
            index += 1
        block = range(start, index)
        cells: list[str] = []
        for row in block:
            line = scan.lines[row]
            if _TABLE_RULE.match(line):
                continue
            cells.extend(cell.strip() for cell in line.strip().strip("|").split("|"))
        content = [cell for cell in cells if cell]
        if content and all(profile.key(cell) in keys for cell in content):
            dropped.update(block)
            tables += 1
    return dropped, tables


def _assign_levels(heads: list[_Head], ladder) -> bool:
    """Give every kept heading a hierarchy level; say whether it was derived.

    When the file uses more than one heading level, that is the author's own
    hierarchy and dokey honors it. When every heading is the same level -- the
    normal case for a laid-out document rendered to Markdown, since the
    renderer saw one *visual* class of heading -- the hierarchy is recovered
    from the numbering instead. An unnumbered heading inherits one rung below
    the last numbered one, so front matter (before any numbering) stays top
    level while an unnumbered subheading sits under its clause.
    """
    kept = [head for head in heads if head.verdict == "section"]
    derived = len({head.atx_level for head in kept}) <= 1 and len(kept) > 1
    if not derived:
        for head in kept:
            head.level = head.atx_level
        return False
    last_numbered = 0
    for head in kept:
        if head.addressed:
            head.level = min(ladder.depth(head.numbering), 6)
            last_numbered = head.level
        else:
            head.level = min(last_numbered + 1, 6) if last_numbered else 1
    return True


def _demote_headings(heads: list[_Head], mark_keys: set[str], profile, report) -> None:
    """Refuse section status to headings that are not sections.

    Three refusals, in order of confidence: a heading whose text is a running
    mark elsewhere in the document; an unnumbered title that repeats (the
    running title of the document, re-emitted at every page break); and a
    heading that is a *sentence*, which is a fragment the page break cut loose
    or a list item the renderer over-promoted.

    Numbering only ever protects: a numbered heading is never refused for
    repeating or for matching a mark, because filtering heading candidates *by*
    numbering was measured to cost more real sections than it removes false
    ones. The sentence test applies to numbered headings too -- a clause title
    ends on a noun, so ``2. … 개선해야 한다.`` is a numbered list item that the
    renderer promoted, not a clause.
    """
    keys = [profile.key(head.title) for head in heads]
    counts = Counter(keys)
    first_seen: dict[str, int] = {}
    for position, head in enumerate(heads):
        key = keys[position]
        if not head.addressed:
            if key in mark_keys:
                head.verdict = "running_mark"
                report.running_marks[head.title.strip()] += 1
                continue
            if counts[key] > 1:
                if counts[key] >= RUNNING_MARK_MIN_REPEATS or key in first_seen:
                    head.verdict = "repeat"
                    report.repeat_titles_demoted += 1
                    continue
                first_seen[key] = position
        if _ITEM_MARKER.match(head.title):
            continue  # a labelled object (<표 3>, 【참고】) is a title, not prose
        if profile.is_sentence_tail(head.title):
            head.verdict = "fragment"
            report.fragments_demoted += 1


def _ends_mid_sentence(text: str, profile) -> bool:
    """Does the text before a heading stop in the middle of a sentence?"""
    stripped = text.strip()
    if not stripped or stripped.startswith(("|", "<!--")):
        return False
    if _SENTENCE_CLOSE.search(stripped):
        return False
    return not profile.is_sentence_tail(stripped)


def _demote_interrupting_fragments(
    scan: _Scan, drop_lines: set[int], profile, report
) -> None:
    """Refuse an unnumbered heading that breaks a run of consecutive numbers.

    A document that writes ``11.1`` and then ``11.2`` numbers everything at
    that rung, so whatever stands between them took no number -- and if the
    text just before it also stops mid-sentence, what stands there is the
    sentence's other half, cut loose by the page break that put a running
    header through the middle of it.

    Both halves of the test are needed. Numbering alone would refuse real
    unnumbered titles (figure captions, an appendix's own headings), which is
    the failure mode that costs sections; the mid-sentence test alone fires on
    ordinary front matter. Together they refused 248 headings across the 866
    measured documents, against 81 for the sentence-ending rule on its own.
    """
    kept = [head for head in scan.heads if head.verdict == "section"]
    for position, head in enumerate(kept):
        if head.addressed or _ITEM_MARKER.match(head.title):
            continue
        if position == 0 or position + 1 >= len(kept):
            continue
        before, after = kept[position - 1], kept[position + 1]
        if not profileslib.is_successor(before.numbering, after.numbering):
            continue
        previous = ""
        for index in range(head.line - 1, -1, -1):
            if index in drop_lines or not scan.lines[index].strip():
                continue
            previous = scan.lines[index]
            break
        if _ends_mid_sentence(previous, profile):
            head.verdict = "fragment"
            report.fragments_demoted += 1


def _rejoin_split_titles(scan: _Scan, drop_lines: set[int], profile, report) -> None:
    """Put a title back together when the layout cut it in two.

    ``## 1. 목`` followed by ``## 적`` is one heading, ``1. 목 적``, split where
    a page ended; the corpus has 292 such pairs. The evidence that it is one
    heading and not two is positional and grammatical at once: nothing stands
    between them, the first ends on a single syllable (a word cut in half), and
    the second is short and unnumbered. Without this the section keeps the
    truncated title, which is what a reader and a search index would both see.
    """
    heads_by_line = {head.line: head for head in scan.heads}
    joinable = ("section", "fragment")
    for previous, head in zip(scan.heads, scan.heads[1:]):
        if head.verdict not in joinable or previous.verdict not in joinable:
            continue
        if head.addressed:
            continue
        # A bare label names something that has not been named yet: "<별표 1>"
        # on one line, "소화기구의 소화약제별 적응성" on the next, then the table.
        # Both lines are one title, so the tail may be a whole caption here.
        label_only = bool(_BARE_LABEL.match(previous.title))
        limit = REJOIN_LABEL_TAIL_CHARS if label_only else REJOIN_MAX_TAIL_CHARS
        if len(head.title) > limit:
            continue
        between = [
            index
            for index in range(previous.line + 1, head.line)
            if scan.lines[index].strip()
            and index not in drop_lines
            and index not in heads_by_line
        ]
        if between:
            continue
        tokens = previous.title.split()
        if not label_only:
            if not tokens or len(tokens[-1]) != 1 or not _ALNUM.match(tokens[-1]):
                continue
            # A title set with letter spacing ("한 국 산 업") ends on a single
            # syllable by typography, not by truncation; it is whole already.
            if len(tokens) >= 3 and sum(len(token) == 1 for token in tokens) * 2 > len(
                tokens
            ):
                continue
        if head.verdict == "fragment":
            report.fragments_demoted -= 1
        previous.title = profile.join_title(previous.title, head.title)
        head.verdict = "rejoined"
        report.titles_rejoined += 1
        # The half-title may have read as prose while it was cut short -- a
        # clause title ending on "및" is a truncation, not a sentence -- so the
        # whole title gets its status back.
        if previous.verdict == "fragment" and (
            previous.addressed
            or not profile.is_sentence_tail(previous.title)
        ):
            previous.verdict = "section"
            report.fragments_demoted -= 1


def _title_echo_ratio(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, left, right).ratio()


SECTION_DEPTH_CHOICES = ("auto", "clause", "subclause")


def _clause_rung(heads: list[_Head], ladder) -> int:
    """The rung this document heads its clauses on.

    Not every document starts at rung 1: one that opens with annexes or parts
    puts its clauses a rung below them. Splitting at "the clause rung" rather
    than at "rung 1" is what makes a depth mean the same thing in two documents
    whose ladders differ.
    """
    rungs = [
        ladder.depth(head.numbering)
        for head in heads
        if head.verdict == "section" and head.addressed
    ]
    return min(rungs) if rungs else 1


def _resolve_depth(
    requested, scan: _Scan, ladder, sizes: dict[int, list[int]]
) -> int | None:
    """Turn what the caller asked for into a rung.

    ``clause``/``subclause`` are read from the document's own ladder, so the
    same word picks the same *kind* of unit everywhere. A number is that rung,
    everywhere, whatever it holds. ``auto`` lets the pieces decide, which is
    the friendliest default and the least comparable between documents.
    """
    if isinstance(requested, int):
        return requested
    if requested in (None, "auto"):
        return _choose_max_level(scan, sizes)
    clause = _clause_rung(scan.heads, ladder)
    if requested == "clause":
        return clause
    if requested == "subclause":
        return clause + 1
    raise ValueError(
        f"Unknown section depth: {requested!r} "
        f"(use a number or one of {', '.join(SECTION_DEPTH_CHOICES)})"
    )


def _choose_max_level(scan: _Scan, lengths: dict[int, list[int]]) -> int:
    """How deep to split when the caller did not say.

    Descend one rung at a time while the sections a rung yields are too large
    to cite, and stop at the shallowest rung that is small enough -- or at the
    deepest the document offers, when none is.
    """
    for depth in sorted(lengths):
        sizes = lengths[depth]
        if not sizes:
            continue
        if statistics.median(sizes) <= SECTION_TARGET_CHARS:
            return depth
    return max(lengths) if lengths else 1


def _section_sizes(scan: _Scan, drop_lines: set[int]) -> dict[int, list[int]]:
    """Characters each candidate depth would put in a section."""
    offsets: list[int] = []
    running = 0
    for line in scan.lines:
        offsets.append(running)
        running += len(line) + 1
    heads = [head for head in scan.heads if head.verdict == "section"]
    if not heads:
        return {}
    depths = sorted({head.level for head in heads})
    sizes: dict[int, list[int]] = {}
    for depth in depths:
        starts = [head.line for head in heads if head.level <= depth]
        if not starts:
            continue
        bounds = starts + [len(scan.lines)]
        sizes[depth] = [
            offsets[end] - offsets[start] if end < len(offsets) else running - offsets[start]
            for start, end in zip(bounds, bounds[1:])
        ]
    return sizes


def _demote_title_echoes(scan: _Scan, profile, report) -> None:
    """Sweep the whole document before deciding which titles are titles.

    A running title is re-set on every page, and the type is re-keyed each
    time, so it comes back not identical but *nearly* so: ``정련기 방호조치``
    against ``정련기의 방호조치``, ``분상·입상`` against ``분상․입상``, and --
    measured in A-101-2018 -- ``기술지침`` against the typo ``기술지칩`` that is
    genuinely on the page. Exact-match repetition cannot see any of these, which
    is why this pass compares every unnumbered title against every other one
    across the document rather than against its neighbours.

    Numbered headings are excluded, and that exclusion is what keeps the rule
    honest: ``5. 수동 밴드나이프 전단기의 유해·위험요인`` and ``6. 자동 밴드나이
    프…`` are 90% alike and are two different clauses. Numbering says the
    document itself distinguished them.
    """
    survivors = [head for head in scan.heads if head.verdict == "section"]
    if not survivors:
        return
    # The document's own title is the thing a running title echoes, so that is
    # what every other title is compared against -- not every title against
    # every other, which was measured to equate real siblings: two appendices
    # (``<부록 4> 하중계 설치방법…`` / ``<부록 1> 지중경사계 설치방법…``) are 90%
    # alike and are two different appendices.
    document = survivors[0]
    if document.addressed:
        return  # a numbered first heading is a clause, not a title page
    title_key = profile.key(document.title)
    if len(title_key) < ECHO_MIN_CHARS:
        return
    for head in survivors[1:]:
        if head.addressed:
            continue
        key = profile.key(head.title)
        if len(key) < ECHO_MIN_CHARS:
            continue
        # Different ordinals mean different things, however alike the words are.
        if _DIGITS.findall(key) != _DIGITS.findall(title_key):
            continue
        if _title_echo_ratio(key, title_key) < ECHO_MIN_RATIO:
            continue
        head.verdict = "echo"
        report.title_echoes_demoted += 1
        report.echoes.append((head.title.strip(), document.title.strip()))


def _demote_empty_headings(scan: _Scan, drop_lines: set[int], profile, report) -> None:
    """Refuse an unnumbered heading with nothing underneath it.

    A section is a passage; a title with no passage is decoration -- a cover
    line, a running title standing at the top of a page, the residue of a title
    the layout broke. Numbered headings are exempt: ``제2장`` legitimately heads
    a chapter whose first clause follows immediately, and the document's own
    numbering says it is a division.
    """
    changed = True
    while changed:
        changed = False
        starts = [head for head in scan.heads if head.verdict == "section"]
        for position, head in enumerate(starts):
            if head.addressed:
                continue
            end = (
                starts[position + 1].line
                if position + 1 < len(starts)
                else len(scan.lines)
            )
            has_body = any(
                index not in drop_lines and scan.lines[index].strip()
                for index in range(head.line + 1, end)
                if index not in {other.line for other in scan.heads}
            )
            if not has_body:
                head.verdict = "empty"
                report.empty_headings_demoted += 1
                changed = True
                break


def derive_outline(sections: list[Section]) -> list[TocEntry]:
    """The document's table of contents, as swept from its own headings.

    Sections and the outline are the same list seen twice: this is the form the
    PDF path speaks (``TocEntry``), so a lake built from a render carries a TOC
    like any other, and the entry's page is the section's synthetic page.
    """
    return [
        TocEntry(
            level=section.level,
            title=section.title,
            page=section.order,
            parent=None if section.parent == section.title else section.parent,
        )
        for section in sections
    ]


def unitize(
    markdown: str,
    *,
    fallback_title: str = "Document",
    max_level: int | str | None = None,
    profile: str | None = "auto",
) -> UnitizeResult:
    """Unitize Markdown into sections, reporting what was dropped or demoted.

    ``max_level`` caps section depth. Left unset it applies only where levels
    had to be derived from numbering, and then defaults to 1 -- the same depth
    the PDF outline path splits at, so a document ingested from its render and
    from its PDF yields the same sections. An explicit value always applies.
    """
    active = profileslib.resolve(profile, markdown)
    report = UnitizeReport(profile=active.name, max_level=max_level)
    scan = _scan(markdown)
    report.headings = len(scan.heads)

    # Which series encloses which is the document's own convention, so it is
    # read off the document before anything is decided by it.
    ladder = ladderlib.induce_from_lines(scan.lines, active)
    # Sections are decided by headings, so their rungs are induced from
    # headings. A series a document uses in both places -- ``1.`` heading its
    # clauses and again numbering the items inside a checklist -- ranks by
    # whichever use is commoner, and in a document full of checklists that is
    # the deep one: measured in D-C-8-2026, clause numbering landed on rung 3
    # and every clause folded away, leaving one section holding 14,627
    # characters. The body ladder still addresses the items; it just does not
    # get to say what a section is.
    heading_ladder = ladder
    heading_titles = [head.title for head in scan.heads]
    if len(heading_titles) >= 2:
        induced = ladderlib.induce_from_lines(heading_titles, active)
        if induced.order:
            heading_ladder = ladderlib.Ladder(
                rank=induced.rank,
                order=induced.order,
                source=induced.source,
                evidence=induced.evidence,
                # Whether a series' ordinals advance is a fact about the whole
                # document, not about its headings.
                unordered_kinds=ladder.unordered_kinds,
            )
    report.ladder = ladder.as_dict()
    report.heading_ladder = heading_ladder.as_dict()
    for head in scan.heads:
        head.numbering = active.numbering(head.title)
        head.addressed = head.numbering is not None and (
            heading_ladder.kind_of(head.numbering) != ladderlib.UNORDERED
        )

    mark_keys = _running_mark_keys(scan, active)
    drop_lines, dropped_marks = _furniture_lines(scan, mark_keys, active)
    table_lines, table_count = _furniture_tables(scan, mark_keys, active)
    drop_lines |= table_lines
    report.running_marks.update(dropped_marks)
    report.running_mark_lines = len(drop_lines)
    report.furniture_tables_dropped = table_count

    _demote_headings(scan.heads, mark_keys, active, report)
    for head in scan.heads:
        if head.line in drop_lines and head.verdict == "section":
            head.verdict = "running_mark"
    _demote_interrupting_fragments(scan, drop_lines, active, report)
    _rejoin_split_titles(scan, drop_lines, active, report)
    # Document-level sweep: judge each title against every other title in the
    # document, not against its neighbours, before any of them becomes a section.
    _demote_title_echoes(scan, active, report)
    _demote_empty_headings(scan, drop_lines, active, report)
    derived = _assign_levels(scan.heads, heading_ladder)
    report.derived_levels = derived

    if derived or isinstance(max_level, int):
        effective_max = _resolve_depth(
            max_level, scan, heading_ladder, _section_sizes(scan, drop_lines)
        )
    else:
        # The file states its own hierarchy; a named depth still applies, but
        # "auto" leaves the author's levels alone.
        effective_max = (
            _resolve_depth(max_level, scan, heading_ladder, _section_sizes(scan, drop_lines))
            if max_level not in (None, "auto")
            else None
        )
    report.max_level = effective_max
    if effective_max is not None:
        for head in scan.heads:
            if head.verdict == "section" and head.level > effective_max:
                head.verdict = "folded"
                report.subheadings_folded += 1

    sections = _build_sections(scan, drop_lines, fallback_title, report)
    report.sections = len(sections)
    if not sections:
        report.notes.append(
            "no sections: the document has no text after furniture removal"
        )
    elif report.headings == 0:
        report.notes.append(
            "no headings found; the whole document is one section "
            "(the renderer may not have marked headings)"
        )
    return UnitizeResult(sections, report, derive_outline(sections), ladder)


def _body_line(scan: _Scan, head: _Head) -> str:
    """How a demoted heading re-enters the body.

    A folded subheading keeps its own marker so the reader still sees where the
    clause begins; a fragment or a repeated title returns as plain prose, since
    its marker was the renderer's mistake, not the document's structure.
    """
    if head.verdict == "folded":
        return scan.lines[head.line]
    if head.verdict in ("echo", "empty"):
        # A re-set running title and a heading with nothing under it are both
        # page decoration; their text is not the document saying anything new.
        return ""
    return head.title


def _build_sections(
    scan: _Scan, drop_lines: set[int], fallback_title: str, report: UnitizeReport
) -> list[Section]:
    heads_by_line = {head.line: head for head in scan.heads}
    starts = [head for head in scan.heads if head.verdict == "section"]

    def body_between(start: int, end: int) -> str:
        collected: list[str] = []
        for index in range(start, end):
            if index in drop_lines:
                continue
            head = heads_by_line.get(index)
            if head is not None:
                if head.verdict in ("section", "rejoined"):
                    # A rejoined tail already lives in the title it belongs to.
                    continue
                collected.append(_body_line(scan, head))
                continue
            collected.append(scan.lines[index])
        # Removing a mark leaves the blank lines that surrounded it; collapse
        # the gap so the paragraph either side reads as one break, not a hole.
        return _BLANK_RUN.sub("\n\n", "\n".join(collected)).strip()

    sections: list[Section] = []
    if not starts:
        body = body_between(0, len(scan.lines))
        if not body:
            return []
        return [Section(1, 1, fallback_title, fallback_title, body)]

    preamble = body_between(0, starts[0].line)
    if preamble:
        sections.append(Section(1, 1, fallback_title, fallback_title, preamble))

    stack: list[tuple[int, str]] = []
    for position, head in enumerate(starts):
        end = starts[position + 1].line if position + 1 < len(starts) else len(scan.lines)
        body = body_between(head.line + 1, end)
        while stack and stack[-1][0] >= head.level:
            stack.pop()
        parent = stack[-1][1] if stack else head.title
        sections.append(
            Section(len(sections) + 1, head.level, head.title, parent, body)
        )
        stack.append((head.level, head.title))
    return sections


def split_markdown(
    markdown: str,
    *,
    fallback_title: str = "Document",
    max_level: int | str | None = None,
    profile: str | None = "auto",
) -> list[Section]:
    """Unitize Markdown by heading; see :func:`unitize` for the reporting form."""
    return unitize(
        markdown,
        fallback_title=fallback_title,
        max_level=max_level,
        profile=profile,
    ).sections


def build_section_ranges(
    sections: list[Section],
    output_dir: Path,
    pages: list[tuple[int, int]] | None = None,
) -> list[SectionRange]:
    """Turn heading sections into SectionRange rows with synthetic pages.

    Each section occupies exactly one synthetic page equal to its sequence
    number, so the page-range machinery downstream (manifest, index, the
    ``page BETWEEN pdf_start AND pdf_end`` search join) maps a page hit back to
    its one section. Artifacts are per-section ``.md`` files, not split PDFs.

    The synthetic page is a fallback, not a claim: a render carries no page
    numbers, and the running marks that survive in it were measured not to
    reconstruct them (the mark count matched the true page count for 1 document
    in 866). When ``pages`` is given -- read from the block stream the render
    came from, which keeps ``page_no`` -- the real range is used instead, and a
    section may then span several pages or share one with its neighbours, as it
    does in the document.
    """
    parent_indexes: dict[str, int] = {}
    parent_item_counts: dict[str, int] = {}
    namer = ArtifactNamer()
    ranges: list[SectionRange] = []
    for section in sections:
        parent = section.parent or section.title
        if parent not in parent_indexes:
            parent_indexes[parent] = len(parent_indexes) + 1
        parent_item_counts[parent] = parent_item_counts.get(parent, 0) + 1
        parent_index = parent_indexes[parent]
        parent_item_index = parent_item_counts[parent]
        page = section.order
        # Page-independent name (see ranges.build_ranges): the title alone, so a
        # re-ingest overwrites in place. The synthetic page and the section's
        # ordinal live in the manifest fields, not in the name.
        parent_folder, filename = namer.name(
            title=section.title, parent=parent, suffix=".md"
        )
        first_page, last_page = (
            pages[len(ranges)] if pages and len(ranges) < len(pages) else (page, page)
        )
        ranges.append(
            SectionRange(
                index=len(ranges) + 1,
                parent_index=parent_index,
                parent_item_index=parent_item_index,
                parent=parent,
                parent_folder=parent_folder,
                title=section.title,
                content_start_page=first_page,
                content_end_page=last_page,
                pdf_start_page=first_page,
                pdf_end_page=last_page,
                page_count=last_page - first_page + 1,
                output_file=str(
                    output_dir / "by_section" / parent_folder / filename
                ),
            )
        )
    return ranges


def section_page_text(section: Section) -> str:
    """The searchable text for a section's synthetic page: title plus body, so
    heading words are found by full-text search too (titles are also boosted
    separately via the section index). Renderer placeholders (``<!-- image -->``)
    are stripped here only -- they stay in the artifact, where they mark the
    figure's position."""
    body = _HTML_COMMENT.sub("", section.body)
    return f"{section.title}\n{body}".strip()
