"""Build a TOC from the book's own printed contents page, by geometry.

``toc.py`` reads a TOC that someone already serialized to a CSV or text file,
and ``outline.py`` reads the PDF's embedded bookmarks. Many books have neither:
the only table of contents is printed on a page or two of the PDF itself. This
module recovers it directly.

It does not parse the serialized text stream. A serialized contents line loses
the two facts that make a TOC legible -- the *indentation* that encodes
hierarchy and the *right-hand column* that carries the page number -- and it
depends on dot leaders (``......``) that are often typeset as plain spacing and
vanish on extraction. Instead this reads the word boxes: each word carries an
(x0, y0) position, so an entry is reconstructed from its layout the way a reader
sees it -- title on the left, page number as the trailing token, indentation
depth giving the level.

Two facts of real books drive the design, both observed on a 511-page volume
whose contents pages carry zero dot leaders:

* The left margin drifts between facing pages (a recto/verso shift of ~18pt),
  so a given logical level sits at different absolute x0 on odd and even pages.
  Levels are therefore assigned *per page* and anchored to the chapter tier
  (the indentation whose entries are numbered ``N``), which is drift-immune.
* A long title wraps to a second physical line, and the page number lands on
  that continuation. Numberless fragments in the text column are buffered and
  merged into the following numbered line so the title is not truncated.

Word geometry comes from PyMuPDF, declared as the optional ``ocr`` extra and
imported lazily; the core stays dependency-light.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import TocEntry
from .ocr import _parse_lines, render_band
from .toc import fill_parents, mark_leaf_entries

# A trailing page number: the last token on an entry line is a bare integer.
_PAGENUM = re.compile(r"^\d{1,4}$")
# Dot-leader tokens (```......``` / ``······``) between the title and the number.
_LEADER = re.compile(r"^[.·•․‥…\-_]+$")
# A fused entry tail: title text, dot leader, and page number set as one word
# with no intervening spaces ("개요·····12"), the norm in Korean/CJK
# typesetting where the leader belongs to the title's text run. At least two
# leader characters are required so a decimal in a title ("물류4.0") never
# splits.
_FUSED_TAIL = re.compile(r"^(?P<title>.*?)[.·•․‥…\-_]{2,}(?P<page>\d{1,4})$")
# A leading dotted section number ("10", "10.2", "10.2.3"); its dot count is depth.
_SECTION_NO = re.compile(r"^(\d+(?:\.\d+)+|\d+)\b")
# Korean structural prefixes: 제N편/부/장 mark the chapter tier and 제N절/항
# one deeper ("제1장 사업 개요" > "제2절 추진배경 및 필요성").
_KO_STRUCT = re.compile(r"^제\s?\d+\s?(?P<unit>[편부장절항])(?=\s|$)")
# A page number set in Roman numerals, which is how front matter is folioed
# ("요 약···········ⅴ"). Both the Unicode number forms and the ASCII spelling
# occur; the ASCII one is matched in its canonical form only, so a title ending
# in a word that happens to be Roman letters ("DVD") is not read as a folio.
_ROMAN_UNICODE = re.compile(r"^[Ⅰ-ↂⅰ-ↄ]+$")
_ROMAN_ASCII = re.compile(
    r"^(?=[ivxlcdm])m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})$",
    re.IGNORECASE,
)
# The same row with its leader fused into the title's text run ("요 약·····ⅴ").
_FUSED_ROMAN_TAIL = re.compile(
    r"[.·•․‥…\-_]{2,}(?P<page>[A-Za-zⅠ-ↄ]{1,7})$"
)
# A row that names an object rather than a division: <표 2-1>, <그림 3-4>,
# Table 5. A page of these is a list of tables or figures, which in a Korean
# report follows the contents under the same "차례 / CONTENTS" running head and
# so reads as a contents page to any test that merely counts title-and-page rows.
_OBJECT_LABEL = re.compile(
    r"^[<〈《【\[(]?\s*(?:표|그림|사진|부표|부도|Table|Figure|Fig\.?|Photo)\s*[\d<\[]"
)
# Titles that survive stripping must still contain a letter or CJK character;
# this drops stray numeric rows (folios, figure numbers) that end in an integer.
_HAS_WORD = re.compile(r"[^\W\d_]", re.UNICODE)
_TOC_HEADERS = {
    "contents",
    "table of contents",
    "목차",
    "차례",
    "목 차",
    "차 례",
}


# How far apart two entries may start and still be the same indentation tier.
# A real indent step is an em or more; this is the jitter of setting the same
# tier twice.
_TIER_TOLERANCE = 3.0


@dataclass(frozen=True)
class _RawEntry:
    x0: float
    title: str
    page: int


def _lazy_fitz():
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "Reading a printed TOC page by geometry needs PyMuPDF. Install the "
            "optional extra:\n"
            "  python -m pip install -e .[ocr]\n"
            "or\n"
            "  python -m pip install pymupdf"
        ) from exc
    return fitz


def _line_words(page) -> list[list[tuple]]:
    """Group a page's words into visual lines, each sorted left to right.

    ``page.get_text("words")`` yields ``(x0, y0, x1, y1, word, block, line,
    word_no)``. Words that share a (block, line) belong to one visual line.
    Lines are returned top to bottom.
    """
    words = page.get_text("words")
    lines: dict[tuple[int, int], list[tuple]] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    ordered = [sorted(group, key=lambda w: w[0]) for group in lines.values()]
    ordered.sort(key=lambda ln: (round(ln[0][1], 1), ln[0][0]))
    return ordered


def _num_depth(title: str) -> int | None:
    """Hierarchy depth from a leading section marker, else None.

    Dotted numbers give their dot count ("10" -> 0, "10.2" -> 1). Korean
    structural prefixes map 제N편/부/장 to the chapter tier (0) and 제N절/항
    one deeper (1), so pages carrying no dotted numbers still anchor.
    """
    match = _SECTION_NO.match(title)
    if match is not None:
        return match.group(1).count(".")
    match = _KO_STRUCT.match(title)
    if match is not None:
        return 0 if match.group("unit") in "편부장" else 1
    return None


def _entry_from_tokens(tokens: list[str], x0: float) -> _RawEntry | None:
    """Parse an already-line-grouped entry: title + trailing page number.

    The page number is either its own trailing token ("... Alpha 12") or fused
    into the last title word behind a dot leader ("...개요·····12"); the fused
    form is split before parsing.
    """
    if not tokens:
        return None
    if not _PAGENUM.match(tokens[-1]):
        fused = _FUSED_TAIL.match(tokens[-1])
        if fused is None:
            return None
        head = [fused.group("title")] if fused.group("title") else []
        tokens = tokens[:-1] + head + [fused.group("page")]
    if len(tokens) < 2:
        return None
    page = int(tokens[-1])
    title_tokens = tokens[:-1]
    while title_tokens and _LEADER.match(title_tokens[-1]):
        title_tokens.pop()
    title = " ".join(title_tokens).strip()
    title = re.sub(r"[\s.,;:·•․‥…\-_]+$", "", title).strip()
    if len(title) < 2 or not _HAS_WORD.search(title):
        return None
    return _RawEntry(x0=round(x0, 1), title=title, page=page)


def _entry_from_line(line: list[tuple]) -> _RawEntry | None:
    return _entry_from_tokens([w[4] for w in line], line[0][0])


def _is_header_fragment(text: str) -> bool:
    return text.strip().lower() in _TOC_HEADERS


def _is_roman_folio(token: str) -> bool:
    return bool(_ROMAN_UNICODE.match(token) or _ROMAN_ASCII.match(token))


def _is_front_matter_row(tokens: list[str]) -> bool:
    """A contents row whose folio is a Roman numeral, i.e. front matter.

    The front matter runs on its own folio series, and that series shares no
    scale with the body's Arabic one: the ⅴ of a summary and the 5 of a clause
    are different pages. Such a row is therefore recognized in order to be left
    out. Recognizing it is what matters -- unrecognized, it reads as a line
    without a page number and is glued onto the title of the first real entry
    below it, which is how ``주요 내용 및 정책제안···ⅲ`` came to be part of the
    title of chapter 1's opening clause.
    """
    if not tokens:
        return False
    tail = tokens[-1]
    fused = _FUSED_ROMAN_TAIL.search(tail)
    if fused is not None:
        return _is_roman_folio(fused.group("page"))
    # A bare Roman tail counts only behind a dot leader. Without one, a title
    # ending on a word that happens to spell a numeral ("Part I") would be read
    # as a folio and the line dropped.
    return (
        len(tokens) > 2
        and _is_roman_folio(tail)
        and any(_LEADER.match(token) for token in tokens[:-1])
    )


def _is_division_header(text: str) -> bool:
    """A line that heads a division and carries no page number of its own.

    ``제1장 서론`` standing alone, with the chapter's clauses numbered beneath
    it, is an entry -- the topmost one -- and not the first line of a wrapped
    title. Read as a wrapped title it swallows the clause below it, and the two
    together are then dropped as a parent, so the chapter's opening clause
    vanishes from the manifest.
    """
    return _KO_STRUCT.match(text.strip()) is not None


def _mostly_object_labels(entries: list[_RawEntry]) -> bool:
    """Whether a page's rows name objects rather than divisions of the text."""
    labelled = sum(1 for entry in entries if _OBJECT_LABEL.match(entry.title))
    return labelled * 2 > len(entries)


def _page_entries(page) -> list[_RawEntry]:
    """Parse one TOC page's entries, merging wrapped title lines.

    A numberless line that sits in the left text column is held as a title
    prefix and merged into the next numbered line (whose page number belongs to
    the whole, wrapped title). The entry's indentation is taken from the first
    (shallowest) physical line, not the continuation.

    A division header (``제3장 국내 정책 동향 분석``) is the exception: it also
    carries no page number, but it is an entry rather than a prefix, and it
    takes the page of the first entry beneath it -- which is where the division
    begins. Opening one also discards whatever was pending, because a new
    division ends anything the page had been accumulating.
    """
    column_limit = page.rect.width * 0.45
    header_band = page.rect.height * 0.06
    out: list[_RawEntry] = []
    pending: str | None = None
    pending_x0: float | None = None
    header: str | None = None
    header_x0: float | None = None
    for line in _line_words(page):
        tokens = [w[4] for w in line]
        x0 = line[0][0]
        y0 = line[0][1]
        entry = _entry_from_tokens(tokens, x0)
        if entry is not None:
            if pending is not None:
                entry = _RawEntry(
                    x0=round(pending_x0, 1),
                    title=f"{pending} {entry.title}".strip(),
                    page=entry.page,
                )
            if header is not None:
                out.append(
                    _RawEntry(x0=round(header_x0, 1), title=header, page=entry.page)
                )
                header = None
                header_x0 = None
            out.append(entry)
            pending = None
            pending_x0 = None
            continue
        if _is_front_matter_row(tokens):
            pending = None
            pending_x0 = None
            continue
        text = " ".join(tokens).strip()
        is_fragment = (
            _HAS_WORD.search(text)
            and x0 < column_limit
            and y0 > header_band
            and not _is_header_fragment(text)
        )
        if not is_fragment:
            pending = None
            pending_x0 = None
            continue
        if _is_division_header(text):
            header = text
            header_x0 = x0
            pending = None
            pending_x0 = None
        elif header is not None and abs(x0 - header_x0) <= 2:
            # The header's own title wrapped: same indentation, no number.
            header = f"{header} {text}"
        else:
            pending = text if pending is None else f"{pending} {text}"
            if pending_x0 is None:
                pending_x0 = x0
    return out


def _looks_like_toc(page, min_entries: int) -> bool:
    entries = _page_entries(page)
    # A list of tables or figures is not a table of contents, however much it
    # looks like one: it is printed after the contents, under the same running
    # head, in the same two columns of title and page. What separates them is
    # what the rows name -- an object inside the text, or a division of it.
    if entries and _mostly_object_labels(entries):
        return False
    # A running head can share the top band with the contents title and sort
    # above it (a Korean report's series title sits beside "목 차" a fraction
    # of a point higher), so the header is looked for in the first few visual
    # lines rather than only the very first.
    for line in _line_words(page)[:3]:
        text = " ".join(w[4] for w in line).strip().lower()
        if any(text.startswith(h) for h in _TOC_HEADERS):
            return True
    return len(entries) >= min_entries


def find_toc_pages(doc, *, max_scan: int | None = 40, min_entries: int = 6) -> list[int]:
    """Return 0-based indices of pages that read as a table of contents.

    Scans the first ``max_scan`` pages (front-matter territory) and returns the
    contiguous run of TOC pages once one is found, so a multi-page contents
    section is captured while a lone page-number-heavy body page later on is not.
    """
    limit = doc.page_count if max_scan is None else min(max_scan, doc.page_count)
    found: list[int] = []
    for index in range(limit):
        if _looks_like_toc(doc[index], min_entries):
            found.append(index)
        elif found:
            break  # contiguous TOC run ended
    return found


def _tier_ranks(positions: list[float]) -> dict[float, int]:
    """Rank indentations left to right, treating near-equal ones as one tier."""
    ordered = sorted(set(positions))
    ranks: dict[float, int] = {}
    rank = 0
    previous: float | None = None
    for x0 in ordered:
        if previous is not None and x0 - previous > _TIER_TOLERANCE:
            rank += 1
        ranks[x0] = rank
        previous = x0
    return ranks


def _page_level_map(entries: list[_RawEntry]) -> dict[float, int]:
    """Map each indentation tier on a page to a hierarchy level.

    Tiers are ranked left to right and anchored to the shallowest numbered
    entry: a single-number ``N`` (depth 0) sits at level 1, so the part header
    to its left is level 0 and its ``N.M`` subsections are level 2. Anchoring on
    the number rather than the absolute x0 makes the levels immune to the
    recto/verso margin drift. Pages with no numbered entry fall back to tier
    rank.

    Entries within ``_TIER_TOLERANCE`` of each other are one tier. Typesetting
    puts the same tier down a fraction of a point apart -- three chapter
    headers on one page measured 99.0, 99.0 and 98.6 -- and read literally that
    jitter becomes a level, putting sibling chapters at different depths.
    """
    rank = _tier_ranks([entry.x0 for entry in entries])
    tiers = sorted(rank)
    anchor: tuple[int, int] | None = None  # (depth, rank)
    for entry in entries:
        depth = _num_depth(entry.title)
        if depth is None:
            continue
        candidate = (depth, rank[entry.x0])
        if anchor is None or candidate < anchor:
            anchor = candidate
    if anchor is None:
        return {x0: rank[x0] for x0 in tiers}
    anchor_depth, anchor_rank = anchor
    base = anchor_depth + 1  # chapter (depth 0) -> level 1, leaving room for parts
    return {x0: max(0, base + rank[x0] - anchor_rank) for x0 in tiers}


def _finalize(entries: list[TocEntry]) -> list[TocEntry]:
    """Normalize levels to a level-0 floor, fill parents, keep leaves."""
    if not entries:
        return []
    floor = min(entry.level for entry in entries)
    if floor:
        entries = [
            TocEntry(level=e.level - floor, title=e.title, page=e.page)
            for e in entries
        ]
    return mark_leaf_entries(fill_parents(entries))


def _read_toc_textlayer(
    pdf_path: Path,
    toc_pages: list[int] | None,
    max_scan_pages: int,
    min_entries: int,
) -> list[TocEntry]:
    """The text-layer path: word geometry off the printed contents page(s)."""
    fitz = _lazy_fitz()
    entries: list[TocEntry] = []
    with fitz.open(str(pdf_path)) as doc:
        if toc_pages is not None:
            indices = [p - 1 for p in toc_pages if 1 <= p <= doc.page_count]
        else:
            indices = find_toc_pages(
                doc, max_scan=max_scan_pages, min_entries=min_entries
            )
        for index in indices:
            page_entries = _page_entries(doc[index])
            levels = _page_level_map(page_entries)
            for entry in page_entries:
                entries.append(
                    TocEntry(level=levels[entry.x0], title=entry.title, page=entry.page)
                )
    return _finalize(entries)


def _ocr_entries(ocr_text: str) -> list[TocEntry]:
    """Parse TOC entries from an OCR transcript of one contents page.

    A serialized OCR page loses the x0 indentation, so the level comes from the
    dotted section number's depth (``10`` -> 0, ``10.2`` -> 1); un-numbered rows
    (parts, appendices) fall to level 0.
    """
    entries: list[TocEntry] = []
    for _line_type, line in _parse_lines(ocr_text):
        raw = _entry_from_tokens(line.split(), 0.0)
        if raw is None:
            continue
        depth = _num_depth(raw.title)
        entries.append(
            TocEntry(level=depth if depth is not None else 0, title=raw.title, page=raw.page)
        )
    return entries


def _ocr_page_is_toc(ocr_text: str, entries: list[TocEntry], min_entries: int) -> bool:
    for _line_type, line in _parse_lines(ocr_text):
        text = line.strip().lower()
        if text:
            if any(text.startswith(header) for header in _TOC_HEADERS):
                return True
            break  # inspect only the first non-empty line as a possible header
    return len(entries) >= min_entries


def _render_full_page(pdf_path: Path, page: int, dpi: int) -> bytes:
    return render_band(pdf_path, page, "full", 1.0, dpi)


def _read_toc_ocr(
    pdf_path: Path,
    client,
    *,
    toc_pages: list[int] | None,
    max_scan_pages: int,
    min_entries: int,
    dpi: int,
    render,
) -> list[TocEntry]:
    """The scanned-PDF fallback: OCR front-matter pages until the contents page
    is recognized, parse it, and stop once its contiguous run ends.

    The rendered images and transcripts are transient scaffolding: nothing is
    written to the lake. Only the recovered TOC (as the returned entries, and in
    turn the silver manifest) survives.
    """
    fitz = _lazy_fitz()
    with fitz.open(str(pdf_path)) as doc:
        page_count = doc.page_count
    if toc_pages is not None:
        candidates = [p for p in toc_pages if 1 <= p <= page_count]
    else:
        limit = min(max_scan_pages, page_count) if max_scan_pages else page_count
        candidates = list(range(1, limit + 1))

    entries: list[TocEntry] = []
    recognized = False
    for page in candidates:
        transcript = client.transcribe(render(pdf_path, page, dpi))
        page_entries = _ocr_entries(transcript)
        if _ocr_page_is_toc(transcript, page_entries, min_entries):
            recognized = True
            entries.extend(page_entries)
        elif recognized:
            break  # contents run ended; OCR no further ("until TOC is recognized")
    return _finalize(entries)


def read_page_toc(
    pdf_path: Path,
    *,
    toc_pages: list[int] | None = None,
    max_scan_pages: int = 40,
    min_entries: int = 6,
    ocr_client=None,
    ocr_dpi: int = 200,
    render=None,
) -> list[TocEntry]:
    """Reconstruct a TOC from the book's printed contents page(s).

    Tries the text layer first (word geometry). If the PDF has no text layer -- a
    scanned book -- and ``ocr_client`` is supplied, falls back to OCR: front
    matter is transcribed page by page only until the contents page is found,
    then discarded. ``toc_pages`` (1-based) pins the pages; otherwise they are
    located automatically. Returns leaf ``TocEntry`` rows with parents filled in,
    the same contract ``build_ranges`` consumes. Entry ``page`` is the printed
    number as it appears in the contents; pass ``--page-offset`` at ingest.
    """
    entries = _read_toc_textlayer(pdf_path, toc_pages, max_scan_pages, min_entries)
    if entries:
        return entries

    if ocr_client is None:
        raise ValueError(
            "No text-layer table-of-contents page found (a scanned PDF?). Supply "
            "--toc / --toc-from-outline, or enable the OCR fallback with a "
            "reachable --ocr-endpoint."
        )
    if not ocr_client.health():
        raise SystemExit(
            f"OCR endpoint not reachable at {ocr_client.endpoint}.\n"
            "Start a local OCR server (see README > Text vs Scanned PDFs), or "
            "supply --toc / --toc-from-outline instead."
        )
    entries = _read_toc_ocr(
        pdf_path,
        ocr_client,
        toc_pages=toc_pages,
        max_scan_pages=max_scan_pages,
        min_entries=min_entries,
        dpi=ocr_dpi,
        render=render or _render_full_page,
    )
    if not entries:
        raise ValueError(
            "OCR fallback scanned the front matter but found no contents page. "
            "Pin it with --toc-page N, or supply --toc / --toc-from-outline."
        )
    return entries
