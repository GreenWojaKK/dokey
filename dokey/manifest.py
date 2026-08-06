from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from .models import SectionRange, TocEntry


def write_toc(output_dir: Path, entries: list[TocEntry]) -> Path:
    """Persist the table of contents the ingest worked from.

    Every path into a lake starts by establishing the document's outline -- an
    embedded PDF outline, a printed contents page, or (for a render) a sweep of
    its own headings -- and until now that outline existed only in memory,
    leaving the manifest's sections with no record of where their boundaries
    came from. Writing it makes the outline inspectable: a reader can see the
    document as the ingest saw it before any splitting happened.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "toc.jsonl"
    with path.open("w", encoding="utf-8") as output:
        for entry in entries:
            output.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    return path


def write_manifests(output_dir: Path, ranges: list[SectionRange]) -> tuple[Path, Path, Path]:
    if not ranges:
        raise ValueError("No section ranges to write.")
    return write_manifest_rows(output_dir, [asdict(item) for item in ranges])


def write_manifest_rows(output_dir: Path, rows: list[dict]) -> tuple[Path, Path, Path]:
    """Write the manifest triple (csv/json/jsonl) from plain dict rows.

    Used both by fresh ingests and by post-processing steps (e.g. folio
    recovery) that augment an existing manifest. All rows must share one key
    order; the first row defines the CSV column order.
    """
    if not rows:
        raise ValueError("No section rows to write.")

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "sections.csv"
    json_path = output_dir / "sections.json"
    jsonl_path = output_dir / "sections.jsonl"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with jsonl_path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")

    return csv_path, json_path, jsonl_path
