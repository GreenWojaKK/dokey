"""Document import forms and flow-document ingest actions."""
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
from dokey import hwp as hwplib
from dokey import mdunit
from dokey import pickers as pickerslib
from dokey import sheets as sheetslib
from dokey.ui.common import (
    _active_project_root,
    _language_profile_input,
    _project_output_dir,
    _report_failure,
    _run_button,
    _section_depth_input,
    _write_items_input,
    t,
)
from dokey.ui.ingest_pdf import _auto_ingest_form, _manual_ingest_form
from dokey.ui.ingest_sheet import _sheet_ingest_form
from dokey.ui.preview import _offer_preview


def import_open() -> bool:
    return bool(st.session_state.get("_import_open"))


def import_control(lake: Path | None) -> None:
    if lake is None:
        return
    if import_open():
        if st.button(
            t("close_import"),
            key="import_close",
            icon=":material/close:",
            use_container_width=True,
        ):
            st.session_state["_import_open"] = False
            st.rerun()
        return
    if st.button(
        t("ingest_book"),
        key="import_open",
        type="primary",
        icon=":material/add:",
        use_container_width=True,
    ):
        st.session_state["_import_open"] = True
        st.rerun()


def import_view() -> None:
    """Render the document import flow in the main pane."""
    st.subheader(t("ingest_book"))
    st.caption(t("adding_to_project", project=_active_project_root().name))
    upload = _document_picker()
    _ingest_form_for(upload)


def _ingest_form_for(upload) -> None:
    """Choose the form from the input format."""
    if upload is not None and hwplib.is_hwp(Path(upload.name)):
        _hwp_ingest_form(upload)
        return
    if upload is not None and sheetslib.is_spreadsheet(Path(upload.name)):
        _sheet_ingest_form(upload)
        return
    if upload is not None and converterslib.is_flow_document(Path(upload.name)):
        _flow_ingest_form(upload)
        return
    if upload is not None and mdunit.is_markdown(Path(upload.name)):
        _md_ingest_form(upload)
        return
    mode = st.radio(
        t("ingest_mode"),
        ["auto", "manual"],
        format_func=lambda value: t(f"ingest_mode_{value}"),
        key="ing_mode",
        horizontal=True,
        help=t("ingest_mode_help"),
    )
    if mode == "auto":
        _auto_ingest_form(upload)
    else:
        _manual_ingest_form(upload)


DOCUMENT_TYPES = [
    "pdf",
    "hwp",
    "hwpx",
    "md",
    "markdown",
    "xlsx",
    "xlsm",
    "xlsb",
    "xls",
    "ods",
    "docx",
    "pptx",
    "html",
    "htm",
    "epub",
]


def _document_picker():
    """Choose locally from the active project, with upload as the web fallback."""
    if not pickerslib.HAS_FILE_PICKER:
        return st.file_uploader(
            t("document_file"),
            type=DOCUMENT_TYPES,
            key="ing_pdf",
        )

    selected = st.session_state.get("_ingest_local_file")
    selected_path = Path(selected) if selected else None
    if selected_path is not None and not selected_path.is_file():
        st.session_state.pop("_ingest_local_file", None)
        selected_path = None
        st.warning(t("document_missing"))

    label = t("change_document") if selected_path else t("choose_document")
    columns = st.columns([2, 1, 3])
    if columns[0].button(
        label,
        key="ing_choose_file",
        icon=":material/file_open:",
        use_container_width=True,
    ):
        chosen = pickerslib.choose_file(
            t("choose_document_title"),
            initial_dir=_active_project_root(),
        )
        if chosen:
            st.session_state["_ingest_local_file"] = chosen
            st.rerun()
    if columns[1].button(
        t("clear_document"),
        key="ing_clear_file",
        disabled=selected_path is None,
        icon=":material/close:",
        use_container_width=True,
    ):
        st.session_state.pop("_ingest_local_file", None)
        st.rerun()

    if selected_path is not None:
        st.caption(t("selected_document", path=selected_path))
        return pickerslib.SelectedFile(selected_path)
    st.caption(t("document_file"))
    return None


def _hwp_ingest_form(upload) -> None:
    """Render the HWP/HWPX converter-backed ingest form."""
    converter, _ = hwplib.resolve_converter()
    if converter is None:
        st.warning(t("hwp_offline"))
    else:
        st.caption(t("hwp_online", cmd=converter.display()))
    name_column, _spacer = st.columns([2, 3])
    lake_name = name_column.text_input(
        t("library_name_optional"), value="", key="hwp_name"
    )
    if _run_button("hwp_run", disabled=converter is None):
        run_hwp_ingest_ui(upload, lake_name)


def run_hwp_ingest_ui(upload, lake_name: str) -> None:
    """Stage an HWP input, run the CLI path, and select the new library."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_hwp_"))
    hwp_path = work / upload.name
    hwp_path.write_bytes(upload.getvalue())

    name = lake_name.strip() or Path(upload.name).stem
    out_dir = _project_output_dir(name)
    args = SimpleNamespace(input=hwp_path, output_dir=out_dir)

    log = io.StringIO()
    try:
        with st.spinner(t("ingesting", name=upload.name)), contextlib.redirect_stdout(
            log
        ):
            dokey_cli.run_hwp_ingest(args)
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


def _flow_ingest_form(upload) -> None:
    """Convert a flow document, then unitize its heading structure."""
    st.caption(t("flow_input_caption"))
    offered = converterslib.offered()
    if not offered:
        st.warning(t("flow_converter_offline"))
        selected = None
    else:
        selected = st.selectbox(
            t("flow_converter_choice"),
            offered,
            format_func=lambda conv: (
                f"{conv.kind} — {converterslib.yields_label(conv.kind)}"
            ),
            key="flow_converter",
            help=t("flow_converter_choice_help"),
        )
    essentials = st.columns(3)
    with essentials[0]:
        lake_name = st.text_input(
            t("library_name_optional"), value="", key="flow_name"
        )
    with essentials[1]:
        depth = _section_depth_input("flow_depth")
    with essentials[2]:
        profile = _language_profile_input("flow_profile")
    write_items = _write_items_input("flow_items")
    if _run_button("flow_run", disabled=selected is None):
        run_flow_ingest_ui(
            upload, lake_name, depth, profile, write_items, selected.kind
        )


def run_flow_ingest_ui(
    upload,
    lake_name: str,
    section_depth: str = "auto",
    profile: str = "auto",
    write_items: bool = True,
    converter: str | None = None,
) -> None:
    """Stage a flow document, run the CLI path, and select the new library."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_flow_"))
    doc_path = work / upload.name
    doc_path.write_bytes(upload.getvalue())

    name = lake_name.strip() or Path(upload.name).stem
    out_dir = _project_output_dir(name)
    args = SimpleNamespace(
        input=doc_path,
        output_dir=out_dir,
        section_depth=section_depth,
        profile=profile,
        no_items=not write_items,
        converter=converter,
    )

    log = io.StringIO()
    try:
        with st.spinner(t("ingesting", name=upload.name)), contextlib.redirect_stdout(
            log
        ):
            dokey_cli.run_flow_ingest(args)
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


def _md_ingest_form(upload) -> None:
    """Render the Markdown ingest form."""
    st.caption(t("md_input_caption"))
    essentials = st.columns(3)
    with essentials[0]:
        lake_name = st.text_input(
            t("library_name_optional"), value="", key="md_name"
        )
    with essentials[1]:
        depth = _section_depth_input("md_depth")
    with essentials[2]:
        profile = _language_profile_input("md_profile")
    with st.expander(t("advanced_overrides"), expanded=False):
        overrides = st.columns(2)
        with overrides[0]:
            blocks_upload = st.file_uploader(
                t("source_blocks"),
                type=["json"],
                key="md_blocks",
                help=t("source_blocks_help"),
            )
        with overrides[1]:
            write_items = _write_items_input("md_items")
    if upload is not None:
        _offer_preview("md", upload, depth, profile, blocks_upload)
    if _run_button("md_run"):
        run_md_ingest_ui(
            upload, lake_name, depth, profile, blocks_upload, write_items
        )


def run_md_ingest_ui(
    upload,
    lake_name: str,
    section_depth: str = "auto",
    profile: str = "auto",
    blocks_upload=None,
    write_items: bool = True,
) -> None:
    """Stage Markdown, run the CLI path, and select the new library."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_md_"))
    md_path = work / upload.name
    md_path.write_bytes(upload.getvalue())

    blocks_path = None
    if blocks_upload is not None:
        blocks_path = md_path.with_suffix(".json")
        blocks_path.write_bytes(blocks_upload.getvalue())

    name = lake_name.strip() or Path(upload.name).stem
    out_dir = _project_output_dir(name)
    args = SimpleNamespace(
        input=md_path,
        output_dir=out_dir,
        section_depth=section_depth,
        profile=profile,
        blocks=blocks_path,
        no_items=not write_items,
    )

    log = io.StringIO()
    try:
        with st.spinner(t("ingesting", name=upload.name)), contextlib.redirect_stdout(
            log
        ):
            dokey_cli.run_md_ingest(args)
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
