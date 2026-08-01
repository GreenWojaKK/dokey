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
    t,
)
from dokey.ui.preview import _offer_preview


def run_ingest_auto_ui(
    pdf_upload,
    toc_upload,
    recover_folios: bool,
    lake_name: str,
    reader: str | None = None,
    section_depth: str = "auto",
    profile: str = "auto",
    write_items: bool = True,
) -> None:
    """Save the staged inputs and run the sequential ingest workflow.

    ``reader`` maps onto the CLI's two knobs: None lets dokey read first and
    hand page images to a converter, "dokey" forbids conversion, and a
    converter kind converts with that tool as given.
    """
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_"))
    pdf_path = work / pdf_upload.name
    pdf_path.write_bytes(pdf_upload.getvalue())

    toc_path = None
    if toc_upload is not None:
        toc_path = work / toc_upload.name
        toc_path.write_bytes(toc_upload.getvalue())

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
        page_offset=None,
        toc=toc_path,
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
        if recover_folios:
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

    st.success(t("ingested", path=out_dir))
    with st.expander(t("ingest_log"), expanded=False):
        st.code(log.getvalue() or t("no_output"))
    st.session_state["_new_lake"] = str(out_dir)
    st.rerun()


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

    The table of contents, the page offset, and each boundary's overlap are
    resolved sequentially and cross-checked against the document itself, so
    the form asks only what no document can state: which reader, what to call
    the library, how deep to cut, and an optional TOC file that is followed
    as given.
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
    toc_column, _spacer = st.columns([2, 3])
    toc_upload = toc_column.file_uploader(
        t("toc_file_optional"),
        type=["csv", "txt"],
        key="auto_toc",
        help=t("toc_file_optional_help"),
    )
    switches = st.columns(3)
    with switches[0]:
        recover = st.checkbox(t("recover_printed"), value=True, key="auto_recover")
    with switches[1]:
        write_items = _write_items_input("auto_items")
    if pdf_upload is not None:
        _offer_preview("pdf", pdf_upload, depth, profile)
    if _run_button("auto_run", disabled=pdf_upload is None):
        run_ingest_auto_ui(
            pdf_upload,
            toc_upload,
            recover,
            lake_name,
            reader,
            depth,
            profile,
            write_items,
        )
