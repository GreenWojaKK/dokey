"""Estimate the ingest ``--page-offset`` from the PDF itself, model-free.

A printed table of contents references *printed* pages while ``build_ranges``
needs PDF pages; the difference is the ``--page-offset`` flag, and supplying
it is the least intuitive step of a manual ingest — the user must open the
PDF, find where printed page 1 falls, and count. For a text-layer PDF the
offset is recoverable from the document alone, lexically:

* Most body pages carry their printed folio in the running header or footer,
  which survives in the text layer as a bare integer token on the first or
  last visual line of the page. Each such token votes
  ``offset = pdf_page - folio``; the modal offset over sampled pages is the
  estimate, and its vote share the confidence. A constant offset dominates
  the histogram even when tables and captions contribute stray integers.
* As a cross-check, a few TOC entry titles are searched on the pages where
  the winning offset predicts them (entry page + offset, +/- 1 for part
  dividers). A title found where predicted confirms the estimate.

The estimate is only a *prior*: real books drift, because plates, part
dividers, and unnumbered leaves push the body out of step mid-volume.
``pin_section_starts`` therefore smoke-tests every section — it reads the
page where the running offset predicts the section to start, spirals nearby
until the section's own title is found, and pins the section to that
physical page. Each pin updates the running offset, so cumulative drift is
tracked section by section; sections whose title cannot be found are
interpolated from their neighbors and reported, never silently misplaced.
When a document has no text-layer folios at all, ``title_scan_offset``
recovers the prior by locating the first TOC titles in the body directly.

This is deliberately dependency-light and local: word geometry via PyMuPDF
(the optional ``ocr`` extra), no OCR, no model. Scanned PDFs expose no text
layer to read; there the OCR-based ``dokey folios`` pipeline applies
instead.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .models import TocEntry
from .tocpage import _lazy_fitz, _line_words

_FOLIO_TOKEN = re.compile(r"^\d{1,4}$")
# Offsets outside this band are treated as stray integers, not folios.
_MIN_OFFSET = -5
_MAX_OFFSET = 400


@dataclass(frozen=True)
class OffsetEstimate:
    offset: int | None
    votes: int  # sampled pages voting for the winning offset
    sampled: int  # sampled pages that yielded any folio candidate
    confirmed_titles: int  # TOC titles found where the offset predicts them
    checked_titles: int

    @property
    def confident(self) -> bool:
        """True when the folio vote is decisive or titles corroborate it."""
        if self.offset is None:
            return False
        decisive = self.votes >= 5 and self.votes * 2 > self.sampled
        corroborated = self.checked_titles > 0 and (
            self.confirmed_titles * 2 >= self.checked_titles
        )
        return decisive or (self.votes >= 3 and corroborated)


def _page_offset_votes(page, page_number: int, page_count: int) -> set[int]:
    """Distinct plausible offsets suggested by one page's header/footer.

    Only the first and last visual lines are inspected — that is where a
    running folio lives — and each distinct offset counts once per page so a
    number-heavy table row cannot outvote the folios.
    """
    lines = _line_words(page)
    if not lines:
        return set()
    votes: set[int] = set()
    inspect = [lines[0]] if len(lines) == 1 else [lines[0], lines[-1]]
    for line in inspect:
        for word in line:
            if not _FOLIO_TOKEN.match(word[4]):
                continue
            folio = int(word[4])
            if not 1 <= folio <= page_count:
                continue
            offset = page_number - folio
            if _MIN_OFFSET <= offset <= _MAX_OFFSET:
                votes.add(offset)
    return votes


# TOC and body renditions of one heading routinely disagree on glyph choice:
# the interpunct comes in half a dozen codepoints (법‧제도적 vs 법·제도적),
# hyphens and dashes vary, and parentheses may be fullwidth. Folding the
# variants keeps such pairs equal after squashing.
_GLYPH_VARIANTS = str.maketrans({
    # interpunct family -> U+00B7
    "‧": "·", "・": "·", "･": "·",
    "ㆍ": "·", "•": "·", "∙": "·",
    "⋅": "·",
    # hyphen/dash family -> ASCII hyphen
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    # fullwidth punctuation -> ASCII
    "（": "(", "）": ")", "［": "[", "］": "]",
    "：": ":", "，": ",", "．": ".",
})


def _squash(text: str) -> str:
    """Whitespace-free, glyph-folded form: tokenization, wrapping, and
    typographic variant differences all vanish."""
    return re.sub(r"\s+", "", text).translate(_GLYPH_VARIANTS)


def _confirm_titles(
    doc,
    entries: list[TocEntry],
    offset: int,
    *,
    max_titles: int = 8,
) -> tuple[int, int]:
    """Count TOC titles found on the page the offset predicts (+/- 1)."""
    usable = [e for e in entries if len(_squash(e.title)) >= 4]
    if not usable:
        return 0, 0
    step = max(1, len(usable) // max_titles)
    chosen = usable[::step][:max_titles]
    confirmed = 0
    for entry in chosen:
        target = entry.page + offset
        needle = _squash(entry.title)
        found = False
        for page_number in (target, target - 1, target + 1):
            if not 1 <= page_number <= doc.page_count:
                continue
            if needle in _squash(doc[page_number - 1].get_text()):
                found = True
                break
        if found:
            confirmed += 1
    return confirmed, len(chosen)


def estimate_page_offset(
    pdf_path: Path,
    entries: list[TocEntry] | None = None,
    *,
    max_samples: int = 80,
) -> OffsetEstimate:
    """Estimate ``--page-offset`` for a text-layer PDF.

    Samples up to ``max_samples`` pages spread over the document, collects
    folio votes, and (when ``entries`` are given) cross-checks the winner by
    locating TOC titles where it predicts them. ``offset`` is ``None`` when no
    page yields a folio candidate — typically a scanned PDF.
    """
    fitz = _lazy_fitz()
    histogram: Counter[int] = Counter()
    sampled = 0
    with fitz.open(str(pdf_path)) as doc:
        page_count = doc.page_count
        step = max(1, page_count // max_samples)
        for index in range(0, page_count, step):
            votes = _page_offset_votes(doc[index], index + 1, page_count)
            if votes:
                sampled += 1
                histogram.update(votes)
        if not histogram:
            return OffsetEstimate(
                offset=None, votes=0, sampled=0, confirmed_titles=0, checked_titles=0
            )
        offset, votes = histogram.most_common(1)[0]
        confirmed, checked = (
            _confirm_titles(doc, entries, offset) if entries else (0, 0)
        )
    return OffsetEstimate(
        offset=offset,
        votes=votes,
        sampled=sampled,
        confirmed_titles=confirmed,
        checked_titles=checked,
    )


@dataclass(frozen=True)
class SectionCheck:
    title: str
    printed_page: int
    predicted_pdf_page: int
    found_pdf_page: int | None
    # Whether the section heading opens a fresh page (no body text above it)
    # on its start page. None when the section was not located.
    clean_start: bool | None = None

    @property
    def status(self) -> str:
        if self.found_pdf_page is None:
            return "unresolved"
        if self.found_pdf_page == self.predicted_pdf_page:
            return "verified"
        return "corrected"


# A section start is "clean" when no body-text line precedes its heading on the
# start page. Lines longer than this many squashed characters are body text; a
# running header, a chapter-title block, and the heading itself all stay under
# it, so only a genuine mid-page break (a preceding paragraph) trips it.
_BODY_LINE_CHARS = 25
# The fraction of located sections that must start cleanly before a document is
# judged clean-breaking (overlap 0). Kept high so a mostly-mid-page document,
# or a mixed one, stays on the safe overlap-1 default.
_CLEAN_BREAK_RATIO = 0.7
# Too few located sections to read the document's sectioning style from.
_MIN_CLEAN_SAMPLE = 3


@dataclass(frozen=True)
class SmokeReport:
    checks: tuple[SectionCheck, ...]

    def count(self, status: str) -> int:
        return sum(1 for check in self.checks if check.status == status)

    @property
    def verified(self) -> int:
        return self.count("verified")

    @property
    def corrected(self) -> int:
        return self.count("corrected")

    @property
    def unresolved(self) -> int:
        return self.count("unresolved")

    @property
    def located(self) -> int:
        return sum(1 for check in self.checks if check.found_pdf_page is not None)

    @property
    def clean_starts(self) -> int:
        return sum(1 for check in self.checks if check.clean_start)

    @property
    def clean_sample(self) -> int:
        """Located sections whose start page could be read for cleanliness."""
        return sum(1 for check in self.checks if check.clean_start is not None)

    @property
    def clean_breaking(self) -> bool | None:
        """Whether sections begin on fresh pages document-wide.

        None when too few sections were located to tell; otherwise True when
        at least ``_CLEAN_BREAK_RATIO`` of located sections start cleanly.
        """
        if self.clean_sample < _MIN_CLEAN_SAMPLE:
            return None
        return self.clean_starts / self.clean_sample >= _CLEAN_BREAK_RATIO

    def recommended_overlap(self) -> int:
        """0 for a clean-breaking document, else the safe default 1."""
        return 0 if self.clean_breaking else 1


class _PageTexts:
    """Lazily squashed page texts with a cache, 1-based pages."""

    def __init__(self, doc):
        self._doc = doc
        self._cache: dict[int, str] = {}
        self.page_count = doc.page_count

    def __call__(self, page_number: int) -> str:
        if page_number not in self._cache:
            self._cache[page_number] = _squash(self._doc[page_number - 1].get_text())
        return self._cache[page_number]


def _title_marker(title: str) -> str | None:
    """The title's structural marker ("제2절", "10.2"), squashed, else None."""
    from .tocpage import _KO_STRUCT, _SECTION_NO

    match = _KO_STRUCT.match(title) or _SECTION_NO.match(title)
    return _squash(match.group(0)) if match else None


# How much of a page's squashed head the marker-only tier may match within: a
# section-start page opens with its heading right after the running head, so a
# marker deeper in the page is a cross-reference, not a heading.
_MARKER_HEAD_CHARS = 100


def _needle_tiers(title: str) -> list[tuple[str, int | None]]:
    """Matching needles for one title, strongest first.

    Tier 1 is the full squashed title. Tier 2 is a 10-character prefix, for a
    TOC that abbreviates the body heading's tail ("종합분석(PEST)" vs
    "종합분석"). Tier 3 is the structural marker alone ("제2절"), restricted
    to the page head, for a TOC whose wording was revised away from the body
    heading entirely; it must carry a digit so it stays specific.
    """
    full = _squash(title)
    tiers: list[tuple[str, int | None]] = []
    if len(full) >= 4:
        tiers.append((full, None))
    if len(full) > 12:
        tiers.append((full[:10], None))
    marker = _title_marker(title)
    if marker and any(ch.isdigit() for ch in marker):
        tiers.append((marker, _MARKER_HEAD_CHARS))
    return tiers


def _is_clean_start(page, title: str) -> bool | None:
    """Does this section's heading open a fresh page, with no body above it?

    Reads the start page's visual lines top to bottom. The running header (top
    band) is skipped; a chapter-title block and the heading itself are short
    lines that pass; the first body-length line reached before the heading
    means the previous section's text flows onto this page — a mid-page break.
    Returns None when the heading cannot be located (no usable needle).
    """
    from .tocpage import _line_words

    needles = [needle for needle, _ in _needle_tiers(title)]
    if not needles:
        return None
    header_band = page.rect.height * 0.08
    for line in _line_words(page):
        y0 = min(word[1] for word in line)
        text = _squash(" ".join(word[4] for word in line))
        if any(text.startswith(needle) for needle in needles):
            return True  # reached the heading with no body line above it
        if y0 <= header_band:
            continue  # running header
        if len(text) > _BODY_LINE_CHARS:
            return False  # previous section's body precedes the heading
    return None


def _contains(texts: _PageTexts, page_number: int, needle: str, head_limit: int | None) -> bool:
    haystack = texts(page_number)
    if head_limit is not None:
        haystack = haystack[:head_limit]
    return needle in haystack


# A page containing this many distinct section titles is a chapter divider or
# a contents-like page, not a section start.
_DIVIDER_TITLE_COUNT = 3


def _looks_like_divider(texts: _PageTexts, page_number: int, all_titles: list[str]) -> bool:
    """True for chapter-divider pages, which list several section titles.

    A Korean report's part divider carries the chapter's full section roster;
    matching a section start against it would pin the section to the divider
    instead of its first page.
    """
    haystack = texts(page_number)
    hits = 0
    for title in all_titles:
        if title and title in haystack:
            hits += 1
            if hits >= _DIVIDER_TITLE_COUNT:
                return True
    return False


def _find_section_start(
    texts: _PageTexts,
    title: str,
    predicted: int,
    *,
    floor: int,
    window: int,
    exclude: set[int],
    all_titles: list[str],
) -> int | None:
    """Locate the physical page a section starts on, searching near the guess.

    Needle tiers are tried strongest-first, each over all pages nearest-first
    around ``predicted``, so an exact title hit two pages away outranks a
    marker-only hit on the predicted page. A hit is walked back over the
    contiguous run of pages containing the needle, because a layout whose
    running head repeats the section title matches on every page of the
    section — the run's first page is the section start. ``floor`` (the
    previous section's pin) keeps the search monotonic; ``exclude`` keeps the
    contents page itself from matching, and divider-like pages (several
    section titles at once) never count as a start.
    """
    deltas = sorted(range(-window, window + 1), key=lambda d: (abs(d), d))
    for needle, head_limit in _needle_tiers(title):
        for delta in deltas:
            page_number = predicted + delta
            if page_number < max(1, floor) or page_number > texts.page_count:
                continue
            if page_number in exclude:
                continue
            if not _contains(texts, page_number, needle, head_limit):
                continue
            if _looks_like_divider(texts, page_number, all_titles):
                continue
            while (
                page_number - 1 >= max(1, floor)
                and page_number - 1 not in exclude
                and _contains(texts, page_number - 1, needle, head_limit)
                and not _looks_like_divider(texts, page_number - 1, all_titles)
            ):
                page_number -= 1
            return page_number
    return None


def pin_section_starts(
    pdf_path: Path,
    entries: list[TocEntry],
    offset: int,
    *,
    window: int = 8,
) -> tuple[list[TocEntry], SmokeReport]:
    """Smoke-test every section start and pin the drift away.

    Walks the entries in printed order, predicting each section's physical
    page from the *running* offset (the prior ``offset`` until the first pin,
    then whatever the latest verified section implies), and searching for the
    section title nearby. Verified and corrected sections are pinned to the
    page the title was found on; unresolved sections are interpolated from
    the nearest resolved neighbor's offset. Returns the entries (sorted by
    printed page) with ``pdf_page`` pinned, plus the per-section report.

    When not a single title can be located — a scanned PDF, or a TOC whose
    wording differs from the body headings — the entries are returned
    unpinned so the constant-offset arithmetic stays in charge, and the
    report shows every section as unresolved.
    """
    fitz = _lazy_fitz()
    from .tocpage import find_toc_pages

    ordered = sorted(entries, key=lambda entry: entry.page)
    checks: list[SectionCheck] = []
    pins: list[int | None] = []
    with fitz.open(str(pdf_path)) as doc:
        exclude = {index + 1 for index in find_toc_pages(doc)}
        texts = _PageTexts(doc)
        all_titles = [_squash(entry.title) for entry in ordered]
        running = offset
        floor = 0
        for entry in ordered:
            predicted = entry.page + running
            found = _find_section_start(
                texts,
                entry.title,
                predicted,
                floor=floor,
                window=window,
                exclude=exclude,
                all_titles=all_titles,
            )
            clean = (
                _is_clean_start(doc[found - 1], entry.title)
                if found is not None
                else None
            )
            checks.append(
                SectionCheck(
                    title=entry.title,
                    printed_page=entry.page,
                    predicted_pdf_page=predicted,
                    found_pdf_page=found,
                    clean_start=clean,
                )
            )
            pins.append(found)
            if found is not None:
                running = found - entry.page
                floor = found

    resolved = [index for index, pin in enumerate(pins) if pin is not None]
    if not resolved:
        return ordered, SmokeReport(checks=tuple(checks))

    # Interpolate unresolved sections from the nearest resolved neighbor's
    # offset, then clamp to keep the sequence monotonic.
    for index, pin in enumerate(pins):
        if pin is not None:
            continue
        nearest = min(resolved, key=lambda r: abs(r - index))
        neighbor_offset = pins[nearest] - ordered[nearest].page
        pins[index] = ordered[index].page + neighbor_offset
    floor = 1
    for index, pin in enumerate(pins):
        pins[index] = max(pin, floor)
        floor = pins[index]

    pinned = [
        TocEntry(
            level=entry.level,
            title=entry.title,
            page=entry.page,
            parent=entry.parent,
            pdf_page=pin,
        )
        for entry, pin in zip(ordered, pins)
    ]
    return pinned, SmokeReport(checks=tuple(checks))


def title_scan_offset(
    pdf_path: Path,
    entries: list[TocEntry],
    *,
    max_titles: int = 4,
) -> int | None:
    """Recover an offset prior by locating the first TOC titles in the body.

    The fallback for documents whose pages carry no text-layer folio: each
    chosen title's first occurrence outside the contents page votes
    ``offset = found page - printed page``, and the modal offset wins.
    """
    fitz = _lazy_fitz()
    from .tocpage import find_toc_pages

    chosen = [entry for entry in entries if len(_squash(entry.title)) >= 6][:max_titles]
    if not chosen:
        return None
    votes: Counter[int] = Counter()
    with fitz.open(str(pdf_path)) as doc:
        exclude = {index + 1 for index in find_toc_pages(doc)}
        texts = _PageTexts(doc)
        all_titles = [_squash(entry.title) for entry in entries]
        for entry in chosen:
            needle = _squash(entry.title)
            for page_number in range(1, texts.page_count + 1):
                if page_number in exclude:
                    continue
                if needle in texts(page_number):
                    # A chapter divider lists several section titles; the
                    # section's true first page lies beyond it.
                    if _looks_like_divider(texts, page_number, all_titles):
                        continue
                    candidate = page_number - entry.page
                    if _MIN_OFFSET <= candidate <= _MAX_OFFSET:
                        votes[candidate] += 1
                    break
    if not votes:
        return None
    return votes.most_common(1)[0][0]
