"""Route a PDF to a text-layer or an OCR ingestion path.

Not every PDF carries a usable text layer. Typeset and digital-first
documents do; a scan is a stack of page images with little or no
extractable text, and feeding it to ``page.extract_text()`` yields empty
sections. Before ingesting, ``probe`` measures how much real text each page
holds and whether the page is essentially a full-page image, then reports
whether the document should take the text path or be sent to OCR first.

The idea is ported from an upstream ingestion pipeline, which classified a
corpus of Korean technical standards with the document-level rule
``mean_chars < 150 and images > 300``.
That constant is tuned for long documents (one scan image per page over
hundreds of pages) and misfires on short ones, so here the primary signal
is per-page: a page is treated as scanned when it has near-zero extractable
text while carrying an image, and the document verdict follows the fraction of
such pages (with mean-chars kept as a corroborating signal).

Page rendering is not needed to probe, but the image inventory is read through
PyMuPDF, declared as the optional ``ocr`` extra and imported lazily so the core
stays dependency-light.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def _lazy_fitz():
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "Probing a PDF for a text layer needs PyMuPDF. Install the optional extra:\n"
            "  python -m pip install -e .[ocr]\n"
            "or\n"
            "  python -m pip install pymupdf"
        ) from exc
    return fitz


# A document is treated as scanned when at least this fraction of its pages are
# images with no text on them. Shared with the CLI, which uses the same evidence
# to decide whether to hand the document to a converter.
SCAN_RATIO_DEFAULT = 0.5


@dataclass(frozen=True)
class PageProbe:
    page: int  # 1-based
    chars: int
    images: int
    is_image: bool


@dataclass(frozen=True)
class PdfProbe:
    path: str
    pages: int
    total_chars: int
    total_images: int
    mean_chars: float
    scanned_pages: tuple[int, ...]
    scanned_ratio: float
    method: str  # "text" | "ocr"
    page_probes: tuple[PageProbe, ...]

    @property
    def needs_ocr(self) -> bool:
        return self.method == "ocr"


def probe_pdf(
    path: Path,
    *,
    min_page_chars: int = 20,
    min_mean_chars: int = 150,
    scan_ratio: float = SCAN_RATIO_DEFAULT,
) -> PdfProbe:
    """Classify a PDF as a text-layer or a scanned (OCR-needed) document.

    A page is counted as scanned when it yields fewer than ``min_page_chars``
    extractable characters yet carries at least one image. The document routes
    to OCR when the scanned fraction reaches ``scan_ratio``, or when the mean
    extractable characters per page falls below ``min_mean_chars`` (which
    catches image-only pages that expose no image XObject).
    """
    fitz = _lazy_fitz()
    probes: list[PageProbe] = []
    total_chars = 0
    total_images = 0
    with fitz.open(str(path)) as doc:
        for index, page in enumerate(doc, start=1):
            chars = len(page.get_text().strip())
            images = len(page.get_images(full=True))
            total_chars += chars
            total_images += images
            is_image = chars < min_page_chars and images >= 1
            probes.append(PageProbe(index, chars, images, is_image))

    pages = len(probes)
    mean_chars = total_chars / pages if pages else 0.0
    scanned = tuple(p.page for p in probes if p.is_image)
    scanned_ratio = len(scanned) / pages if pages else 0.0
    method = (
        "ocr"
        if pages and (scanned_ratio >= scan_ratio or mean_chars < min_mean_chars)
        else "text"
    )
    return PdfProbe(
        path=str(path),
        pages=pages,
        total_chars=total_chars,
        total_images=total_images,
        mean_chars=mean_chars,
        scanned_pages=scanned,
        scanned_ratio=scanned_ratio,
        method=method,
        page_probes=tuple(probes),
    )


def format_probe(probe: PdfProbe, *, max_listed: int = 20) -> str:
    """Render a human-readable one-block summary of a probe result."""
    lines = [
        f"{probe.path}",
        f"  pages: {probe.pages}  mean chars/page: {probe.mean_chars:.0f}"
        f"  total images: {probe.total_images}",
        f"  scanned pages: {len(probe.scanned_pages)}"
        f" ({probe.scanned_ratio * 100:.0f}%)",
        f"  route: {probe.method.upper()}"
        + (
            "  -> extract the text layer"
            if probe.method == "text"
            else "  -> render pages and OCR (see `dokey folios --source ocr` / README)"
        ),
    ]
    if probe.scanned_pages:
        shown = ", ".join(str(p) for p in probe.scanned_pages[:max_listed])
        if len(probe.scanned_pages) > max_listed:
            shown += ", ..."
        lines.append(f"  scanned page numbers: {shown}")
    if probe.method == "text" and probe.scanned_pages:
        lines.append(
            f"  note: {len(probe.scanned_pages)} page(s) look scanned inside an "
            "otherwise-text document; those sections may extract empty."
        )
    return "\n".join(lines)
