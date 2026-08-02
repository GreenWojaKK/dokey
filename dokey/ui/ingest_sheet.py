"""Spreadsheet ingest controls and runner for the Streamlit UI."""

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
from dokey import sheets as sheetslib
from dokey.ui.common import (
    _project_output_dir,
    _report_failure,
    _run_button,
    finish_ingest,
    t,
)
from dokey.ui.preview import _staged_key


def _sheet_read_summary(staged) -> str | None:
    """Summarize the native read and cache it for the staged file version."""
    key = _staged_key(staged)
    cache = st.session_state.setdefault("_sheet_summaries", {})
    if key in cache:
        return cache[key]
    summary = None
    try:
        path = getattr(staged, "path", None)
        suffix = Path(staged.name).suffix.lower()
        if suffix in sheetslib.LEGACY_SUFFIXES:
            if path is not None:
                summary = sheetslib.read_xls(Path(path)).report.summary()
        else:
            source = Path(path) if path is not None else io.BytesIO(staged.getvalue())
            summary = sheetslib.read_xlsx(source).report.summary()
    except (Exception, SystemExit):
        summary = None
    cache[key] = summary
    return summary


def _sheet_read_options(suffix: str) -> tuple[list[tuple[str | None, str]], bool]:
    """Return the available reading routes and whether the native route is blocked."""
    options: list[tuple[str | None, str]] = []
    blocked = False
    if suffix in sheetslib.LEGACY_SUFFIXES:
        options.append((None, t("sheet_read_native_legacy")))
        if not sheetslib.can_read_legacy():
            blocked = True
    elif not sheetslib.needs_converter(Path(f"x{suffix}")):
        options.append((None, t("sheet_read_native")))
    for converter in converterslib.offered():
        if "blocks" in converterslib.adapter_yields(converter.kind):
            label = t("sheet_read_converter", kind=converter.kind)
        else:
            label = t("sheet_read_converter_md", kind=converter.kind)
        options.append((converter.kind, label))
    options.sort(
        key=lambda option: (
            option[0] is not None,
            not converterslib.known_for(option[0] or "", suffix),
        )
    )
    return options, blocked


def _sheet_ingest_form(upload) -> None:
    """Render spreadsheet reading and ingest controls."""
    st.caption(t("sheet_input_caption"))
    suffix = Path(upload.name).suffix.lower()
    options, blocked = _sheet_read_options(suffix)
    if blocked:
        st.warning(t("sheet_xlrd_offline"))
    if not options:
        st.warning(t("sheet_converter_offline"))
        blocked = True
    chosen: str | None = None
    if options:
        selected = st.selectbox(
            t("sheet_read_path"),
            options,
            format_func=lambda option: option[1],
            key="sheet_read",
            help=t("sheet_read_path_help"),
        )
        chosen = selected[0]
    if chosen is None and options and not blocked:
        summary = _sheet_read_summary(upload)
        if summary:
            st.caption(t("sheet_will_read", summary=summary))
    path = getattr(upload, "path", None)
    names = sheetslib.sheet_names(path if path else io.BytesIO(upload.getvalue()))
    named = [name for name in names if name.strip()]
    if named:
        st.caption(
            t("sheet_sections_caption", count=len(named), names=", ".join(named))
        )
    else:
        st.caption(t("sheet_names_unreadable"))
    name_column, _spacer = st.columns([2, 3])
    lake_name = name_column.text_input(
        t("library_name_optional"), value="", key="sheet_name"
    )
    if _run_button("sheet_run", disabled=blocked):
        run_sheet_ingest_ui(upload, lake_name, chosen)


def run_sheet_ingest_ui(
    upload, lake_name: str, converter: str | None = None
) -> None:
    """Save a staged spreadsheet and run its ingest workflow."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_sheet_"))
    book_path = work / upload.name
    book_path.write_bytes(upload.getvalue())

    name = lake_name.strip() or Path(upload.name).stem
    out_dir = _project_output_dir(name)
    args = SimpleNamespace(
        input=book_path,
        output_dir=out_dir,
        converter=converter,
    )

    log = io.StringIO()
    try:
        with st.spinner(
            t("ingesting", name=upload.name)
        ), contextlib.redirect_stdout(log):
            dokey_cli.run_sheet_ingest(args)
    except SystemExit as exc:
        _report_failure("ingest_failed", exc, log)
        return
    except Exception as exc:
        _report_failure("ingest_error", exc, log, trace=traceback.format_exc())
        return

    finish_ingest(out_dir, log)
