"""The PDF ingest form and its runner for the Streamlit UI."""

from __future__ import annotations

import contextlib
import io
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import streamlit as st

from dokey import cli as dokey_cli
from dokey import converters as converterslib
from dokey.ui.common import (
    _language_profile_input,
    _project_output_dir,
    _report_failure,
    _run_button,
    _section_depth_input,
    _write_items_input,
    finish_ingest,
    t,
)
from dokey.ui.preview import _offer_preview


def run_ingest_auto_ui(
    pdf_upload,
    lake_name: str,
    reader: str | None = None,
    section_depth: str = "auto",
    profile: str = "auto",
    write_items: bool = True,
    write_markdown: bool = False,
) -> None:
    """Save the staged PDF and run the sequential ingest workflow.

    ``reader`` maps onto the CLI's two knobs: None lets dokey read first and
    hand page images to a converter, "dokey" forbids conversion, and a
    converter kind converts with that tool as given. The printed page
    numbers are always recovered afterwards; a document that defeats the
    recovery says so in a warning and the ingest stands.
    """
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_"))
    pdf_path = work / pdf_upload.name
    pdf_path.write_bytes(pdf_upload.getvalue())

    name = lake_name.strip() or Path(pdf_upload.name).stem
    out_dir = _project_output_dir(name)

    if reader is None:
        convert, converter = "auto", None
    elif reader == "dokey":
        convert, converter = "never", None
    else:
        convert, converter = "always", reader

    auto_args = SimpleNamespace(
        command="auto",
        input=pdf_path,
        output_dir=out_dir,
        section_depth=section_depth,
        profile=profile,
        no_items=not write_items,
        markdown=write_markdown,
        page_offset=None,
        toc=None,
        toc_format="auto",
        toc_page=None,
        section_overlap=None,
        ocr_endpoint=None,
        convert=convert,
        converter=converter,
        blocks=None,
    )
    log = io.StringIO()
    try:
        with st.spinner(
            t("ingesting", name=pdf_upload.name)
        ), contextlib.redirect_stdout(log):
            dokey_cli.run_auto(auto_args)
        folio_args = SimpleNamespace(
            lake=out_dir,
            pdf=None,
            source="toc",
            endpoint=None,
            all_pages=False,
            verify=8,
            dpi=200,
            rebuild=False,
        )
        try:
            with st.spinner(t("recovering_pages")), contextlib.redirect_stdout(log):
                dokey_cli.run_folios(folio_args)
        except SystemExit as exc:
            st.warning(t("skipped_page_recovery", error=exc))
    except SystemExit as exc:
        _report_failure("ingest_failed", exc, log)
        return
    except Exception as exc:
        _report_failure("ingest_error", exc, log, trace=traceback.format_exc())
        return

    finish_ingest(out_dir, log)


def _reader_options() -> list[tuple[str | None, str]]:
    """The reading routes for a PDF: automatic, dokey itself, each converter."""
    options: list[tuple[str | None, str]] = [
        (None, t("pdf_reader_auto")),
        ("dokey", t("pdf_reader_dokey")),
    ]
    for converter in converterslib.offered():
        options.append(
            (
                converter.kind,
                f"{converter.kind} — {converterslib.yields_label(converter.kind)}",
            )
        )
    return options


def _pdf_ingest_form(pdf_upload) -> None:
    """Render the one PDF form: everything else is read from the document.

    The table of contents, the page offset, each boundary's overlap, and the
    printed page numbers are resolved sequentially and cross-checked against
    the document itself, so the form asks only what no document can state:
    which reader, what to call the library, and how deep to cut.
    """
    reader = st.selectbox(
        t("pdf_reader"),
        _reader_options(),
        format_func=lambda option: option[1],
        key="pdf_reader",
        help=t("pdf_reader_help"),
    )[0]
    essentials = st.columns(3)
    with essentials[0]:
        lake_name = st.text_input(
            t("library_name_optional"), value="", key="auto_name"
        )
    with essentials[1]:
        depth = _section_depth_input("auto_depth")
    with essentials[2]:
        profile = _language_profile_input("auto_profile")
    switches = st.columns(3)
    with switches[0]:
        write_items = _write_items_input("auto_items")
    with switches[1]:
        # Splitting a PDF yields PDFs; this asks for the same sections as text
        # as well. It stands on its own line because it is a question about
        # what to keep, not about how the document is read.
        write_markdown = st.checkbox(
            t("write_markdown"),
            value=False,
            key="auto_markdown",
            help=t("write_markdown_help"),
        )
    if pdf_upload is not None:
        _offer_preview("pdf", pdf_upload, depth, profile)
    if _run_button("auto_run", disabled=pdf_upload is None):
        run_ingest_auto_ui(
            pdf_upload,
            lake_name,
            reader,
            depth,
            profile,
            write_items,
            write_markdown,
        )
