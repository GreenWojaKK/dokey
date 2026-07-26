from __future__ import annotations

import shutil
import json
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:  # pragma: no cover - compatibility fallback
    try:
        from PyPDF2 import PdfReader, PdfWriter
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Missing PDF dependency. Install one of these first:\n"
            "  pip install pypdf\n"
            "or\n"
            "  pip install PyPDF2"
        ) from exc

from .models import SectionRange


def open_reader(path: Path) -> PdfReader:
    if not path.exists():
        raise FileNotFoundError(f"Input PDF not found: {path}")
    return PdfReader(str(path))


def copy_raw_pdf(input_path: Path, output_dir: Path) -> Path:
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / input_path.name
    shutil.copy2(input_path, target)
    return target


def write_pages_jsonl(reader: PdfReader, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            output.write(json.dumps({"page": index, "text": text}, ensure_ascii=False) + "\n")


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
