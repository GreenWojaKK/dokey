"""Local OCR pipeline for recovering true printed page numbers (folios).

The book PDFs dokey ingests are almost never numbered so that PDF page N ==
printed page N: front matter, part dividers, and dropped blank leaves push the
two out of step, and the offset drifts across the book. When a lake is built
from a PDF outline (whose destinations are physical PDF pages), the manifest's
``content_*`` columns therefore hold PDF pages, not the book's printed folios.

This module reads the real printed folio off each page image with a local
vision-language OCR model served over an OpenAI-compatible HTTP endpoint
(e.g. llama.cpp's ``llama-server`` running a GGUF such as Unlimited-OCR on the
GPU). It renders only the header/footer band of each page, transcribes it, and
parses the folio, so it works even on scanned PDFs with no text layer.

Core (``dokey``) stays dependency-light; page rendering needs PyMuPDF, which
is declared as the optional ``ocr`` extra and imported lazily.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENDPOINT = "http://127.0.0.1:8731/v1/chat/completions"
OCR_PROMPT = "Transcribe all text in this image."

# The OCR model annotates each line as ``<type> [x0, y0, x1, y1]<text>``.
_LINE = re.compile(r"^\s*([A-Za-z_]+)\s*\[([-\d,\s]*)\]\s*(.*)$")
_PUNCT = ".,:;()[]{}·•"

# Confidence tiers for a folio candidate (lower is better).
_TIER_PAGE_NUMBER = 0  # model tagged the line as a page number
_TIER_ISOLATED = 1     # a line that is nothing but an integer
_TIER_EDGE = 2         # first/last token of a header/footer line


def _lazy_fitz():
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "The OCR pipeline needs PyMuPDF. Install the optional extra:\n"
            "  python -m pip install -e .[ocr]\n"
            "or\n"
            "  python -m pip install pymupdf"
        ) from exc
    return fitz


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_band(pdf_path: Path, page: int, where: str, frac: float, dpi: int = 200) -> bytes:
    """Render the top or bottom ``frac`` of a 1-indexed PDF page to PNG bytes."""
    fitz = _lazy_fitz()
    document = fitz.open(str(pdf_path))
    try:
        rect = document[page - 1].rect
        if where == "top":
            clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * frac)
        elif where == "bottom":
            clip = fitz.Rect(rect.x0, rect.y1 - rect.height * frac, rect.x1, rect.y1)
        else:
            clip = rect
        return document[page - 1].get_pixmap(dpi=dpi, clip=clip).tobytes("png")
    finally:
        document.close()


# --------------------------------------------------------------------------- #
# OCR client (OpenAI-compatible /v1/chat/completions with an image)
# --------------------------------------------------------------------------- #
class OcrClient:
    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 180.0,
        max_tokens: int = 160,
        retries: int = 2,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.retries = retries
        self.calls = 0  # number of transcribe() requests issued

    def health(self) -> bool:
        base = self.endpoint.split("/v1/", 1)[0]
        try:
            with urllib.request.urlopen(base + "/health", timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def transcribe(self, png_bytes: bytes) -> str:
        data_uri = "data:image/png;base64," + b64encode(png_bytes).decode()
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
        }
        body = json.dumps(payload).encode()
        self.calls += 1
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(
                self.endpoint, data=body, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    result = json.loads(resp.read())
                return result["choices"][0]["message"]["content"] or ""
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(1.0 + attempt)
        raise RuntimeError(f"OCR request failed after retries: {last_error}")


# --------------------------------------------------------------------------- #
# Folio parsing (pure functions)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FolioCandidate:
    value: int
    tier: int


def _int_token(token: str) -> int | None:
    stripped = token.strip(_PUNCT)
    if stripped.isdigit() and 1 <= len(stripped) <= 4:
        return int(stripped)
    return None


def _parse_lines(ocr_text: str):
    """Yield (line_type, text) pairs, stripping the model's bbox annotations."""
    for raw in ocr_text.splitlines():
        match = _LINE.match(raw)
        if match:
            yield match.group(1).lower(), match.group(3).strip()
        else:
            stripped = raw.strip()
            if stripped:
                yield "text", stripped


def folio_candidates(ocr_text: str, max_folio: int = 600) -> list[FolioCandidate]:
    """Extract plausible printed-folio integers from an OCR transcript."""
    found: list[FolioCandidate] = []
    for line_type, text in _parse_lines(ocr_text):
        tokens = text.split()
        if not tokens:
            continue
        if line_type == "page_number":
            for token in tokens:
                value = _int_token(token)
                if value is not None and value <= max_folio:
                    found.append(FolioCandidate(value, _TIER_PAGE_NUMBER))
            continue
        if len(tokens) == 1:
            value = _int_token(tokens[0])
            if value is not None and value <= max_folio:
                found.append(FolioCandidate(value, _TIER_ISOLATED))
                continue
        for token in {tokens[0], tokens[-1]}:
            value = _int_token(token)
            if value is not None and value <= max_folio:
                found.append(FolioCandidate(value, _TIER_EDGE))
    return found


def pick_folio(candidates: list[FolioCandidate], expected: int | None) -> int | None:
    """Choose the best candidate: highest confidence tier, then closest to
    ``expected`` (a running estimate from already-resolved pages)."""
    if not candidates:
        return None
    best_tier = min(candidate.tier for candidate in candidates)
    pool = [candidate.value for candidate in candidates if candidate.tier == best_tier]
    if expected is None:
        return min(pool)
    return min(pool, key=lambda value: (abs(value - expected), value))


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FolioResult:
    page: int
    folio: int | None
    source: str  # "ocr" | "interpolated" | "unresolved"


def read_folio(
    client: OcrClient,
    pdf_path: Path,
    page: int,
    expected: int | None,
    max_folio: int,
    dpi: int,
) -> int | None:
    """OCR the top band; if that yields nothing, consult the bottom band
    (chapter/part opening pages print the folio in the footer)."""
    candidates = folio_candidates(
        client.transcribe(render_band(pdf_path, page, "top", 0.20, dpi)), max_folio
    )
    if not candidates:
        candidates = folio_candidates(
            client.transcribe(render_band(pdf_path, page, "bottom", 0.24, dpi)), max_folio
        )
    return pick_folio(candidates, expected)


def build_folio_map(
    client: OcrClient,
    pdf_path: Path,
    pages: list[int],
    max_folio: int = 600,
    dpi: int = 200,
    cache: dict[int, int] | None = None,
    progress=None,
) -> dict[int, FolioResult]:
    """OCR each requested page in order, using resolved pages as an anchor, then
    interpolate any that could not be read."""
    results: dict[int, FolioResult] = {}
    cache = cache or {}
    last: tuple[int, int] | None = None
    for page in sorted(set(pages)):
        if page in cache:
            folio = cache[page]
            results[page] = FolioResult(page, folio, "ocr")
            last = (page, folio)
            if progress:
                progress(page, folio, "cache")
            continue
        expected = None if last is None else last[1] + (page - last[0])
        folio = read_folio(client, pdf_path, page, expected, max_folio, dpi)
        source = "ocr" if folio is not None else "unresolved"
        results[page] = FolioResult(page, folio, source)
        if folio is not None:
            last = (page, folio)
        if progress:
            progress(page, folio, source)
    _interpolate(results)
    return results


def _interpolate(results: dict[int, FolioResult]) -> None:
    pages = sorted(results)
    known = [p for p in pages if results[p].folio is not None]
    if not known:
        return
    for page in pages:
        if results[page].folio is not None:
            continue
        before = [p for p in known if p < page]
        after = [p for p in known if p > page]
        if before:
            anchor = before[-1]
            folio = results[anchor].folio + (page - anchor)
        else:
            anchor = after[0]
            folio = results[anchor].folio - (anchor - page)
        if folio >= 1:
            results[page] = FolioResult(page, folio, "interpolated")


def load_cache(path: Path) -> dict[int, int]:
    cache: dict[int, int] = {}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    if row.get("folio") is not None and row.get("source") == "ocr":
                        cache[int(row["page"])] = int(row["folio"])
    return cache


def save_folio_map(path: Path, results: dict[int, FolioResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for page in sorted(results):
            item = results[page]
            handle.write(
                json.dumps(
                    {"page": item.page, "folio": item.folio, "source": item.source}
                )
                + "\n"
            )


# --------------------------------------------------------------------------- #
# Offset calibration (cheap): model printed = pdf - offset as a piecewise-
# constant function and find its breakpoints by binary search, instead of
# OCR-ing every page.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OffsetSegment:
    start_page: int  # first PDF page governed by this offset
    offset: int      # printed folio = pdf_page - offset


@dataclass(frozen=True)
class OffsetModel:
    segments: tuple[OffsetSegment, ...]
    first_page: int  # first body PDF page the model covers
    last_page: int

    def offset_at(self, page: int) -> int | None:
        if page < self.first_page or page > self.last_page:
            return None
        chosen = self.segments[0].offset
        for segment in self.segments:
            if segment.start_page <= page:
                chosen = segment.offset
            else:
                break
        return chosen

    def folio_at(self, page: int) -> int | None:
        offset = self.offset_at(page)
        return None if offset is None else page - offset

    def to_dict(self) -> dict:
        return {
            "first_page": self.first_page,
            "last_page": self.last_page,
            "segments": [
                {"start_page": s.start_page, "offset": s.offset} for s in self.segments
            ],
        }


def _spiral(span: int):
    yield 0
    for delta in range(1, span + 1):
        yield delta
        yield -delta


def read_folio_near(
    client: OcrClient,
    pdf_path: Path,
    page: int,
    low: int,
    high: int,
    expected: int | None,
    max_folio: int,
    dpi: int,
    span: int = 3,
) -> tuple[int, int] | None:
    """Read a folio at ``page``; if that page has none (e.g. a figure-only or
    unreadable page), try immediate neighbours. Returns (page_used, folio)."""
    for delta in _spiral(span):
        probe = page + delta
        if low <= probe <= high:
            local_expected = None if expected is None else expected + (probe - page)
            folio = read_folio(client, pdf_path, probe, local_expected, max_folio, dpi)
            if folio is not None:
                return probe, folio
    return None


def confirmed_offset(
    client: OcrClient,
    pdf_path: Path,
    page: int,
    low: int,
    high: int,
    expected: int | None,
    max_folio: int,
    dpi: int,
    span: int = 2,
) -> tuple[int, int, int] | None:
    """Return (page_used, folio, offset) only when a page and its successor read
    as consecutive folios (slope-1). This single check rejects almost all
    isolated OCR misreads, since a garbage read rarely has a neighbour exactly
    one greater. Slides to nearby pages if ``page`` straddles a breakpoint."""
    for delta in _spiral(span):
        probe = page + delta
        if not (low <= probe < high):
            continue
        local_expected = None if expected is None else expected + (probe - page)
        first = read_folio(client, pdf_path, probe, local_expected, max_folio, dpi)
        if first is None:
            continue
        nxt = read_folio(client, pdf_path, probe + 1, first + 1, max_folio, dpi)
        if nxt == first + 1:
            return probe, first, probe - first
    return None


def _clean_samples(samples: dict[int, int], window: int = 2, tol: int = 3) -> dict[int, int]:
    """Drop samples that deviate from their local median offset by more than
    ``tol`` (residual outliers the slope-1 check let through)."""
    pages = sorted(samples)
    values = [samples[p] for p in pages]
    kept: dict[int, int] = {}
    for i, page in enumerate(pages):
        lo = max(0, i - window)
        hi = min(len(pages), i + window + 1)
        neighbourhood = sorted(values[lo:hi])
        median = neighbourhood[len(neighbourhood) // 2]
        if abs(values[i] - median) <= tol:
            kept[page] = samples[page]
    return kept


def detect_body_start(
    client: OcrClient,
    pdf_path: Path,
    total_pages: int,
    max_folio: int,
    dpi: int = 200,
    limit: int = 60,
) -> tuple[int, int] | None:
    """Find the first PDF page that carries an arabic body folio, confirmed by
    its successor being exactly one greater (slope-1 check). Returns
    (pdf_page, folio)."""
    upper = min(total_pages - 1, limit)
    for page in range(1, upper + 1):
        first = read_folio(client, pdf_path, page, None, max_folio, dpi)
        if first is None or first >= page:
            continue
        nxt = read_folio(client, pdf_path, page + 1, first + 1, max_folio, dpi)
        if nxt == first + 1:
            return page, first
    return None


def calibrate_offsets(
    client: OcrClient,
    pdf_path: Path,
    first_page: int,
    last_page: int,
    max_folio: int,
    dpi: int = 200,
    spotcheck_min: int = 16,
    log=None,
) -> OffsetModel:
    """Reconstruct the piecewise-constant offset function over
    [first_page, last_page] using O(breakpoints * log n) OCR reads.

    Each probe is a *confirmed* offset (slope-1 with its neighbour), so isolated
    OCR misreads are rejected rather than turned into fake breakpoints. Where two
    endpoints disagree the transition is bisected until pinned between adjacent
    pages; apparently constant segments are spot-checked past ``spotcheck_min``
    pages. A local-median pass then drops any residual outliers.
    """
    samples: dict[int, int] = {}

    def sample(page: int, hint_offset: int | None = None) -> int | None:
        if page in samples:
            return samples[page]
        expected = None if hint_offset is None else page - hint_offset
        got = confirmed_offset(
            client, pdf_path, page, first_page, last_page, expected, max_folio, dpi
        )
        if got is None:
            return None
        used, folio, offset = got
        samples[used] = offset
        if used != page:
            samples[page] = offset
        if log:
            log(page, used, folio, offset)
        return offset

    # Endpoints must be confirmable. Scan inward so a bad edge (e.g. the index,
    # whose dense number columns defeat OCR) does not abort calibration; the
    # model then simply does not cover those uncalibratable tail pages.
    eff_first = offset_first = None
    for page in range(first_page, min(first_page + 40, last_page)):
        offset = sample(page)
        if offset is not None:
            eff_first, offset_first = page, offset
            break
    eff_last = offset_last = None
    for page in range(last_page - 1, max(first_page, last_page - 80) - 1, -1):
        offset = sample(page)
        if offset is not None:
            eff_last, offset_last = page, offset
            break
    if eff_first is None or eff_last is None or eff_first >= eff_last:
        raise RuntimeError("Could not find confirmable calibration endpoints.")

    def recurse(lo: int, olo: int, hi: int, ohi: int) -> None:
        if hi - lo <= 1:
            return
        if olo == ohi:
            if hi - lo < spotcheck_min:
                return
            mid = (lo + hi) // 2
            omid = sample(mid, olo)
            if omid is None or omid == olo:
                return
            recurse(lo, olo, mid, omid)
            recurse(mid, omid, hi, ohi)
            return
        mid = (lo + hi) // 2
        omid = sample(mid, olo)
        if omid is None:
            omid = olo
        recurse(lo, olo, mid, omid)
        recurse(mid, omid, hi, ohi)

    recurse(eff_first, offset_first, eff_last, offset_last)

    samples = {p: o for p, o in _clean_samples(samples).items() if eff_first <= p <= eff_last}
    if not samples:
        raise RuntimeError("No offset samples survived outlier filtering.")

    segments: list[OffsetSegment] = []
    for page in sorted(samples):
        offset = samples[page]
        if not segments or segments[-1].offset != offset:
            segments.append(OffsetSegment(page, offset))
    segments[0] = OffsetSegment(eff_first, segments[0].offset)
    return OffsetModel(tuple(segments), eff_first, eff_last)


def verify_model(
    client: OcrClient,
    pdf_path: Path,
    model: OffsetModel,
    sample_pages: list[int],
    max_folio: int,
    dpi: int = 200,
) -> list[tuple[int, int, int]]:
    """Spot-check the model against fresh OCR reads. Returns
    (page, predicted_folio, ocr_folio) for each disagreement."""
    mismatches = []
    for page in sample_pages:
        predicted = model.folio_at(page)
        if predicted is None:
            continue
        actual = read_folio(client, pdf_path, page, predicted, max_folio, dpi)
        if actual is not None and actual != predicted:
            mismatches.append((page, predicted, actual))
    return mismatches
