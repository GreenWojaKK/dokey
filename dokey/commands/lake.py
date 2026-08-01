"""Shared lake-writing pipelines used by command handlers."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import blocks as blockslib
from .. import docname as docnamelib
from .. import figures as figureslib
from .. import mdunit
from .. import mentions as mentionslib
from .. import paths as pathslib
from .. import profiles as profileslib
from .. import search as searchlib
from ..manifest import write_manifests, write_toc
from ..models import TocEntry
from ..pdf import copy_raw_pdf, write_pages_jsonl, write_split_pdfs
from ..ranges import build_ranges


def _reset_section_artifacts(output_dir: Path) -> None:
    """Clear the per-section artifact tree before a (re-)ingest writes into it.

    Artifact filenames are stable, so a re-ingest overwrites same-named files;
    but a section that was removed or renamed would otherwise leave an orphan
    behind. Wiping ``artifacts/by_section`` first keeps the tree an exact mirror
    of the current manifest. The manifests, page text, and index are all
    rewritten (or atomically replaced) in place, so only this tree accumulates.
    """
    by_section = output_dir / "artifacts" / "by_section"
    if by_section.exists():
        shutil.rmtree(by_section)


def ingest_entries(
    reader,
    entries: list[TocEntry],
    *,
    input_path: Path,
    output_dir: Path,
    page_offset: int,
    max_content_page: int | None,
    section_overlap: int | None,
    no_raw_copy: bool = False,
    no_page_text: bool = False,
    no_pdf_artifacts: bool = False,
) -> int:
    """The post-TOC ingest pipeline, shared by ``ingest`` and ``auto``.

    Builds the section ranges and writes the lake outputs; returns the number
    of ingested sections.
    """
    ranges = build_ranges(
        entries=entries,
        output_dir=output_dir,
        total_pdf_pages=len(reader.pages),
        pdf_page_offset=page_offset,
        max_content_page=max_content_page,
        section_overlap=section_overlap,
    )
    if not ranges:
        raise ValueError("No ranges generated. Check TOC pages and page offset.")

    output_dir.mkdir(parents=True, exist_ok=True)

    if not no_raw_copy:
        raw_path = copy_raw_pdf(input_path, output_dir)
        print(f"Wrote raw PDF: {raw_path}")

    if not no_page_text:
        pages_path = output_dir / "bronze" / "pages.jsonl"
        write_pages_jsonl(reader, pages_path)
        print(f"Wrote page text: {pages_path}")

    toc_path = write_toc(output_dir, entries)
    print(f"Wrote table of contents: {toc_path} ({len(entries)} entries)")

    _write_document_name(output_dir, input_path)

    csv_path, json_path, jsonl_path = write_manifests(output_dir, ranges)
    print(f"Wrote section CSV: {csv_path}")
    print(f"Wrote section JSON: {json_path}")
    print(f"Wrote section JSONL: {jsonl_path}")

    if not no_pdf_artifacts:
        _reset_section_artifacts(output_dir)
        write_split_pdfs(reader, ranges)
        print(f"Wrote split PDFs: {output_dir / 'artifacts' / 'by_section'}")
    else:
        print("Skipped split PDF artifacts.")

    print(f"Ingested {len(ranges)} sections from {len(entries)} TOC entries.")
    return len(ranges)


def _write_document_name(output_dir: Path, source: Path) -> None:
    """Record what the source document's own filename states.

    Where a document comes from an organization that encodes date, equipment
    tag and revision in the filename, that is metadata the text does not
    repeat -- and a lake that keeps only the text loses it.
    """
    path = docnamelib.write_document_json(output_dir, source)
    read = docnamelib.read(source)
    stated = []
    if read.dates:
        stated.append(f"{len(read.dates)} date(s)")
    if read.tags:
        stated.append(f"tag {', '.join(item.text for item in read.tags)}")
    if read.revision:
        stated.append(f"revision {read.revision.text}")
    detail = ", ".join(stated) if stated else "no date, tag or revision in the name"
    print(f"Wrote document name: {path} ({detail})")


def _write_section_pages(sections: list, output_dir: Path) -> Path:
    """One synthetic bronze page per section: its sequence number and text."""
    pages_path = output_dir / "bronze" / "pages.jsonl"
    pages_path.parent.mkdir(parents=True, exist_ok=True)
    with pages_path.open("w", encoding="utf-8") as output:
        for section in sections:
            output.write(
                json.dumps(
                    {"page": section.order, "text": mdunit.section_page_text(section)},
                    ensure_ascii=False,
                )
                + "\n"
            )
    return pages_path


def _write_addressed_items(
    sections: list, output_dir: Path, *, profile: str, ladder=None
):
    """Cut every section along the document's numbering ladder and record it.

    The section is the unit a reader cites; the *item* is the unit the document
    addresses (``4.1 (1) (가)``), and anything reading these documents for their
    content anchors on that address. Offsets are into the section body, so a
    consumer can verify the words really sit where the address says.
    """
    sample = "\n".join(section.title + "\n" + section.body for section in sections[:20])
    active = profileslib.resolve(profile, sample)
    report = pathslib.SegmentReport()
    rows: list[dict] = []
    for section, items in pathslib.segment_sections(
        sections, profile=active, ladder=ladder, report=report
    ):
        for item in items:
            rows.append(
                {
                    "section_index": section.order,
                    "section_title": section.title,
                    "address": item.address,
                    "path": list(item.path),
                    "label": item.label,
                    "depth": item.depth,
                    "irregular": item.irregular,
                    "ordered": item.ordered,
                    "sequence": item.sequence,
                    "skipped": item.skipped,
                    "char_start": item.char_start,
                    "char_end": item.char_end,
                    "char_own_end": item.char_own_end,
                    "text": item.text,
                }
            )
    return pathslib.write_items_jsonl(rows, output_dir), report


def _write_unitize_report(
    report, output_dir: Path, input_path: Path, provenance: str | None = None
) -> Path:
    """Record what unitizing dropped, demoted, and folded, next to the lake.

    A render's page furniture has to be removed for the sections to be usable,
    and removal without a record is indistinguishable from loss. This file is
    the record: counts, the marks themselves, and the ingest's known defects.
    Which converter produced the render is part of how the sections were
    decided, so it is recorded here too -- as provenance, not as a defect.
    """
    path = output_dir / "bronze" / "md_ingest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": input_path.name, **report.as_dict()}
    if provenance:
        payload["converted_by"] = provenance
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _write_page_texts(rows: list[dict], output_dir: Path) -> Path:
    """Page text from the source blocks, furniture excluded.

    The blocks say which of them the converter judged furniture, so the text a
    search index reads is the page's content without the running header -- a
    distinction the Markdown path has to infer and this one is told.
    """
    path = output_dir / "bronze" / "pages.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _write_section_artifacts(sections: list, ranges: list, output_dir: Path) -> None:
    """Per-section Markdown files, the flow-document analogue of the split PDFs."""
    for section, row in zip(sections, ranges):
        out_path = Path(row.output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        heading = "#" * section.level + " " + section.title
        text = f"{heading}\n\n{section.body}\n" if section.body else f"{heading}\n"
        out_path.write_text(text, encoding="utf-8")


def _ingest_markdown(
    markdown: str,
    *,
    input_path: Path,
    output_dir: Path,
    fallback_title: str,
    source_label: str,
    max_level: int | None = None,
    profile: str = "auto",
    write_items: bool = True,
    source_blocks: Path | None = None,
    provenance: str | None = None,
) -> None:
    """Unitize a Markdown string by heading and write the full lake.

    Shared by every flow-document input -- an HWP conversion, a plain Markdown
    file, a Docling/Marker render -- since they all reduce to "Markdown in,
    heading-unitized sections out." Each section is one synthetic page, so the
    manifest, index, and search layers work exactly as for a PDF.
    """
    result = mdunit.unitize(
        markdown,
        fallback_title=fallback_title,
        max_level=max_level,
        profile=profile,
    )
    sections = result.sections
    if not sections:
        raise SystemExit(
            "No text to ingest: the render carries no readable text.\n"
            "A converted scan looks like this when its OCR was disabled -- the "
            "layout is there, the words are not. Re-convert with OCR, or ingest "
            "the source PDF and let dokey route it to the OCR path."
        )
    print(f"Sections: {result.report.summary()}")

    # A render has no pages, but the stream it was rendered from does. When it
    # is there, sections take the pages they actually occupy instead of one
    # each -- a fifteen-page document stops claiming thirteen one-page sections.
    pages = None
    page_report = None
    if source_blocks is not None:
        blocks = blockslib.read_blocks(source_blocks)
        if blocks:
            page_report = blockslib.PageReport()
            pages = blockslib.locate_sections(sections, blocks, page_report)
            print(
                f"Pages: from {source_blocks.name} "
                f"({page_report.pages} pages, {page_report.located} sections "
                f"located, {page_report.interpolated} interpolated)"
            )
            result.report.notes.extend(page_report.notes)

    ranges = mdunit.build_section_ranges(sections, output_dir, pages)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_path = copy_raw_pdf(input_path, output_dir)  # copies any file under raw/
    print(f"Wrote raw {source_label}: {raw_path}")

    if pages is not None:
        pages_path = _write_page_texts(blockslib.page_texts(blocks), output_dir)
        print(f"Wrote page text: {pages_path} (from the source blocks)")
    else:
        pages_path = _write_section_pages(sections, output_dir)
        print(f"Wrote section text: {pages_path}")

    toc_path = write_toc(output_dir, result.outline)
    print(f"Wrote table of contents: {toc_path} ({len(result.outline)} entries)")

    if source_blocks is not None:
        # A caption belongs to something other than itself, and the block
        # stream is where the geometry to settle that lives.
        figures, figure_report = figureslib.read_figures(
            source_blocks, sections, pages
        )
        if figures:
            figures_path = figureslib.write_figures(output_dir, figures)
            print(f"Wrote figures: {figures_path} ({figure_report.summary()})")

    if write_items:
        items_path, segment_report = _write_addressed_items(
            sections, output_dir, profile=profile, ladder=result.ladder
        )
        print(
            f"Wrote addressed items: {items_path} "
            f"({segment_report.items} items, "
            f"{segment_report.skipped_rungs} skipped rungs)"
        )

    report_path = _write_unitize_report(result.report, output_dir, input_path, provenance)
    print(f"Wrote unitize report: {report_path}")

    _write_document_name(output_dir, input_path)
    _write_mentions(sections, output_dir, input_path)

    _finish_lake(sections, ranges, output_dir)


def _write_mentions(sections: list, output_dir: Path, source: Path) -> None:
    """Record where tag-shaped identifiers occur, with the address of each.

    A plant's documents are joined by their tags, not by their words: the
    sentence saying T-101 was damaged, the sheet listing its material and the
    quotation pricing its repair share nothing else. dokey records the
    occurrences and their addresses; what the tag denotes stays a question for
    whoever holds the tag registry.
    """
    named = tuple(item.text for item in docnamelib.read(source).tags)
    found, report = mentionslib.find(sections, source.stem, named)
    if not found:
        return
    path = mentionslib.write_mentions(output_dir, found)
    print(f"Wrote mentions: {path} ({report.summary()})")


def _finish_lake(sections: list, ranges: list, output_dir: Path) -> None:
    """Write the manifest, the per-section artifacts and the index, and sign off.

    The last three steps of every flow-document ingest, whatever decided the
    sections: a heading sweep of a render, or the sheets of a workbook.
    """
    csv_path, json_path, jsonl_path = write_manifests(output_dir, ranges)
    print(f"Wrote section CSV: {csv_path}")
    print(f"Wrote section JSON: {json_path}")
    print(f"Wrote section JSONL: {jsonl_path}")

    _reset_section_artifacts(output_dir)
    _write_section_artifacts(sections, ranges, output_dir)
    print(f"Wrote section Markdown: {output_dir / 'artifacts' / 'by_section'}")

    stats = searchlib.ensure_index(output_dir)
    print(
        f"Search index: {stats.db_path} "
        f"({stats.sections} sections, {stats.pages} pages)"
    )
    print("\nDone. Try:")
    print(f'  dokey search "keyword" --lake "{output_dir}"')
    print(f'  dokey ui --lake "{output_dir}"')


def _write_sections_lake(
    sections: list,
    *,
    input_path: Path,
    output_dir: Path,
    source_label: str,
    extra_report: dict | None = None,
) -> None:
    """Write a lake from sections that were decided without a heading sweep.

    A sheet is its own page, so the synthetic page numbering -- one page per
    section, in order -- is not a stand-in here: sheet 2 really is page 2.
    """
    ranges = mdunit.build_section_ranges(sections, output_dir, None)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_path = copy_raw_pdf(input_path, output_dir)  # copies any file under raw/
    print(f"Wrote raw {source_label}: {raw_path}")

    pages_path = _write_section_pages(sections, output_dir)
    print(f"Wrote section text: {pages_path}")

    outline = mdunit.derive_outline(sections)
    toc_path = write_toc(output_dir, outline)
    print(f"Wrote table of contents: {toc_path} ({len(outline)} entries)")

    if extra_report:
        report_path = output_dir / "bronze" / "ingest.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {"source": input_path.name, **extra_report},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Wrote ingest report: {report_path}")

    _write_document_name(output_dir, input_path)
    _write_mentions(sections, output_dir, input_path)
    _finish_lake(sections, ranges, output_dir)
