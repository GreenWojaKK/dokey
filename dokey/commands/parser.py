from __future__ import annotations

import argparse
from pathlib import Path

from .. import convert as convertlib
from .. import ocr as ocrlib
from .. import profiles as profileslib
from .common import _section_depth_arg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dokey",
        description="Section-aware book PDF ingestion for document lake pipelines.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auto = subparsers.add_parser(
        "auto",
        help=(
            "One-shot smart ingest: probe the PDF, pick a TOC source "
            "(outline > printed contents page), estimate the page offset, "
            "ingest, and build the search index. Start here."
        ),
    )
    auto.add_argument(
        "input",
        type=Path,
        help=(
            "Input PDF, a .md/.markdown file (e.g. a Docling render), or "
            ".hwp/.hwpx (needs a BYO converter; see `dokey hwp`)."
        ),
    )
    auto.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Lake directory. Default: dokey_out/<pdf name> under the current directory.",
    )
    auto.add_argument(
        "--page-offset",
        type=int,
        default=None,
        help=(
            "Printed page -> PDF page offset (PDF page = printed page + N). "
            "Default: estimated from the document's own running folios."
        ),
    )
    auto.add_argument(
        "--toc",
        type=Path,
        default=None,
        help=(
            "TOC file (CSV: level,title,page — or indented text), followed as "
            "given instead of reading the document's own sources. The page "
            "offset, the per-section verification, and the boundary overlap "
            "are still resolved from the document."
        ),
    )
    auto.add_argument(
        "--toc-format",
        choices=("auto", "csv", "text"),
        default="auto",
        help="Format of --toc. Default: auto-detected.",
    )
    auto.add_argument(
        "--toc-page",
        type=int,
        action="append",
        metavar="N",
        help=(
            "1-based PDF page holding the printed TOC (repeatable). Omit to "
            "auto-detect the contents page(s)."
        ),
    )
    auto.add_argument(
        "--outline-max-level",
        type=int,
        default=None,
        help=(
            "Deepest level to split from: PDF outline levels, or -- for a "
            "Markdown input whose heading levels are uniform -- the depth of "
            "the numbering (5.1 is level 2). Default: 1."
        ),
    )
    auto.add_argument(
        "--section-depth",
        type=_section_depth_arg,
        default=None,
        metavar="DEPTH",
        help=(
            "How deep to split into sections: 'clause' (the rung the document "
            "heads its clauses on), 'subclause' (one below that, 5.1), a rung "
            "number, or 'auto'. 'clause' and 'subclause' mean the same kind of "
            "unit in every document even when their ladders differ; a number "
            "means the same rung everywhere; 'auto' (the default) descends "
            "until the sections are of citable size, which is per-document and "
            "so is not comparable between them."
        ),
    )
    auto.add_argument(
        "--blocks",
        type=Path,
        default=None,
        help=(
            "Block stream (DoclingDocument JSON) the Markdown was rendered "
            "from, so sections take the pages they really occupy. Default: a "
            "<name>.json beside the input, when there is one."
        ),
    )
    auto.add_argument(
        "--no-items",
        action="store_true",
        help=(
            "Skip silver/items.jsonl, the section text cut along the document's "
            "own numbering ladder (4.1 (1) (가)). Written by default."
        ),
    )
    auto.add_argument(
        "--profile",
        choices=profileslib.AVAILABLE,
        default="auto",
        help=(
            "Language profile for Markdown unitizing (numbering ladder, "
            "sentence endings). Default: auto-detected from the text."
        ),
    )
    auto.add_argument(
        "--convert",
        choices=("auto", "never", "always"),
        default="auto",
        help=(
            "When to hand the PDF to the BYO layout converter (see `dokey "
            "convert`). 'auto' does so only when the pages are images with no "
            "text layer; 'never' always reads the text layer; 'always' converts "
            "regardless. Default: auto."
        ),
    )
    auto.add_argument(
        "--converter",
        default=None,
        help=(
            "Which converter to use for this run: a name (docling, markitdown) "
            "or a full command. This run's counterpart of `dokey convert "
            "--set`; the flag outranks the saved setting and discovery. "
            "Default: the saved converter, else the richest-evidence one found."
        ),
    )
    auto.add_argument(
        "--section-overlap",
        type=int,
        default=None,
        help=(
            "Extend each section's end N pages into the next. Default: chosen "
            "from the document — 0 when sections start on fresh pages (a clean "
            "break needs no shared page), else 1 (a boundary that falls "
            "mid-page keeps both sections complete)."
        ),
    )
    auto.add_argument(
        "--ocr-endpoint",
        default=None,
        help=(
            "OpenAI-compatible OCR chat endpoint for the scanned-PDF contents-"
            "page fallback. Default: the backend saved with `dokey backend "
            f"--set`, else {ocrlib.DEFAULT_ENDPOINT}"
        ),
    )

    convert = subparsers.add_parser(
        "convert",
        help=(
            "Convert a document with a BYO layout converter (Docling) and ingest "
            "the result. Use for scanned PDFs and layout-heavy documents."
        ),
    )
    convert.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="Document to convert (PDF, DOCX, PPTX, HTML, image...).",
    )
    convert.add_argument(
        "--converter",
        default=None,
        help=(
            "Which converter to use for this run: a name (docling, markitdown) "
            "or a full command. Unlike --set, nothing is saved."
        ),
    )
    convert.add_argument(
        "--set",
        dest="set_cmd",
        default=None,
        metavar="COMMAND",
        help='Save the converter command, e.g. --set "docling".',
    )
    convert.add_argument(
        "--clear", action="store_true", help="Forget the saved converter."
    )
    convert.add_argument(
        "--to",
        action="append",
        choices=("md", "json"),
        default=None,
        help=(
            "Conversion target; repeatable. 'md' is the readable render, 'json' "
            "the block stream that keeps page numbers and bounding boxes. "
            "Default: both, which costs one conversion, and leaves the JSON "
            "beside the Markdown where dokey looks for it."
        ),
    )
    convert.add_argument(
        "--output",
        "--keep",
        dest="output",
        type=Path,
        default=None,
        help=(
            "Where the converted files are written. Default: the current "
            "directory."
        ),
    )
    convert.add_argument(
        "--ingest",
        action="store_true",
        help=(
            "Also unitize the render into a searchable lake. Off by default: "
            "converting a document and taking it apart are separate acts, and "
            "conversion is the slow one. `dokey auto <render>.md` does the rest "
            "whenever you want it."
        ),
    )
    convert.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Lake directory for --ingest. Default: dokey_out/<name> under the "
            "current directory."
        ),
    )
    convert.add_argument(
        "--no-ingest",
        action="store_true",
        help="Accepted and ignored: not ingesting is now the default.",
    )
    convert.add_argument(
        "--ocr",
        action="store_true",
        help=(
            "Let the converter OCR bitmap content. Off by default: a PDF with a "
            "text layer needs none, and OCR is where a converter is slowest and "
            "least trustworthy."
        ),
    )
    convert.add_argument(
        "--ocr-engine",
        default=None,
        help="Converter OCR engine (e.g. easyocr, tesseract). See --ocr.",
    )
    convert.add_argument(
        "--ocr-lang",
        default=None,
        help="Comma-separated OCR languages, e.g. ko,en.",
    )
    convert.add_argument(
        "--images",
        choices=("placeholder", "embedded", "referenced"),
        default="placeholder",
        help=(
            "How the converter exports figures. Default: placeholder -- the "
            "figure's position is marked and its pixels stay out of the lake "
            # argparse expands % in help text, and "% o" is a valid octal
            # specifier: unescaped, this crashed `dokey convert --help`.
            "(embedded base64 was 99.7%% of a measured render's characters)."
        ),
    )
    convert.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps", "xpu"),
        default=None,
        help="Accelerator the converter should use. Default: the converter's own.",
    )
    convert.add_argument(
        "--timeout",
        type=int,
        default=convertlib.DEFAULT_TIMEOUT,
        help=f"Seconds to allow the converter. Default: {convertlib.DEFAULT_TIMEOUT}.",
    )
    convert.add_argument(
        "--outline-max-level",
        type=int,
        default=None,
        help="Section depth for the converted Markdown (see `dokey auto`). Default: 1.",
    )
    convert.add_argument(
        "--section-depth",
        type=_section_depth_arg,
        default=None,
        metavar="DEPTH",
        help=(
            "How deep to split into sections: 'clause' (the rung the document "
            "heads its clauses on), 'subclause' (one below that, 5.1), a rung "
            "number, or 'auto'. 'clause' and 'subclause' mean the same kind of "
            "unit in every document even when their ladders differ; a number "
            "means the same rung everywhere; 'auto' (the default) descends "
            "until the sections are of citable size, which is per-document and "
            "so is not comparable between them."
        ),
    )
    convert.add_argument(
        "--blocks",
        type=Path,
        default=None,
        help=(
            "Block stream (DoclingDocument JSON) the Markdown was rendered "
            "from, so sections take the pages they really occupy. Default: a "
            "<name>.json beside the input, when there is one."
        ),
    )
    convert.add_argument(
        "--no-items",
        action="store_true",
        help=(
            "Skip silver/items.jsonl, the section text cut along the document's "
            "own numbering ladder (4.1 (1) (가)). Written by default."
        ),
    )
    convert.add_argument(
        "--profile",
        choices=profileslib.AVAILABLE,
        default="auto",
        help="Language profile for unitizing. Default: auto-detected.",
    )

    split = subparsers.add_parser(
        "ingest",
        help="Create raw, bronze, silver, and artifact outputs from a PDF and TOC.",
    )
    split.add_argument("--input", type=Path, required=True, help="Input PDF path.")
    split.add_argument(
        "--converter",
        default=None,
        help=(
            "Which converter to use for this run: a name (docling, markitdown) "
            "or a full command. Outranks the saved setting and discovery."
        ),
    )
    split.add_argument("--toc", type=Path, help="TOC CSV or text path.")
    split.add_argument(
        "--toc-from-outline",
        action="store_true",
        help="Use the input PDF's outline/bookmarks as the TOC.",
    )
    split.add_argument(
        "--toc-from-page",
        action="store_true",
        help=(
            "Reconstruct the TOC from the book's own printed contents page(s) by "
            "word geometry (needs the optional [ocr] extra: PyMuPDF). Use when the "
            "PDF has no outline and no external TOC file."
        ),
    )
    split.add_argument(
        "--toc-page",
        type=int,
        action="append",
        metavar="N",
        help=(
            "1-based PDF page holding the printed TOC (repeatable). Only with "
            "--toc-from-page; omit to auto-detect the contents page(s)."
        ),
    )
    split.add_argument(
        "--no-ocr-fallback",
        action="store_true",
        help=(
            "With --toc-from-page, do not fall back to OCR when the PDF has no "
            "text layer (a scanned book); fail instead."
        ),
    )
    split.add_argument(
        "--ocr-endpoint",
        default=None,
        help=(
            "OpenAI-compatible OCR chat endpoint for the --toc-from-page scanned "
            "fallback. Default: the backend saved with `dokey backend --set`, "
            f"else {ocrlib.DEFAULT_ENDPOINT}"
        ),
    )
    split.add_argument(
        "--ocr-dpi",
        type=int,
        default=200,
        help="Render DPI for the --toc-from-page OCR fallback. Default: 200.",
    )
    split.add_argument(
        "--toc-format",
        choices=("auto", "csv", "text"),
        default="auto",
        help="TOC format. Default: auto.",
    )
    split.add_argument(
        "--outline-max-level",
        type=int,
        default=None,
        help=(
            "Deepest PDF outline level to split from when --toc-from-outline is set. "
            "Top-level outline entries are level 0. For a Markdown input whose "
            "heading levels are uniform, this caps the numbering depth instead "
            "(5.1 is level 2). Default: 1."
        ),
    )
    split.add_argument(
        "--profile",
        choices=profileslib.AVAILABLE,
        default="auto",
        help=(
            "Language profile for Markdown unitizing (numbering ladder, "
            "sentence endings). Default: auto-detected from the text."
        ),
    )
    split.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output document lake directory.",
    )
    split.add_argument(
        "--page-offset",
        type=int,
        default=0,
        help="Offset added to TOC/content pages to get PDF pages. Default: 0.",
    )
    split.add_argument(
        "--max-content-page",
        type=int,
        default=0,
        help="Last content page to include. Default 0 means infer from PDF length.",
    )
    split.add_argument(
        "--section-overlap",
        type=int,
        default=1,
        help=(
            "Extend each section's end by N pages into the next section. Section "
            "boundaries often fall mid-page; N=1 keeps a section's chunk complete "
            "when it shares a page with the next one, at the cost of duplicating "
            "that boundary page. Use 0 for strictly non-overlapping ranges. "
            "Default: 1."
        ),
    )
    split.add_argument(
        "--no-raw-copy",
        action="store_true",
        help="Do not copy the original PDF under raw/.",
    )
    split.add_argument(
        "--no-page-text",
        action="store_true",
        help="Do not extract bronze/pages.jsonl.",
    )
    split.add_argument(
        "--no-pdf-artifacts",
        action="store_true",
        help="Write manifests only; skip split PDF artifacts.",
    )

    index = subparsers.add_parser(
        "index",
        help="Build or refresh the full-text search index under gold/.",
    )
    index.add_argument("--lake", type=Path, help="Lake directory from a previous ingest.")
    index.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild even if the index looks up to date.",
    )

    search = subparsers.add_parser(
        "search",
        help="Full-text search over page text and section titles.",
    )
    search.add_argument("query", nargs="+", help="Search terms; FTS5 syntax allowed.")
    search.add_argument("--lake", type=Path, help="Lake directory from a previous ingest.")
    search.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of sections to report. Default: 10.",
    )
    search.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild the index before searching.",
    )

    ui = subparsers.add_parser(
        "ui",
        help="Launch the local Streamlit search UI (requires streamlit).",
    )
    ui.add_argument("--lake", type=Path, help="Lake directory to open at startup.")
    ui.add_argument("--port", type=int, help="Streamlit server port.")

    folios = subparsers.add_parser(
        "folios",
        help=(
            "Recover true printed page numbers (folios) from page images via a "
            "local OCR endpoint and add them to the manifest."
        ),
    )
    folios.add_argument("--lake", type=Path, help="Lake directory from a previous ingest.")
    folios.add_argument(
        "--pdf",
        type=Path,
        help="Source PDF to read folios from. Default: the file under the lake's raw/.",
    )
    folios.add_argument(
        "--source",
        choices=("auto", "toc", "ocr"),
        default="auto",
        help=(
            "Folio source. 'toc' parses the PDF's text Table of Contents (pypdf, "
            "no GPU); 'ocr' reads page images via a local OCR endpoint (for scanned "
            "PDFs); 'auto' uses the TOC when available and falls back to OCR. "
            "Default: auto."
        ),
    )
    folios.add_argument(
        "--endpoint",
        default=None,
        help=(
            "OpenAI-compatible OCR chat endpoint (ocr source). Default: the "
            "backend saved with `dokey backend --set`, else "
            f"{ocrlib.DEFAULT_ENDPOINT}"
        ),
    )
    folios.add_argument(
        "--all-pages",
        action="store_true",
        help=(
            "Exhaustively OCR every section-boundary page instead of the fast "
            "calibration search. Slower; use to verify the calibrated model."
        ),
    )
    folios.add_argument(
        "--verify",
        type=int,
        default=8,
        help="Number of evenly spaced pages to spot-check the calibrated model. Default: 8.",
    )
    folios.add_argument(
        "--dpi", type=int, default=200, help="Render DPI for OCR. Default: 200."
    )
    folios.add_argument(
        "--rebuild",
        action="store_true",
        help="Ignore any cached gold/folios.jsonl and re-OCR.",
    )

    backend = subparsers.add_parser(
        "backend",
        help=(
            "Show, discover, or set the local OCR serving endpoint. dokey "
            "ships no models: bring your own serving (LM Studio, llama.cpp "
            "llama-server, Ollama) and point dokey at it."
        ),
    )
    backend.add_argument(
        "--set",
        dest="set_url",
        metavar="URL",
        help=(
            "Persist URL (host:port, base URL, or full /v1/chat/completions) "
            "as the OCR backend for all commands."
        ),
    )
    backend.add_argument(
        "--clear",
        action="store_true",
        help="Forget the saved backend and return to the built-in default.",
    )
    backend.add_argument(
        "--no-discover",
        action="store_true",
        help="Skip probing well-known local ports.",
    )

    hwp = subparsers.add_parser(
        "hwp",
        help=(
            "Show, discover, or set the HWP/HWPX -> Markdown converter. dokey "
            "ships no HWP parser: bring your own (the reference one is hwp2md, a "
            "Rust CLI) and point dokey at it. To ingest an HWP file, pass it to "
            "`dokey auto <file.hwpx>`."
        ),
    )
    hwp.add_argument(
        "--set",
        dest="set_cmd",
        metavar="CMD",
        help=(
            "Persist a converter command, e.g. \"hwp2md\" or "
            "\"wsl.exe -e /home/<you>/.cargo/bin/hwp2md\". The `to-md` "
            "invocation is appended by dokey."
        ),
    )
    hwp.add_argument(
        "--wsl",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Force (or forbid) treating the command as running inside WSL, which "
            "translates Windows path arguments to /mnt/<drive>/.... Default: "
            "inferred from whether the command starts with wsl.exe."
        ),
    )
    hwp.add_argument(
        "--clear",
        action="store_true",
        help="Forget the saved converter and return to auto-discovery.",
    )

    app = subparsers.add_parser(
        "app",
        help=(
            "Open the UI in a local desktop window (requires the [app] extra: "
            "pywebview). Everything still runs on this machine."
        ),
    )
    app.add_argument("--lake", type=Path, help="Lake directory to open at startup.")
    app.add_argument(
        "--port", type=int, help="UI server port. Default: an unused port."
    )

    probe = subparsers.add_parser(
        "probe",
        help=(
            "Classify a PDF as a text-layer or a scanned (OCR-needed) document "
            "before ingesting (needs the optional [ocr] extra: PyMuPDF)."
        ),
    )
    probe.add_argument("--input", type=Path, required=True, help="Input PDF path.")
    probe.add_argument(
        "--min-mean-chars",
        type=int,
        default=150,
        help="Route to OCR when mean chars/page is below this. Default: 150.",
    )
    probe.add_argument(
        "--min-page-chars",
        type=int,
        default=20,
        help="A page with fewer chars and >=1 image counts as scanned. Default: 20.",
    )
    probe.add_argument(
        "--scan-ratio",
        type=float,
        default=0.5,
        help="Route to OCR when at least this fraction of pages look scanned. Default: 0.5.",
    )
    return parser
