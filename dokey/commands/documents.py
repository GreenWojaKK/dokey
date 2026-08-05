from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .. import blocks as blockslib
from .. import convert as convertlib
from .. import converters as converterslib
from .. import hwp as hwplib
from .common import _lake_dir, _section_depth
from .lake import _ingest_markdown


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

def run_hwp_ingest(args: argparse.Namespace) -> None:
    """Ingest an .hwp/.hwpx by converting it to Markdown (BYO converter) and
    unitizing the heading hierarchy into sections."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    input_path = args.input
    if not input_path.is_file():
        raise SystemExit(f"HWP file not found: {input_path}")
    # No converter in the path here: the HWP seam resolves one tool by
    # discovery and states no kind for itself, so there is never a second
    # name to tell apart. Name the lake yourself with --output-dir.
    output_dir = _lake_dir(args, input_path)

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

def run_flow_ingest(args: argparse.Namespace) -> None:
    """Ingest a flow document (docx, pptx, html, epub): convert, then unitize.

    A flow format states no pages -- its pagination is a rendering artifact --
    so by the evidence rule a markdown-only converter loses nothing
    structural, and the lightest tool on the machine is fully adequate. A
    converter that also yields a block stream is still preferred (it can only
    know more), and whichever one ran is recorded in the unitize report.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    input_path = args.input
    if not input_path.is_file():
        raise SystemExit(f"Document not found: {input_path}")

    options = convertlib.load_options()
    started = time.time()

    def run(choice):
        print(f"{input_path.name}: converting with {choice.display()}")
        return convertlib.convert(
            input_path,
            choice.converter,
            to=convertlib.DEFAULT_TARGETS if "blocks" in choice.yields else ("md",),
            ocr=False,
            device=options.device,
            images=options.images or "placeholder",
            timeout=getattr(args, "timeout", convertlib.DEFAULT_TIMEOUT),
        )

    choice, produced = converterslib.attempt(
        input_path, run, prefer=getattr(args, "converter", None)
    )
    render = next((path for path in produced if path.suffix == ".md"), None)
    if render is None:
        raise SystemExit("The converter produced no Markdown to ingest.")
    print(
        f"Converted in {time.time() - started:.1f}s: "
        f"{', '.join(path.name for path in produced)}"
    )
    # Named only now: which converter opened the file is settled by running
    # it, so before this point there was nothing to name the lake after.
    output_dir = _lake_dir(
        args, input_path, convertlib.converter_slug(choice.converter)
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
        provenance=(
            f"converted by {choice.converter.display()} "
            f"({converterslib.yields_label(choice.converter.kind)})"
        ),
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
    # A render made by `dokey convert` already carries its converter in its
    # own filename, so the lake named after it is separate without help.
    output_dir = _lake_dir(args, input_path)

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

    if args.input is None:
        found = converterslib.discover()
        saved = convertlib.load_converter()
        if not found and saved is None:
            print("Converters: none found")
            print()
            print(convertlib.install_hint())
            return
        # The full listing lives here rather than in the app: a form asks
        # which converter reads *this* document, which is a different
        # question from what the machine has and what each one keeps.
        listed = ([("saved", saved)] if saved is not None else []) + [
            ("discovered", converter) for converter in found
        ]
        for source, converter in listed:
            print(
                f"{source.title()}: {converter.kind} — "
                f"{converterslib.yields_label(converter.kind)}"
            )
            print(f"  command: {converter.display()}")
            # "Known for", not "reads": what a tool opens is settled by
            # running it, and this list is only where dokey looks first.
            print(
                "  known for: "
                + " ".join(sorted(converterslib.known_suffixes(converter.kind)))
                + " (others are tried too)"
            )
        print(f"Saved defaults: {convertlib.load_options().describe()}")
        print("Convert a document with:  dokey convert <file.pdf>")
        print("  …and unitize what comes back:  dokey convert <file.pdf> --ingest")
        return

    input_path = args.input
    if not input_path.is_file():
        raise SystemExit(f"File not found: {input_path}")
    choice = converterslib.choose(
        input_path, prefer=getattr(args, "converter", None)
    )
    if choice is None:
        raise SystemExit(
            converterslib.flow_install_hint()
            if converterslib.is_flow_document(input_path)
            else convertlib.install_hint()
        )
    converter = choice.converter
    print(f"Converter: {choice.display()}")
    if choice.degraded:
        # The evidence rule's verdict: this source states pages and the
        # chosen converter will not keep them. Proceed, but say so now.
        print(
            "Note: this converter yields markdown only, so the PDF's pages "
            "will not survive -- sections will get synthetic page numbers. "
            "docling keeps them."
        )
    options = convertlib.load_options().merged(
        ocr_engine=args.ocr_engine,
        ocr_lang=args.ocr_lang,
        device=args.device,
        images=args.images if args.images != "placeholder" else None,
    )
    caution = convertlib.ocr_engine_caution(args.ocr, options.ocr_engine)
    if caution and "blocks" in choice.yields:
        print(f"Note: {caution}")
    targets = tuple(args.to) if args.to else convertlib.DEFAULT_TARGETS
    if "blocks" not in choice.yields:
        if args.to and "json" in targets:
            raise SystemExit(
                "This converter yields markdown only; --to json needs docling."
            )
        targets = ("md",)
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
    output_dir = _lake_dir(args, input_path, convertlib.converter_slug(converter))
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
