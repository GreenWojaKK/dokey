from __future__ import annotations

import shutil
import json
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError as pypdf_error:  # pragma: no cover - compatibility fallback
    try:
        from PyPDF2 import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover
        # The pypdf failure is the interesting one: pypdf can be present and
        # still fail to import (a broken optional backend, a frozen build
        # missing a piece), and hiding that behind "install pypdf" once cost
        # a debugging session. Say what actually went wrong.
        raise SystemExit(
            "Missing PDF dependency. Install one of these first:\n"
            "  pip install pypdf\n"
            "or\n"
            "  pip install PyPDF2\n"
            f"(pypdf import failed with: {pypdf_error!r})"
        ) from exc

from .models import SectionRange


def open_reader(path: Path) -> PdfReader:
    if not path.exists():
        raise FileNotFoundError(f"Input PDF not found: {path}")
    return PdfReader(str(path))


def copy_source_document(input_path: Path, output_dir: Path) -> Path:
    """Keep the source document in the lake, under its own name.

    The lake is flat, so the original sits at the root beside what was read
    out of it -- opening the folder says at once which document it holds. A
    re-ingest pointed at that very copy is left alone rather than copied
    onto itself.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / input_path.name
    if target.exists() and target.resolve() == input_path.resolve():
        return target
    shutil.copy2(input_path, target)
    return target


def page_texts(reader: PdfReader) -> list[str]:
    """Every page's text, extracted once.

    Two writers want the same read -- the page stream and the per-
    section Markdown -- and on a long document extracting twice is the most
    expensive thing an ingest could do for nothing.
    """
    return [(page.extract_text() or "") for page in reader.pages]


def write_pages_jsonl(
    reader: PdfReader, output_path: Path, texts: list[str] | None = None
) -> None:
    if texts is None:
        texts = page_texts(reader)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for index, text in enumerate(texts, start=1):
            output.write(json.dumps({"page": index, "text": text}, ensure_ascii=False) + "\n")


def write_section_markdown(ranges: list[SectionRange], texts: list[str]) -> None:
    """Per-section Markdown beside the split PDFs, cut from the text layer.

    The same artifact a flow document gets, for a source that states pages:
    each section's own pages, joined, under its title. It is written from the
    text dokey already read, so it needs no converter and costs no second
    pass -- and where a converter did read the document, the render it
    produced is what the sections were cut from in the first place.
    """
    for item in ranges:
        pages = texts[item.pdf_start_page - 1 : item.pdf_end_page]
        body = "\n\n".join(page.strip() for page in pages if page.strip())
        # A top-level section is its own parent; anything else sits one rung
        # below the section it belongs to.
        heading = "#" * (1 if item.parent == item.title else 2) + " " + item.title
        out_path = Path(item.output_file).with_suffix(".md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            f"{heading}\n\n{body}\n" if body else f"{heading}\n", encoding="utf-8"
        )


def write_split_pdfs(reader: PdfReader, ranges: list[SectionRange]) -> None:
    for item in ranges:
        writer = PdfWriter()
        add_page_range(reader, writer, item.pdf_start_page, item.pdf_end_page)
        output_path = Path(item.output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as output:
            writer.write(output)


def add_page_range(
    reader: PdfReader,
    writer: PdfWriter,
    start_page: int,
    end_page: int,
) -> None:
    total_pages = len(reader.pages)
    if start_page < 1 or end_page > total_pages or start_page > end_page:
        raise ValueError(
            f"Invalid PDF page range {start_page}-{end_page}; "
            f"source has {total_pages} pages."
        )

    for page_number in range(start_page, end_page + 1):
        writer.add_page(reader.pages[page_number - 1])
