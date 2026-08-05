"""PDF-oriented command handlers."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

from .. import backends as backendslib
from .. import convert as convertlib
from .. import converters as converterslib
from .. import detect as detectlib
from .. import ocr as ocrlib
from .. import offset as offsetlib
from .. import outline as outlinelib
from .. import search as searchlib
from .. import tocsource
from ..models import TocEntry
from ..outline import read_outline_toc
from ..pdf import open_reader
from ..toc import read_toc
from ..tocpage import read_page_toc
from .common import _lake_dir, _outline_max_level, _section_depth
from .lake import _ingest_markdown, ingest_entries


def run_probe(args: argparse.Namespace) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    probe = detectlib.probe_pdf(
        args.input,
        min_page_chars=args.min_page_chars,
        min_mean_chars=args.min_mean_chars,
        scan_ratio=args.scan_ratio,
    )
    print(detectlib.format_probe(probe))


def ingest(args: argparse.Namespace) -> None:
    reader = open_reader(args.input)
    if args.toc_from_outline:
        entries = read_outline_toc(reader, max_level=_outline_max_level(args))
    elif args.toc_from_page:
        if args.no_ocr_fallback:
            ocr_client = None
        else:
            endpoint, _ = backendslib.resolve_endpoint(args.ocr_endpoint)
            ocr_client = ocrlib.OcrClient(endpoint, max_tokens=2048)
        entries = read_page_toc(
            args.input,
            toc_pages=args.toc_page,
            ocr_client=ocr_client,
            ocr_dpi=args.ocr_dpi,
        )
        source = (
            f"pinned page(s) {args.toc_page}"
            if args.toc_page
            else "auto-detected contents page(s)"
        )
        print(f"Read {len(entries)} TOC entries from {source}.")
    elif args.toc is not None:
        entries = read_toc(args.toc, args.toc_format)
    else:
        raise ValueError(
            "Provide --toc, or set --toc-from-outline / --toc-from-page."
        )
    max_content_page = None if args.max_content_page == 0 else args.max_content_page
    ingest_entries(
        reader,
        entries,
        input_path=args.input,
        output_dir=args.output_dir,
        page_offset=args.page_offset,
        max_content_page=max_content_page,
        section_overlap=getattr(args, "section_overlap", 0),
        no_raw_copy=args.no_raw_copy,
        no_page_text=args.no_page_text,
        no_pdf_artifacts=args.no_pdf_artifacts,
        write_markdown=getattr(args, "markdown", False),
    )


def run_auto(args: argparse.Namespace) -> None:
    """One-shot smart ingest: recognize the document's shape, then run.

    The recognition is deliberately lexical (no LLM): pypdf reads the embedded
    outline, word geometry reads a printed contents page, and the running
    folios vote on the page offset. Every decision is printed so a wrong guess
    is visible and overridable (--toc-page, --page-offset).
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    input_pdf = args.input
    if not input_pdf.is_file():
        raise SystemExit(f"Input PDF not found: {input_pdf}")
    output_dir = _lake_dir(args, input_pdf)

    reader = open_reader(input_pdf)
    print(f"{input_pdf.name}: {len(reader.pages)} PDF pages")
    has_fitz = importlib.util.find_spec("fitz") is not None

    # Route probe: text layer vs scanned. dokey reads a text layer and nothing
    # else, so a scan is a dead end here -- hand it to the BYO converter, which
    # is the only thing in reach that can turn page images into text.
    if has_fitz:
        probe = detectlib.probe_pdf(input_pdf)
        print(
            f"Route: {probe.method.upper()} "
            f"(mean {probe.mean_chars:.0f} chars/page, "
            f"{len(probe.scanned_pages)} scanned-looking pages)"
        )
        # Only page images are grounds to hand the document to a converter.
        # The probe also routes to OCR on a low character *mean*, but a sparse
        # document is not a scanned one, and shelling out to a layout engine
        # (minutes of work, an OCR pass, a heavy dependency) on that evidence
        # would hijack documents whose text layer is perfectly readable.
        scanned = probe.scanned_ratio >= detectlib.SCAN_RATIO_DEFAULT
        wanted = getattr(args, "convert", "auto")
        if wanted == "always" or (scanned and wanted == "auto"):
            # A scanned page has nothing but its image, so this branch needs
            # a converter that reconstructs pages -- a markdown-only tool
            # would read the empty text layer and return silence.
            choice = converterslib.choose(
                input_pdf,
                require_blocks=True,
                prefer=getattr(args, "converter", None),
            )
            converter, source = (
                (choice.converter, choice.source) if choice else (None, "none")
            )
            if converter is None:
                print()
                print(
                    f"{len(probe.scanned_pages)} of {probe.pages} pages are images "
                    "with no text layer: pypdf will read nothing from them and "
                    "those sections will index empty."
                )
                print(convertlib.install_hint())
                print()
                print("Continuing on the text path; expect empty sections.")
            else:
                print(
                    f"Page images, no text layer: converting with "
                    f"{converter.display()} ({source}) instead."
                )
                return _convert_then_ingest(args, input_pdf, converter)
        elif probe.method == "ocr":
            print(
                "Note: little extractable text per page. If this document is a "
                "scan, `dokey convert` runs a BYO layout converter over it."
            )

    # A given TOC file is an instruction and replaces the source cascade --
    # but only the cascade. The offset prior, the smoke test, and the
    # per-boundary overlap still run: the file says what the sections are,
    # the document still says where they begin.
    if getattr(args, "toc", None) is not None:
        entries = read_toc(args.toc, getattr(args, "toc_format", "auto"))
        print(f"TOC: {len(entries)} entries from the given file ({args.toc.name})")
        return _ingest_resolved(
            args,
            reader,
            input_pdf,
            output_dir,
            entries,
            physical_pages=False,
            has_fitz=has_fitz,
        )

    # TOC source cascade, one implementation shared with the app's preview:
    # embedded outline, the document's own printed contents page, its numbered
    # headings, and OCR only when the text layer had nothing.
    endpoint, _ = backendslib.resolve_endpoint(args.ocr_endpoint)
    found = tocsource.resolve(
        reader,
        input_pdf,
        max_level=_outline_max_level(args),
        profile=getattr(args, "profile", "auto"),
        toc_pages=args.toc_page,
        ocr_client=ocrlib.OcrClient(endpoint, max_tokens=2048),
        allow_printed=has_fitz,
    )
    entries = found.entries
    if found.note:
        print(f"TOC: {found.note}")
    if not entries:
        if not has_fitz:
            raise SystemExit(
                "This PDF has no embedded outline, and reading its printed "
                "contents page needs PyMuPDF. Install the optional extra:\n"
                "  python -m pip install -e .[ocr]\n"
                "or supply a TOC file via `dokey ingest --toc ...`."
            )
        raise SystemExit(
            "No table of contents found: the PDF has no outline, no contents "
            "page dokey could read, and no numbered headings in its text. "
            "Supply one with `dokey ingest --toc ...`, or pin the contents "
            "page with --toc-page N."
        )
    print(f"TOC: {len(entries)} entries from {found.label}")
    return _ingest_resolved(
        args,
        reader,
        input_pdf,
        output_dir,
        entries,
        physical_pages=found.physical_pages,
        has_fitz=has_fitz,
    )


def _ingest_resolved(
    args: argparse.Namespace,
    reader,
    input_pdf: Path,
    output_dir: Path,
    entries,
    *,
    physical_pages: bool,
    has_fitz: bool,
) -> None:
    """Resolve where the sections begin and end, then ingest.

    The sequential tail every TOC source shares: the offset prior and the
    smoke test locate each section's real start page, that same look at the
    page settles each boundary's overlap, and the lake is written and
    indexed. Nothing here asks; every guess is printed and overridable.
    """
    # An entry whose page is already a physical PDF page needs no offset and no
    # smoke test: an outline's destination and a heading found in the body both
    # say where they are, where a printed contents page only says what the
    # document calls that place.
    if physical_pages:
        page_offset = 0 if args.page_offset is None else args.page_offset
        if has_fitz and args.section_overlap is None:
            # The smoke test never runs here, so the boundary evidence takes
            # its own pass: one read of each section's start page.
            entries = offsetlib.mark_clean_starts(input_pdf, entries)

    if not physical_pages:
        # A page offset prior: the flag if given, else the running folios,
        # else the first TOC titles located in the body. The prior is never
        # trusted as-is — the smoke test below verifies every section.
        if args.page_offset is not None:
            page_offset = args.page_offset
            print(f"Page offset prior: {page_offset} (from --page-offset)")
        else:
            estimate = offsetlib.estimate_page_offset(input_pdf)
            if estimate.offset is not None:
                page_offset = estimate.offset
                print(
                    f"Page offset prior: {page_offset} "
                    f"(folio votes {estimate.votes}/{estimate.sampled})"
                )
            else:
                scanned = offsetlib.title_scan_offset(input_pdf, entries)
                if scanned is None:
                    raise SystemExit(
                        "Could not derive the printed-page -> PDF-page "
                        "offset: no text-layer folios, and no TOC title was "
                        "found in the body (a scanned PDF?). Re-run with an "
                        "explicit offset:\n"
                        f'  dokey auto "{input_pdf}" --page-offset N\n'
                        "where PDF page = printed page + N."
                    )
                page_offset = scanned
                print(
                    f"Page offset prior: {page_offset} "
                    "(from locating TOC titles in the body)"
                )

        # Smoke test: read every section's predicted start page and verify
        # the section actually begins there; pin drift away where it does not.
        entries, report = offsetlib.pin_section_starts(
            input_pdf, entries, page_offset
        )
        print(
            f"Smoke test: {report.verified}/{len(report.checks)} section "
            f"starts verified, {report.corrected} corrected, "
            f"{report.unresolved} interpolated"
        )
        shown = 0
        for check in report.checks:
            if check.status == "corrected" and shown < 12:
                print(
                    f"  corrected: {check.title} — pdf "
                    f"{check.predicted_pdf_page} -> {check.found_pdf_page}"
                )
                shown += 1
        if report.unresolved:
            unresolved_titles = [
                check.title for check in report.checks if check.status == "unresolved"
            ]
            listed = ", ".join(unresolved_titles[:6])
            if len(unresolved_titles) > 6:
                listed += ", ..."
            print(f"  interpolated from neighbors: {listed}")

    # Section overlap: the flag if given, else decided boundary by boundary
    # from each section's own start page — already read by the smoke test (or
    # the clean-start pass above). A heading that opens a fresh page shares
    # nothing; a mid-page break keeps the shared page in both sections; a
    # page that was never read stays shared, the safe side.
    if args.section_overlap is not None:
        section_overlap = args.section_overlap
        print(f"Section overlap: {section_overlap} (from --section-overlap)")
    else:
        section_overlap = None
        sample = [
            entry.clean_start for entry in entries if entry.clean_start is not None
        ]
        fresh = sum(1 for clean in sample if clean)
        if sample:
            print(
                f"Section overlap: per boundary — {fresh}/{len(sample)} "
                "sections open a fresh page; only mid-page breaks share "
                "their page"
            )
        else:
            print(
                "Section overlap: per boundary (no start page read — "
                "boundary pages shared)"
            )

    ingest_entries(
        reader,
        entries,
        input_path=input_pdf,
        output_dir=output_dir,
        page_offset=page_offset,
        max_content_page=None,
        section_overlap=section_overlap,
        write_markdown=getattr(args, "markdown", False),
    )
    stats = searchlib.ensure_index(output_dir)
    print(
        f"Search index: {stats.db_path} "
        f"({stats.sections} sections, {stats.pages} pages)"
    )
    print("\nDone. Try:")
    print(f'  dokey search "keyword" --lake "{output_dir}"')
    print(f'  dokey ui --lake "{output_dir}"')


def _printed_toc_if_better(
    args: argparse.Namespace,
    input_pdf: Path,
    outline_entries: list[TocEntry],
    page_count: int,
) -> list[TocEntry]:
    """The printed contents page, if it divides the document and the outline did not.

    Reading it costs a word-geometry scan of the front matter and nothing else,
    but the outline is given up only for something demonstrably better: more
    entries, and a division of the document by the same test the outline just
    failed. Otherwise the outline stands -- a poor table of contents still
    beats none, and its pages at least need no offset.

    The printed entries carry the document's own folios rather than PDF pages, so
    they are measured on the spans between them, which the page offset does not
    move.
    """
    if importlib.util.find_spec("fitz") is None:
        return []
    try:
        printed = read_page_toc(input_pdf, toc_pages=args.toc_page, ocr_client=None)
    except ValueError:
        return []
    if len(printed) <= len(outline_entries):
        return []
    if not outlinelib.divides_document(printed, page_count, count_tail=False):
        return []
    return printed


def _convert_then_ingest(
    args: argparse.Namespace, input_path: Path, converter
) -> None:
    """The scanned-PDF path: convert out of process, ingest the Markdown.

    OCR is on here, unlike ``dokey convert``'s default: a page image is the one
    case where there is nothing else to read. The language comes from the OCR
    backend's saved setting if there is one, so a Korean scan does not get the
    converter's default engine by accident.

    The lake is named here rather than by the caller, because until the route
    was settled there was no converter to name it after.
    """
    output_dir = _lake_dir(args, input_path, convertlib.converter_slug(converter))
    options = convertlib.load_options()
    caution = convertlib.ocr_engine_caution(True, options.ocr_engine)
    if caution:
        print(f"Note: {caution}")
        print(
            '      Save the choice once:  dokey convert --set "docling" '
            "--ocr-engine easyocr --ocr-lang ko,en"
        )
    started = time.time()
    # Both formats, because the same parse produces them: the Markdown is what
    # unitizes, and the block stream is where the sections' pages come from. A
    # scan has no text layer for dokey to fall back on, so without the JSON the
    # pages here would be the synthetic one-per-section kind.
    produced = convertlib.convert(
        input_path,
        converter,
        to=convertlib.DEFAULT_TARGETS,
        ocr=True,
        ocr_engine=options.ocr_engine,
        ocr_lang=options.ocr_lang,
        device=options.device,
        images=options.images or "placeholder",
        timeout=getattr(args, "timeout", convertlib.DEFAULT_TIMEOUT),
    )
    render = next((path for path in produced if path.suffix == ".md"), None)
    if render is None:
        raise SystemExit("The converter produced no Markdown to ingest.")
    print(
        f"Converted in {time.time() - started:.1f}s: "
        f"{', '.join(path.name for path in produced)}"
    )
    _ingest_markdown(
        render.read_text(encoding="utf-8"),
        input_path=input_path,
        output_dir=output_dir,
        fallback_title=input_path.stem,
        source_label="document",
        max_level=_section_depth(args),
        profile=getattr(args, "profile", "auto"),
        write_items=not getattr(args, "no_items", False),
        source_blocks=next(
            (path for path in produced if path.suffix == ".json"), None
        ),
    )
