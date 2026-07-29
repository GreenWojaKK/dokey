from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import backends as backendslib
from . import blocks as blockslib
from . import bodytoc
from . import convert as convertlib
from . import detect as detectlib
from . import docname as docnamelib
from . import figures as figureslib
from . import folios as folioslib
from . import hwp as hwplib
from . import mdunit
from . import mentions as mentionslib
from . import ocr as ocrlib
from . import paths as pathslib
from . import profiles as profileslib
from . import offset as offsetlib
from . import sheets as sheetslib
from . import outline as outlinelib
from . import search as searchlib
from . import tocsource
from .manifest import write_manifest_rows, write_manifests, write_toc
from .models import TocEntry
from .outline import read_outline_toc
from .pdf import copy_raw_pdf, open_reader, write_pages_jsonl, write_split_pdfs
from .ranges import build_ranges
from .toc import read_toc
from .tocpage import read_page_toc


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


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:]) if argv is None else list(argv)
    if not arguments:
        # A double-clicked dokey.exe lands here: launch the app instead of
        # printing a usage error into a console that closes immediately.
        launch_default()
        return
    parser = build_parser()
    args = parser.parse_args(arguments)

    if args.command == "auto":
        if hwplib.is_hwp(args.input):
            run_hwp_ingest(args)
        elif sheetslib.is_spreadsheet(args.input):
            run_sheet_ingest(args)
        elif mdunit.is_markdown(args.input):
            run_md_ingest(args)
        else:
            run_auto(args)
    elif args.command == "ingest":
        if hwplib.is_hwp(args.input):
            run_hwp_ingest(args)
        elif sheetslib.is_spreadsheet(args.input):
            run_sheet_ingest(args)
        elif mdunit.is_markdown(args.input):
            run_md_ingest(args)
        else:
            ingest(args)
    elif args.command == "convert":
        run_convert(args)
    elif args.command == "hwp":
        run_hwp_backend(args)
    elif args.command == "index":
        run_index(args)
    elif args.command == "search":
        run_search(args)
    elif args.command == "ui":
        run_ui(args)
    elif args.command == "folios":
        run_folios(args)
    elif args.command == "probe":
        run_probe(args)
    elif args.command == "backend":
        run_backend(args)
    elif args.command == "app":
        run_app(args)
    else:  # pragma: no cover
        parser.error(f"Unsupported command: {args.command}")


def _ensure_workspace_cwd() -> Path:
    """Give a bare launch a stable working directory.

    When started from a real project directory (lakes present under cwd), keep
    it. Otherwise — the double-click case, where Windows hands us the Scripts
    folder — switch to the user workspace so lake discovery and new ingests
    land in one predictable, writable place."""
    if searchlib.find_lakes(Path.cwd()):
        return Path.cwd()
    workspace = backendslib.workspace_dir()
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)
    return workspace


def launch_default() -> None:
    """The no-argument surface: open the friendliest available UI."""
    if importlib.util.find_spec("streamlit") is None:
        build_parser().print_help()
        raise SystemExit(
            "\nTo launch the UI by double-click, install the app extras first:\n"
            "  python -m pip install -e .[app]"
        )
    _ensure_workspace_cwd()
    namespace = argparse.Namespace(lake=None, port=None)
    if importlib.util.find_spec("webview") is not None:
        run_app(namespace)
    else:
        run_ui(namespace)


def _alert(message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Dokey", 0x10)
    else:
        print(message, file=sys.stderr)


def main_app() -> None:
    """Entry point of the windowed ``dokey-app`` executable (gui-scripts).

    A GUI process has no console: nothing printed is ever seen, so failures
    must surface as a message box instead of vanishing stderr."""
    try:
        launch_default()
    except SystemExit as exc:
        if exc.code not in (0, None):
            _alert(str(exc))
    except Exception as exc:  # pragma: no cover - last-resort surface
        _alert(f"Dokey failed to start:\n{exc}")


def run_backend(args: argparse.Namespace) -> None:
    if args.set_url and args.clear:
        raise SystemExit("Pass either --set or --clear, not both.")
    if args.set_url:
        path = backendslib.set_saved_endpoint(args.set_url)
        print(f"Saved OCR backend: {backendslib.chat_endpoint(args.set_url)}")
        print(f"  config: {path}")
    elif args.clear:
        backendslib.set_saved_endpoint(None)
        print("Cleared the saved OCR backend; the built-in default applies.")

    endpoint, source = backendslib.resolve_endpoint(None)
    backend = backendslib.probe(endpoint)
    status = "online" if backend is not None else "offline"
    print(f"OCR backend: {endpoint} ({source}, {status})")
    if backend is not None and backend.models:
        shown = ", ".join(backend.models[:6])
        if len(backend.models) > 6:
            shown += ", ..."
        print(f"  models: {shown}")

    if not args.no_discover:
        print("Scanning well-known local ports ...")
        found = backendslib.discover()
        if not found:
            print(
                "  no OpenAI-compatible server found; start one "
                "(LM Studio, llama.cpp llama-server, Ollama)"
            )
        for item in found:
            models = ", ".join(item.models[:4]) or "?"
            print(f"  {item.endpoint}  models: {models}")
        if found:
            print("Choose one with: dokey backend --set <url>")


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_ui(port: int, timeout: float = 45.0) -> bool:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/_stcore/health", timeout=2
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def run_app(args: argparse.Namespace) -> None:
    if importlib.util.find_spec("streamlit") is None:
        raise SystemExit(
            "Streamlit is not installed. Install the app extras first:\n"
            "  python -m pip install -e .[app]"
        )
    if importlib.util.find_spec("webview") is None:
        raise SystemExit(
            "pywebview is not installed. Install the optional app extra first:\n"
            "  python -m pip install -e .[app]\n"
            "or\n"
            "  python -m pip install pywebview\n"
            "(Or use the browser UI instead: dokey ui)"
        )
    port = args.port or _free_port()
    app_path = Path(__file__).resolve().parent / "ui_app.py"
    command = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.port", str(port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    if args.lake is not None:
        command += ["--", "--lake", str(args.lake)]
    server = subprocess.Popen(command, cwd=os.getcwd())
    try:
        if not _wait_for_ui(port):
            raise SystemExit(
                "The UI server did not come up; run `dokey ui` to see its output."
            )
        import webview

        webview.create_window(
            "Dokey",
            f"http://127.0.0.1:{port}",
            width=1280,
            height=860,
            # pywebview disables text selection by default, which makes the
            # desktop window a place where an error message can be read but not
            # copied -- exactly the text a user needs to hand to someone else.
            # The browser UI never had this problem; the app should not either.
            text_select=True,
        )
        assets_dir = Path(__file__).resolve().parent / "assets"
        # winforms needs a real .ico; gtk/cocoa load PNG directly.
        icon_path = assets_dir / ("logo.ico" if sys.platform == "win32" else "logo.png")
        webview.start(icon=str(icon_path) if icon_path.exists() else None)
    finally:
        server.terminate()


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
    )


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
    section_overlap: int,
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


def _default_lake_dir(input_pdf: Path) -> Path:
    # Keep the (often non-ASCII) book name readable in the lake path; strip
    # only the characters the filesystem rejects.
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", input_pdf.stem).strip() or "book"
    return Path("dokey_out") / stem


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
    output_dir = args.output_dir or _default_lake_dir(input_pdf)

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
            converter, source = convertlib.resolve_converter()
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
                return _convert_then_ingest(args, input_pdf, output_dir, converter)
        elif probe.method == "ocr":
            print(
                "Note: little extractable text per page. If this document is a "
                "scan, `dokey convert` runs a BYO layout converter over it."
            )

    # TOC source cascade, one implementation shared with the app's preview:
    # embedded outline, the book's own printed contents page, the document's
    # numbered headings, and OCR only when the text layer had nothing.
    report: offsetlib.SmokeReport | None = None
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

    # An entry whose page is already a physical PDF page needs no offset and no
    # smoke test: an outline's destination and a heading found in the body both
    # say where they are, where a printed contents page only says what the book
    # calls that place.
    physical_pages = found.physical_pages
    if physical_pages:
        page_offset = 0 if args.page_offset is None else args.page_offset

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

    # Section overlap: the flag if given, else read from how the document
    # breaks. A section that starts on a fresh page (clean break) needs no
    # shared boundary page; one that starts mid-page needs overlap 1 to stay
    # complete. The smoke test already read every start page, so the choice is
    # free. Without a report (outline TOC, or too few sections located) the
    # safe default 1 stands.
    if args.section_overlap is not None:
        section_overlap = args.section_overlap
        print(f"Section overlap: {section_overlap} (from --section-overlap)")
    elif report is not None and report.clean_breaking is not None:
        section_overlap = report.recommended_overlap()
        style = (
            "sections start on fresh pages"
            if section_overlap == 0
            else "section breaks fall mid-page"
        )
        print(
            f"Section overlap: {section_overlap} "
            f"({report.clean_starts}/{report.clean_sample} clean starts — {style})"
        )
    else:
        section_overlap = 1
        print("Section overlap: 1 (default)")

    ingest_entries(
        reader,
        entries,
        input_path=input_pdf,
        output_dir=output_dir,
        page_offset=page_offset,
        max_content_page=None,
        section_overlap=section_overlap,
    )
    stats = searchlib.ensure_index(output_dir)
    print(
        f"Search index: {stats.db_path} "
        f"({stats.sections} sections, {stats.pages} pages)"
    )
    print("\nDone. Try:")
    print(f'  dokey search "keyword" --lake "{output_dir}"')
    print(f'  dokey ui --lake "{output_dir}"')


def _no_converter_message() -> str:
    return (
        "No HWP converter found. dokey ships no HWP parser; bring your own.\n"
        "The reference converter is hwp2md (a Rust CLI). dokey only runs it at\n"
        "arm's length -- a separate process -- so dokey stays MIT even though\n"
        "hwp2md is GPL, as long as you install it yourself:\n"
        "  cargo install hwp2md            # native, on PATH\n"
        "dokey also auto-discovers an hwp2md installed inside WSL.\n"
        "Point dokey at any HWP->Markdown converter explicitly:\n"
        '  dokey hwp --set "hwp2md"\n'
        '  dokey hwp --set "wsl.exe -e /home/<you>/.cargo/bin/hwp2md"'
    )


def _section_depth_arg(value: str):
    """``auto``, ``clause``, ``subclause``, or a rung number."""
    if value in mdunit.SECTION_DEPTH_CHOICES:
        return value
    try:
        depth = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a number or one of {', '.join(mdunit.SECTION_DEPTH_CHOICES)}, "
            f"got {value!r}"
        ) from None
    if depth < 1:
        raise argparse.ArgumentTypeError("section depth starts at 1")
    return depth


def _section_depth(args: argparse.Namespace):
    """What the caller asked for, with the older flag still honoured."""
    requested = getattr(args, "section_depth", None)
    if requested is not None:
        return requested
    return getattr(args, "outline_max_level", None)


def _outline_max_level(args: argparse.Namespace) -> int:
    """Split depth for the PDF outline path.

    The flag now defaults to unset so the Markdown path can tell "the user
    asked for depth 1" from "the user said nothing" -- there, saying nothing
    means *honor the file's own heading levels*. For an outline, saying nothing
    still means depth 1, which is what it always meant.
    """
    level = _section_depth(args)
    if isinstance(level, int):
        return level
    # An outline states its own levels, so a named depth maps to a count:
    # clauses are its top level, subclauses one below.
    return 2 if level == "subclause" else 1


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

    The printed entries carry the book's own folios rather than PDF pages, so
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


def _write_unitize_report(report, output_dir: Path, input_path: Path) -> Path:
    """Record what unitizing dropped, demoted, and folded, next to the lake.

    A render's page furniture has to be removed for the sections to be usable,
    and removal without a record is indistinguishable from loss. This file is
    the record: counts, the marks themselves, and the ingest's known defects.
    """
    path = output_dir / "bronze" / "md_ingest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"source": input_path.name, **report.as_dict()}
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

    report_path = _write_unitize_report(result.report, output_dir, input_path)
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


def run_hwp_ingest(args: argparse.Namespace) -> None:
    """Ingest an .hwp/.hwpx by converting it to Markdown (BYO converter) and
    unitizing the heading hierarchy into sections."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    input_path = args.input
    if not input_path.is_file():
        raise SystemExit(f"HWP file not found: {input_path}")
    output_dir = getattr(args, "output_dir", None) or _default_lake_dir(input_path)

    converter, source = hwplib.resolve_converter()
    if converter is None:
        raise SystemExit(_no_converter_message())
    print(f"{input_path.name}: HWP converter {converter.display()} ({source})")

    markdown = hwplib.convert_to_markdown(input_path, converter)
    _ingest_markdown(
        markdown,
        input_path=input_path,
        output_dir=output_dir,
        fallback_title=input_path.stem,
        source_label="HWP",
        max_level=_section_depth(args),
        profile=getattr(args, "profile", "auto"),
        write_items=not getattr(args, "no_items", False),
    )


def run_sheet_ingest(args: argparse.Namespace) -> None:
    """Ingest a spreadsheet: convert it, then take one section per sheet.

    The conversion is the layout converter's job -- merged cells, formats, the
    legacy binary formats -- and the unitizing is dokey's. What comes back is
    tables tagged with the sheet they came from; what a workbook has no room
    for is a heading, so the prose unitizer is not involved at all.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    input_path = args.input
    if not input_path.is_file():
        raise SystemExit(f"Spreadsheet not found: {input_path}")
    output_dir = getattr(args, "output_dir", None) or _default_lake_dir(input_path)

    # A legacy binary workbook never sees the converter: the converter cannot
    # open it, and a grid needs no layout reconstruction anyway.
    if not sheetslib.needs_converter(input_path):
        sections, report = sheetslib.read_xls(input_path)
        print(f"{input_path.name}: legacy workbook, read directly (no converter)")
        print(f"Sheets: {report.summary()}")
        _write_sections_lake(
            sections,
            input_path=input_path,
            output_dir=output_dir,
            source_label="spreadsheet",
            extra_report={"sheets": report.as_dict()},
        )
        return

    blocks = getattr(args, "blocks", None) or blockslib.find_source_blocks(input_path)
    if blocks is None:
        converter, source = convertlib.resolve_converter()
        if converter is None:
            raise SystemExit(convertlib.install_hint())
        print(f"{input_path.name}: converting with {converter.display()} ({source})")
        options = convertlib.load_options()
        started = time.time()
        produced = convertlib.convert(
            input_path,
            converter,
            to=("json",),  # the block stream says which sheet each table is on
            ocr=False,
            device=options.device,
            images=options.images or "placeholder",
            timeout=getattr(args, "timeout", convertlib.DEFAULT_TIMEOUT),
        )
        blocks = next((path for path in produced if path.suffix == ".json"), None)
        if blocks is None:
            raise SystemExit("The converter produced no block stream to read.")
        print(f"Converted in {time.time() - started:.1f}s: {blocks.name}")

    names = sheetslib.sheet_names(input_path)
    sections, report = sheetslib.unitize(blocks, names)
    if not sections:
        raise SystemExit(
            "No sheets to ingest: the converter found no tables in this workbook."
        )
    print(f"Sheets: {report.summary()}")
    _write_sections_lake(
        sections,
        input_path=input_path,
        output_dir=output_dir,
        source_label="spreadsheet",
        extra_report={"sheets": report.as_dict()},
    )


def run_md_ingest(args: argparse.Namespace) -> None:
    """Ingest a Markdown/text file directly -- no conversion needed.

    This is the fast path for text a user already has: a Docling/Marker PDF
    render, exported notes, anything with an ATX heading hierarchy. dokey reads
    it as-is and unitizes by heading, keeping upstream layout tools upstream.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    input_path = args.input
    if not input_path.is_file():
        raise SystemExit(f"Markdown file not found: {input_path}")
    output_dir = getattr(args, "output_dir", None) or _default_lake_dir(input_path)

    markdown = input_path.read_text(encoding="utf-8")
    print(f"{input_path.name}: Markdown input ({len(markdown)} chars)")
    _ingest_markdown(
        markdown,
        input_path=input_path,
        output_dir=output_dir,
        fallback_title=input_path.stem,
        source_label="Markdown",
        max_level=_section_depth(args),
        profile=getattr(args, "profile", "auto"),
        write_items=not getattr(args, "no_items", False),
        # A converter that wrote a block stream beside the render kept the
        # pages; without it the sections fall back to one page each.
        source_blocks=(
            getattr(args, "blocks", None) or blockslib.find_source_blocks(input_path)
        ),
    )


def _convert_then_ingest(
    args: argparse.Namespace, input_path: Path, output_dir: Path, converter
) -> None:
    """The scanned-PDF path: convert out of process, ingest the Markdown.

    OCR is on here, unlike ``dokey convert``'s default: a page image is the one
    case where there is nothing else to read. The language comes from the OCR
    backend's saved setting if there is one, so a Korean scan does not get the
    converter's default engine by accident.
    """
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


def run_convert(args: argparse.Namespace) -> None:
    """Convert a document with the BYO converter, then ingest what it produced."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.set_cmd and args.clear:
        raise SystemExit("Pass either --set or --clear, not both.")
    if args.set_cmd:
        converter = convertlib.converter_from_command(args.set_cmd)
        path = convertlib.save_converter(converter)
        print(f"Saved converter: {converter.display()}")
        # Whatever options came with --set become the defaults, so the paths
        # that convert without asking (dokey auto on a scan) use them too.
        # Merge onto what is already saved: re-running --set to change the
        # command must not quietly drop an OCR engine chosen earlier.
        saved = convertlib.load_options().merged(
            ocr_engine=args.ocr_engine,
            ocr_lang=args.ocr_lang,
            device=args.device,
            images=args.images if args.images != "placeholder" else None,
        )
        convertlib.save_options(saved)
        print(f"  defaults: {saved.describe()}")
        print(f"  config: {path}")
    elif args.clear:
        convertlib.save_converter(None)
        convertlib.save_options(convertlib.Options())
        print("Cleared the saved converter; auto-discovery applies.")

    converter, source = convertlib.resolve_converter()
    if args.input is None:
        if converter is None:
            print("Converter: none found")
            print()
            print(convertlib.install_hint())
            return
        print(f"Converter: {converter.display()} ({source})")
        print(f"Saved defaults: {convertlib.load_options().describe()}")
        print("Convert a document with:  dokey convert <file.pdf>")
        print("  …and unitize what comes back:  dokey convert <file.pdf> --ingest")
        return
    if converter is None:
        raise SystemExit(convertlib.install_hint())

    input_path = args.input
    if not input_path.is_file():
        raise SystemExit(f"File not found: {input_path}")
    print(f"Converter: {converter.display()} ({source})")
    options = convertlib.load_options().merged(
        ocr_engine=args.ocr_engine,
        ocr_lang=args.ocr_lang,
        device=args.device,
        images=args.images if args.images != "placeholder" else None,
    )
    caution = convertlib.ocr_engine_caution(args.ocr, options.ocr_engine)
    if caution:
        print(f"Note: {caution}")
    targets = tuple(args.to) if args.to else convertlib.DEFAULT_TARGETS
    out_dir = args.output or Path.cwd()
    print(
        f"Converting {input_path.name} to {', '.join(targets)} "
        "(this can take a while)..."
    )
    started = time.time()
    produced = convertlib.convert(
        input_path,
        converter,
        to=targets,
        ocr=args.ocr,
        ocr_engine=options.ocr_engine,
        ocr_lang=options.ocr_lang,
        device=options.device,
        images=options.images or "placeholder",
        timeout=args.timeout,
        work_dir=out_dir,
    )
    print(f"Converted in {time.time() - started:.1f}s:")
    for path in produced:
        print(f"  {path}")

    render = next((path for path in produced if path.suffix == ".md"), None)
    if not args.ingest:
        # Converting a document and taking it apart are separate acts. The
        # conversion is the product here; the next command is printed rather
        # than run, so the slow step is never repeated to reach the fast one.
        if render is not None:
            print()
            print("Unitize it into a searchable lake with:")
            print(f'  dokey auto "{render}"')
            if any(path.suffix == ".json" for path in produced):
                print(
                    "  (the block stream beside it gives the sections their "
                    "real pages)"
                )
        return

    if render is None:
        raise SystemExit(
            "Nothing to ingest: the lake is built from the Markdown render, so "
            "ask for --to md as well."
        )
    output_dir = getattr(args, "output_dir", None) or _default_lake_dir(input_path)
    markdown = render.read_text(encoding="utf-8")
    print(f"{render.name}: Markdown from converter ({len(markdown)} chars)")
    _ingest_markdown(
        markdown,
        input_path=input_path,  # keep the *source* document under raw/
        output_dir=output_dir,
        fallback_title=input_path.stem,
        source_label="document",
        max_level=_section_depth(args),
        profile=getattr(args, "profile", "auto"),
        write_items=not getattr(args, "no_items", False),
        source_blocks=(
            getattr(args, "blocks", None)
            or next((path for path in produced if path.suffix == ".json"), None)
            or blockslib.find_source_blocks(input_path)
        ),
    )


def run_hwp_backend(args: argparse.Namespace) -> None:
    """Manage the BYO HWP converter, mirroring `dokey backend` for OCR."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.set_cmd and args.clear:
        raise SystemExit("Pass either --set or --clear, not both.")
    if args.set_cmd:
        converter = hwplib.converter_from_command(args.set_cmd, wsl=args.wsl)
        path = hwplib.save_converter(converter)
        print(f"Saved HWP converter: {converter.display()}")
        print(f"  config: {path}")
    elif args.clear:
        hwplib.save_converter(None)
        print("Cleared the saved HWP converter; auto-discovery applies.")

    converter, source = hwplib.resolve_converter()
    if converter is None:
        print("HWP converter: none found")
        print()
        print(_no_converter_message())
        return
    print(f"HWP converter: {converter.display()} ({source})")
    print("Ingest an HWP file with:  dokey auto <file.hwpx>")


def resolve_lake(lake: Path | None) -> Path:
    if lake is not None:
        if not (lake / "silver" / "sections.jsonl").exists():
            raise SystemExit(
                f"Not a lake directory (no silver/sections.jsonl): {lake}"
            )
        return lake
    candidates = searchlib.find_lakes(Path.cwd())
    if len(candidates) == 1:
        print(f"Using lake: {candidates[0]}")
        return candidates[0]
    if not candidates:
        raise SystemExit("No lake found under the current directory. Pass --lake.")
    listing = "\n".join(f"  {path}" for path in candidates)
    raise SystemExit(f"Multiple lakes found; pass --lake:\n{listing}")


def run_index(args: argparse.Namespace) -> None:
    lake = resolve_lake(args.lake)
    stale = args.rebuild or searchlib.is_stale(lake)
    stats = searchlib.ensure_index(lake, rebuild=args.rebuild)
    action = "Built" if stale else "Up to date"
    print(
        f"{action}: {stats.db_path} "
        f"({stats.sections} sections, {stats.pages} pages)"
    )
    if not stats.has_page_text:
        print(
            "Note: no bronze/pages.jsonl, so only section titles are searchable. "
            "Re-run ingest without --no-page-text for full-text search."
        )


def run_search(args: argparse.Namespace) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    lake = resolve_lake(args.lake)
    if args.rebuild or searchlib.is_stale(lake):
        stats = searchlib.ensure_index(lake, rebuild=args.rebuild)
        print(
            f"Built index: {stats.db_path} "
            f"({stats.sections} sections, {stats.pages} pages)"
        )
    query = " ".join(args.query)
    hits = searchlib.search(lake, query, limit=args.limit)
    if not hits:
        print(f"No matches for: {query}")
        return

    for rank, hit in enumerate(hits, start=1):
        flag = "  [title match]" if hit.matched_title else ""
        # A top-level section is its own parent; printing the breadcrumb then
        # just says the title twice.
        crumb = f"{hit.parent} > {hit.title}" if hit.parent != hit.title else hit.title
        print(f"{rank:2d}. {crumb}{flag}")
        pages = ", ".join(str(page) for page in hit.pages[:8])
        if len(hit.pages) > 8:
            pages += ", ..."
        if hit.printed_start_page is not None:
            location = (
                f"    book pp. {hit.printed_start_page}-{hit.printed_end_page}"
                f" | pdf {hit.pdf_start_page}-{hit.pdf_end_page}"
            )
        else:
            location = (
                f"    content {hit.content_start_page}-{hit.content_end_page}"
                f" | pdf {hit.pdf_start_page}-{hit.pdf_end_page}"
            )
        if pages:
            location += f" | matched pdf pages: {pages}"
        print(location)
        for snippet in hit.snippets:
            rendered = snippet.replace(searchlib.MARK_START, "«").replace(
                searchlib.MARK_END, "»"
            )
            print(f"    ... {rendered} ...")
        artifact = searchlib.resolve_artifact(lake, hit)
        if artifact is not None:
            print(f"    {artifact}")
        print()


def run_ui(args: argparse.Namespace) -> None:
    if importlib.util.find_spec("streamlit") is None:
        raise SystemExit(
            "Streamlit is not installed. Install the optional UI dependency first:\n"
            "  python -m pip install -e .[ui]\n"
            "or\n"
            "  python -m pip install streamlit"
        )
    app_path = Path(__file__).resolve().parent / "ui_app.py"
    command = [sys.executable, "-m", "streamlit", "run", str(app_path)]
    if args.port is not None:
        command += ["--server.port", str(args.port)]
    script_args = []
    if args.lake is not None:
        script_args += ["--lake", str(args.lake)]
    if script_args:
        command += ["--", *script_args]
    returncode = subprocess.call(command, cwd=os.getcwd())
    if returncode != 0:
        raise SystemExit(returncode)


def _find_raw_pdf(lake: Path) -> Path:
    raw_dir = lake / "raw"
    pdfs = sorted(raw_dir.glob("*.pdf")) if raw_dir.exists() else []
    if not pdfs:
        raise SystemExit(
            f"No PDF under {raw_dir}. Pass --pdf, or re-ingest without --no-raw-copy."
        )
    return pdfs[0]


def _model_summary(model: ocrlib.OffsetModel) -> str:
    return "; ".join(
        f"pdf {segment.start_page}->offset {segment.offset}"
        for segment in model.segments
    )


def _results_from_model(
    model: ocrlib.OffsetModel, total_pages: int
) -> dict[int, "ocrlib.FolioResult"]:
    results = {}
    for page in range(1, total_pages + 1):
        folio = model.folio_at(page)
        source = "model" if folio is not None else "front-matter"
        results[page] = ocrlib.FolioResult(page, folio, source)
    return results


def _load_model(model_path: Path) -> ocrlib.OffsetModel:
    data = json.loads(model_path.read_text(encoding="utf-8"))
    segments = tuple(
        ocrlib.OffsetSegment(s["start_page"], s["offset"]) for s in data["segments"]
    )
    return ocrlib.OffsetModel(segments, data["first_page"], data["last_page"])


def _folios_calibrated(
    client, pdf_path, total_pages, args, folio_cache_path, model_path
):
    if model_path.exists() and not args.rebuild:
        model = _load_model(model_path)
        print(f"Reusing offset model {model_path}: {_model_summary(model)}")
    else:
        print(f"Calibrating offset model from {pdf_path.name} via {args.endpoint} ...")
        body = ocrlib.detect_body_start(
            client, pdf_path, total_pages, total_pages, args.dpi
        )
        if body is None:
            raise SystemExit(
                "Could not detect the first body page with an arabic folio. "
                "Pass --all-pages for exhaustive OCR instead."
            )
        body_start, body_folio = body
        print(
            f"  body starts at pdf {body_start} (printed {body_folio}, "
            f"offset {body_start - body_folio})"
        )

        def log(page, used, folio, offset):
            note = "" if used == page else f" (used pdf {used})"
            print(f"  probe pdf {page}{note} -> folio {folio}, offset {offset}")

        model = ocrlib.calibrate_offsets(
            client, pdf_path, body_start, total_pages,
            max_folio=total_pages, dpi=args.dpi, log=log,
        )
        print(f"  offset segments: {_model_summary(model)}")

        if args.verify > 0:
            span = model.last_page - model.first_page
            step = max(1, span // (args.verify + 1))
            samples = list(
                range(model.first_page + step, model.last_page, step)
            )[: args.verify]
            mismatches = ocrlib.verify_model(
                client, pdf_path, model, samples, max_folio=total_pages, dpi=args.dpi
            )
            if mismatches:
                print(f"  WARNING: {len(mismatches)} verification mismatch(es):")
                for page, predicted, actual in mismatches:
                    print(f"    pdf {page}: model {predicted} vs OCR {actual}")
            else:
                print(f"  verified {len(samples)} sample page(s); all consistent")

        model_path.write_text(
            json.dumps(model.to_dict(), indent=2), encoding="utf-8"
        )
        print(f"  wrote offset model: {model_path}")

    results = _results_from_model(model, total_pages)
    ocrlib.save_folio_map(folio_cache_path, results)
    return results


def _folios_exhaustive(client, pdf_path, rows, total_pages, args, folio_cache_path):
    needed = set()
    for row in rows:
        needed.add(int(row["pdf_start_page"]))
        needed.add(int(row["pdf_end_page"]))
    pages = sorted(needed)
    print(f"Exhaustive OCR: {len(pages)} boundary page(s) via {args.endpoint}")
    cache = {} if args.rebuild else ocrlib.load_cache(folio_cache_path)
    if cache:
        print(f"Reusing {len(cache)} cached folio(s)")
    done = 0

    def progress(page, folio, source):
        nonlocal done
        done += 1
        if done % 25 == 0 or done == len(pages):
            print(f"  {done}/{len(pages)} pages (last pdf {page} -> folio {folio})")

    results = ocrlib.build_folio_map(
        client, pdf_path, pages, max_folio=total_pages,
        dpi=args.dpi, cache=cache, progress=progress,
    )
    ocrlib.save_folio_map(folio_cache_path, results)
    return results


def _folios_via_ocr(lake, pdf_path, rows, args) -> None:
    endpoint, endpoint_source = backendslib.resolve_endpoint(args.endpoint)
    args.endpoint = endpoint  # downstream progress lines print the resolved URL
    print(f"OCR endpoint: {endpoint} ({endpoint_source})")
    client = ocrlib.OcrClient(endpoint)
    if not client.health():
        raise SystemExit(
            f"OCR endpoint not reachable at {endpoint} ({endpoint_source}).\n"
            "Start your local serving first (LM Studio, llama.cpp llama-server "
            "with an OCR GGUF and --mmproj, Ollama), or point dokey at a "
            "running one:\n"
            "  dokey backend            # discover local servers\n"
            "  dokey backend --set URL  # remember one"
        )
    total_pdf_pages = max(int(row["pdf_end_page"]) for row in rows)
    folio_cache_path = searchlib.index_path(lake).with_name("folios.jsonl")
    model_path = searchlib.index_path(lake).with_name("offset_model.json")
    started = time.monotonic()
    if args.all_pages:
        results = _folios_exhaustive(
            client, pdf_path, rows, total_pdf_pages, args, folio_cache_path
        )
    else:
        results = _folios_calibrated(
            client, pdf_path, total_pdf_pages, args, folio_cache_path, model_path
        )
    elapsed = time.monotonic() - started
    modeled = sum(1 for r in results.values() if r.source == "model")
    ocr_read = sum(1 for r in results.values() if r.source == "ocr")
    interpolated = sum(1 for r in results.values() if r.source == "interpolated")
    unresolved = sum(1 for r in results.values() if r.folio is None)
    print(
        f"Folio map: {ocr_read} OCR, {modeled} modeled, {interpolated} interpolated, "
        f"{unresolved} unresolved | {client.calls} OCR calls in {elapsed:.0f}s"
    )
    for row in rows:
        for key in ("printed_start_page", "printed_end_page", "folio_source"):
            row.pop(key, None)
        start = results.get(int(row["pdf_start_page"]))
        end = results.get(int(row["pdf_end_page"]))
        row["printed_start_page"] = start.folio if start else None
        row["printed_end_page"] = end.folio if end else None
        row["folio_source"] = start.source if start else "unresolved"


def run_folios(args: argparse.Namespace) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    lake = resolve_lake(args.lake)
    pdf_path = args.pdf or _find_raw_pdf(lake)

    rows = searchlib._read_jsonl(lake / "silver" / "sections.jsonl")
    if not rows:
        raise SystemExit("Empty section manifest; run `dokey ingest` first.")

    used = None
    if args.source in ("auto", "toc"):
        toc_map, toc_pages = folioslib.build_toc_map(pdf_path)
        if toc_map and (len(toc_map) >= 10 or args.source == "toc"):
            print(
                f"TOC folios: {len(toc_map)} numbered entries from "
                f"pdf pages {toc_pages or '?'} of {pdf_path.name}"
            )
            stats = folioslib.apply_folios(rows, toc_map)
            print(
                f"  matched {stats.matched}, derived {stats.derived}, "
                f"front-matter {stats.front_matter} of {stats.total} sections "
                f"| offset range {stats.offset_min}..{stats.offset_max}"
            )
            used = "toc"
        elif args.source == "toc":
            raise SystemExit(
                "No usable text Table of Contents found. For a scanned PDF, "
                "use --source ocr."
            )
        else:
            print(
                f"TOC parse yielded {len(toc_map)} entries; falling back to OCR."
            )

    if used is None:
        _folios_via_ocr(lake, pdf_path, rows, args)

    backup = lake / "silver" / "sections.prefolio.jsonl"
    if not backup.exists():
        backup.write_text(
            (lake / "silver" / "sections.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        print(f"Backed up original manifest: {backup}")

    write_manifest_rows(lake, rows)
    print("Updated manifest with printed_start_page / printed_end_page.")

    stats = searchlib.build_index(lake)
    print(
        f"Rebuilt index: {stats.db_path} "
        f"({stats.sections} sections, {stats.pages} pages)"
    )


if __name__ == "__main__":
    main()
