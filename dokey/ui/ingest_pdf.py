"""PDF ingest controls and runners for the Streamlit UI."""

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
from dokey import search as searchlib
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


def run_ingest_ui(
    pdf_upload,
    toc_source: str,
    toc_upload,
    toc_format: str,
    page_offset: int,
    section_overlap: int,
    recover_folios: bool,
    lake_name: str,
) -> None:
    """Save a staged PDF and run the manual ingest workflow."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_"))
    pdf_path = work / pdf_upload.name
    pdf_path.write_bytes(pdf_upload.getvalue())

    toc_path = None
    if toc_source == "file" and toc_upload is not None:
        toc_path = work / toc_upload.name
        toc_path.write_bytes(toc_upload.getvalue())

    name = lake_name.strip() or Path(pdf_upload.name).stem
    out_dir = _project_output_dir(name)

    ingest_args = SimpleNamespace(
        command="ingest",
        input=pdf_path,
        toc=toc_path,
        toc_from_outline=(toc_source == "outline"),
        toc_from_page=(toc_source == "printed"),
        toc_page=None,
        no_ocr_fallback=False,
        ocr_endpoint=None,
        ocr_dpi=200,
        toc_format=toc_format,
        outline_max_level=1,
        output_dir=out_dir,
        page_offset=page_offset,
        max_content_page=0,
        section_overlap=section_overlap,
        no_raw_copy=False,
        no_page_text=False,
        no_pdf_artifacts=False,
    )
    log = io.StringIO()
    try:
        with st.spinner(
            t("ingesting", name=pdf_upload.name)
        ), contextlib.redirect_stdout(log):
            dokey_cli.ingest(ingest_args)
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
                with contextlib.redirect_stdout(log):
                    searchlib.build_index(out_dir)
                st.warning(t("skipped_page_recovery", error=exc))
        else:
            with st.spinner(t("building_index")), contextlib.redirect_stdout(log):
                searchlib.build_index(out_dir)
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


def run_ingest_auto_ui(
    pdf_upload,
    page_offset: int | None,
    section_overlap: int | None,
    toc_pages: list[int] | None,
    recover_folios: bool,
    lake_name: str,
    read_method: str = "auto",
    section_depth: str = "auto",
    profile: str = "auto",
    write_items: bool = True,
    converter: str | None = None,
) -> None:
    """Save a staged PDF and run the automatic ingest workflow."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_"))
    pdf_path = work / pdf_upload.name
    pdf_path.write_bytes(pdf_upload.getvalue())

    name = lake_name.strip() or Path(pdf_upload.name).stem
    out_dir = _project_output_dir(name)

    auto_args = SimpleNamespace(
        command="auto",
        input=pdf_path,
        output_dir=out_dir,
        section_depth=section_depth,
        profile=profile,
        no_items=not write_items,
        page_offset=page_offset,
        toc_page=toc_pages,
        section_overlap=section_overlap,
        ocr_endpoint=None,
        convert=read_method,
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


def _parse_optional_int(text: str) -> tuple[int | None, bool]:
    """Parse an optional integer and report whether the input was valid."""
    text = text.strip()
    if not text:
        return None, True
    try:
        return int(text), True
    except ValueError:
        return None, False


def _parse_int_list(text: str) -> list[int] | None:
    """Parse comma- or space-separated integers."""
    values = []
    for part in text.replace(",", " ").split():
        try:
            values.append(int(part))
        except ValueError:
            pass
    return values or None


def converter_status(offered=None) -> bool:
    """Show the available converters and the output each one retains."""
    if offered is None:
        offered = converterslib.offered()
    if not offered:
        st.caption(t("converter_offline"))
        return False
    entries = [
        f"{converter.kind} \u2014 {converterslib.yields_label(converter.kind)}"
        for converter in offered
    ]
    st.caption(t("converters_discovered", list=" \u00b7 ".join(entries)))
    return True


def _auto_ingest_form(pdf_upload) -> None:
    """Render the automatic PDF ingest controls."""
    machine = converterslib.offered()
    has_converter = converter_status(machine)
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
        recover = st.checkbox(t("recover_printed"), value=True, key="auto_recover")
    with switches[1]:
        write_items = _write_items_input("auto_items")
    with st.expander(t("advanced_overrides"), expanded=False):
        overrides = st.columns(2)
        with overrides[0]:
            read_method = st.selectbox(
                t("read_method"),
                ["auto", "never", "always"],
                format_func=lambda value: t(f"read_method_{value}"),
                key="auto_convert",
                help=t("read_method_help"),
                disabled=not has_converter,
            )
            converter_choice = st.selectbox(
                t("pdf_converter_choice"),
                [None] + [conv.kind for conv in machine],
                format_func=lambda value: (
                    t("pdf_converter_auto")
                    if value is None
                    else f"{value} — {converterslib.yields_label(value)}"
                ),
                key="auto_converter",
                help=t("pdf_converter_choice_help"),
                disabled=not has_converter,
            )
            offset_text = st.text_input(
                t("page_offset_auto"),
                value="",
                key="auto_offset",
                help=t("page_offset_auto_help"),
            )
        with overrides[1]:
            overlap_choice = st.selectbox(
                t("section_overlap"),
                ["auto", "0", "1", "2"],
                format_func=lambda value: (
                    t("overlap_auto") if value == "auto" else value
                ),
                key="auto_overlap",
                help=t("section_overlap_auto_help"),
            )
            toc_page_text = st.text_input(
                t("toc_page_pin"),
                value="",
                key="auto_tocpage",
                help=t("toc_page_pin_help"),
            )
    if pdf_upload is not None:
        _offer_preview("pdf", pdf_upload, depth, profile)
    if _run_button("auto_run", disabled=pdf_upload is None):
        page_offset, ok = _parse_optional_int(offset_text)
        if not ok:
            st.error(t("invalid_number"))
            return
        section_overlap = (
            None if overlap_choice == "auto" else int(overlap_choice)
        )
        toc_pages = _parse_int_list(toc_page_text)
        run_ingest_auto_ui(
            pdf_upload,
            page_offset,
            section_overlap,
            toc_pages,
            recover,
            lake_name,
            read_method if has_converter else "never",
            depth,
            profile,
            write_items,
            converter_choice if has_converter else None,
        )


def _manual_ingest_form(pdf_upload) -> None:
    """Render the manual PDF ingest controls."""
    toc_source = st.radio(
        t("toc_method"),
        ["outline", "file", "printed"],
        format_func=lambda value: t(f"toc_{value}"),
        key="ing_toc_source",
        horizontal=True,
        help=t("toc_help"),
    )
    toc_upload, toc_format = None, "auto"
    if toc_source == "file":
        given = st.columns(2)
        with given[0]:
            toc_upload = st.file_uploader(
                t("toc_file_label"), type=["csv", "txt"], key="ing_toc"
            )
        with given[1]:
            toc_format = st.selectbox(
                t("toc_format"),
                ["auto", "csv", "text"],
                format_func=lambda value: t(f"format_{value}"),
                key="ing_toc_fmt",
            )
    essentials = st.columns(3)
    with essentials[0]:
        lake_name = st.text_input(
            t("library_name_optional"), value="", key="ing_name"
        )
    with essentials[1]:
        page_offset = st.number_input(
            t("page_offset"), value=0, step=1, key="ing_offset"
        )
    with essentials[2]:
        section_overlap = st.number_input(
            t("section_overlap"),
            value=1,
            min_value=0,
            step=1,
            key="ing_overlap",
            help=t("section_overlap_help"),
        )
    recover = st.checkbox(t("recover_printed"), value=True, key="ing_recover")
    if _run_button("ing_run", disabled=pdf_upload is None):
        run_ingest_ui(
            pdf_upload,
            toc_source,
            toc_upload,
            toc_format,
            int(page_offset),
            int(section_overlap),
            recover,
            lake_name,
        )
