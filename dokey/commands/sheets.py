from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .. import blocks as blockslib
from .. import convert as convertlib
from .. import converters as converterslib
from .. import sheets as sheetslib
from .common import _default_lake_dir
from .lake import _ingest_markdown, _write_sections_lake


def run_sheet_ingest(args: argparse.Namespace) -> None:
    """Ingest a spreadsheet by reading the workbook's own file.

    A workbook carries its structure -- coordinates, types, merges, sheet
    names -- so there is nothing for a layout converter to reconstruct, and
    it is read directly: the OOXML zip through the standard library, the
    legacy binary through xlrd. The container is recognized by its bytes
    rather than its suffix, because files in the wild wear extensions their
    bytes do not honour. Only a format neither reader opens still goes to the
    converter, and an explicitly supplied block stream is honoured as the
    instruction it is.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    input_path = args.input
    if not input_path.is_file():
        raise SystemExit(f"Spreadsheet not found: {input_path}")
    output_dir = getattr(args, "output_dir", None) or _default_lake_dir(input_path)
    explicit_blocks = getattr(args, "blocks", None)
    # Naming a converter is an instruction, and an instruction outranks the
    # native reader -- silently reading directly after being told otherwise
    # would be doing something other than what was asked.
    prefer = getattr(args, "converter", None)

    if explicit_blocks is None and prefer is not None:
        choice = converterslib.choose(input_path, prefer=prefer)
        if choice is not None and "blocks" not in choice.yields:
            # A markdown-only converter keeps sheet identity as headings --
            # measured: one "## name" per sheet -- and drops everything else
            # the file states. The route is honoured because it was asked
            # for, and the cost is said before the work starts.
            print(f"{input_path.name}: converting with {choice.display()}")
            print(
                "Note: this route keeps sheets and tables only. Pictures, "
                "text boxes, merges and cell coordinates are not carried; "
                "the direct read (default) keeps them."
            )
            options = convertlib.load_options()
            started = time.time()
            produced = convertlib.convert(
                input_path,
                choice.converter,
                to=("md",),
                ocr=False,
                device=options.device,
                images=options.images or "placeholder",
                timeout=getattr(args, "timeout", convertlib.DEFAULT_TIMEOUT),
            )
            render = next(
                (path for path in produced if path.suffix == ".md"), None
            )
            if render is None:
                raise SystemExit("The converter produced no Markdown to ingest.")
            print(f"Converted in {time.time() - started:.1f}s: {render.name}")
            _ingest_markdown(
                render.read_text(encoding="utf-8"),
                input_path=input_path,
                output_dir=output_dir,
                fallback_title=input_path.stem,
                source_label="spreadsheet",
                profile=getattr(args, "profile", "auto"),
                write_items=not getattr(args, "no_items", False),
                provenance=(
                    f"converted by {choice.converter.display()} (markdown "
                    "only; pictures, text boxes, merges and coordinates not "
                    "carried)"
                ),
            )
            return

    if explicit_blocks is None and prefer is None and sheetslib.is_legacy_workbook(
        input_path
    ):
        read = sheetslib.read_xls(input_path)
        print(f"{input_path.name}: legacy workbook, read directly (no converter)")
        _finish_sheet_lake(read, input_path, output_dir)
        return
    if explicit_blocks is None and prefer is None and sheetslib.is_native_workbook(
        input_path
    ):
        read = sheetslib.read_xlsx(input_path)
        print(f"{input_path.name}: workbook, read directly (no converter)")
        _finish_sheet_lake(read, input_path, output_dir)
        return

    blocks = explicit_blocks or blockslib.find_source_blocks(input_path)
    if blocks is None:
        # Sheet identity travels in the block stream, so this fallback needs
        # a converter that yields one.
        choice = converterslib.choose(
            input_path,
            require_blocks=True,
            prefer=getattr(args, "converter", None),
        )
        if choice is None:
            raise SystemExit(convertlib.install_hint())
        converter = choice.converter
        print(f"{input_path.name}: converting with {choice.display()}")
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

def _finish_sheet_lake(
    read, input_path: Path, output_dir: Path
) -> None:
    """Write a directly-read workbook's lake, evidence layers included.

    The cells go to bronze under their own references -- the record the
    rendered sections can be checked against -- and what the workbook
    declares about its objects goes to silver with its anchors. Media bytes
    are carried as they are; reading a picture is a VLM's job, not this one's.
    """
    if not read.sections:
        raise SystemExit("No sheets to ingest: the workbook is empty.")
    print(f"Sheets: {read.report.summary()}")
    if read.cells:
        cells_path = sheetslib.write_cells(output_dir, read.cells)
        print(f"Wrote cells: {cells_path} ({len(read.cells)} cell(s))")
    if read.objects:
        objects_path = sheetslib.write_objects(output_dir, read.objects)
        print(
            f"Wrote objects: {objects_path} "
            f"({read.report.charts} chart(s), {read.report.images} image(s), "
            f"{read.report.shapes} shape(s))"
        )
    if read.figures:
        figures_path = sheetslib.write_figures(output_dir, read.figures)
        induced = sum(1 for row in read.figures if row["basis"] == "induced")
        print(
            f"Wrote sheet figures: {figures_path} "
            f"({len(read.figures)} figure(s), {induced} assembled from parts "
            "the file left loose)"
        )
    if read.media:
        media_dir = sheetslib.write_media(output_dir, read.media)
        print(f"Wrote media: {media_dir} ({len(read.media)} file(s))")
    extra = {"sheets": read.report.as_dict()}
    if read.regions:
        extra["regions"] = read.regions
    _write_sections_lake(
        read.sections,
        input_path=input_path,
        output_dir=output_dir,
        source_label="spreadsheet",
        extra_report=extra,
    )
