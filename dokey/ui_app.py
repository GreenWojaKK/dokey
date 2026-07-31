"""Streamlit search UI. Run via `dokey ui` or:

    streamlit run dokey/ui_app.py -- --lake dokey_out/<lake>
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import html
import io
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

# Streamlit executes this file without package context; make the repo root
# importable so an uninstalled checkout still works.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dokey import backends as backendslib
from dokey import cli as dokey_cli
from dokey import convert as convertlib
from dokey import converters as converterslib
from dokey import hwp as hwplib
from dokey import mdunit
from dokey import blocks as blockslib
from dokey import search as searchlib
from dokey import sheets as sheetslib
from dokey import tocsource
from dokey import workspace as workspacelib
from dokey.i18n import (
    LANGUAGE_LABELS,
    SUPPORTED_LANGUAGES,
    preferred_language,
    translate,
)
from dokey.pickers import (
    HAS_FILE_PICKER,
    HAS_FOLDER_PICKER,
    SelectedFile,
    choose_file,
    choose_folder,
)
from dokey.names import slugify

_MARK_CSS = (
    "<style>mark{background:rgba(255,200,0,.45);color:inherit;"
    "padding:0 .12em;border-radius:3px}</style>"
)
# The project tree is made of buttons because a row has to be clickable, but a
# tree of buttons reads as a stack of boxes. These prefixes mark the rows that
# are navigation, so the styling below can strip the box off them and leave an
# icon and a name -- while the real actions next to them (add a project, forget
# one) keep looking like the actions they are.
_NAV_PREFIXES = ("nav_project", "nav_lake")


def _nav_rows(suffix: str = "", prefixes: tuple[str, ...] = _NAV_PREFIXES) -> str:
    """Every navigation row, addressed by the key it was given."""
    return ", ".join(
        f'[data-testid="stSidebar"] [class*="st-key-{prefix}_"] button{suffix}'
        for prefix in prefixes
    )


def _nav_selected(suffix: str = "") -> str:
    """The active row. Streamlit's ``primary`` kind is the only hook there is.

    It marks which row is current -- nothing here wants the filled button that
    normally comes with it.
    """
    return ", ".join(
        f'[data-testid="stSidebar"] [class*="st-key-{prefix}_"] '
        f'[data-testid="stBaseButton-primary"]{suffix}'
        for prefix in _NAV_PREFIXES
    )


# Selection is weight and opacity, not colour: this Streamlit exposes no theme
# variable to borrow an accent from, and a hard-coded one would be wrong under
# any theme but the default.
_PROJECT_CSS = f"""
<style>
{_nav_rows()} {{
    background: transparent;
    border: none;
    box-shadow: none;
    color: inherit;
    opacity: .8;  /* enough to set the active row apart, not enough to read as disabled */
    align-items: center;
    gap: .35rem;
    min-width: 0;
    min-height: 0;
    padding: .12rem .3rem;
}}
/* A button centres what is inside it, and a full-width button centres it in
   the middle of the sidebar. Every box in a navigation row starts at the left
   instead, so the name sits against its folder icon and the names line up with
   each other. Told to each descendant because the label is nested a few boxes
   deep and any one of them can be the thing doing the centring. */
{_nav_rows()}, {_nav_rows(" *")} {{
    justify-content: flex-start;
    text-align: left;
}}
{_nav_rows(" p")} {{
    color: inherit;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}
{_nav_rows(":hover")} {{
    background: rgba(127, 127, 127, .14);
    opacity: 1;
}}
{_nav_rows("", (_NAV_PREFIXES[1],))} {{
    padding-left: 1.1rem;  /* a library sits under the project that owns it */
}}
{_nav_selected()}, {_nav_selected(" p")} {{
    color: inherit;
    opacity: 1;
    font-weight: 600;
}}
</style>
"""

# The logo is the browser tab's icon and nothing else. A wordmark at the top of
# the sidebar spends the most valuable rows on the page telling the reader which
# application they just opened; the tab already says that.
_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.png"
# Streamlit opens the sidebar at 300px and remembers whatever it is dragged to.
# Navigation needs less than that; the drag handle still decides the rest.
_SIDEBAR_WIDTH = 240


def ui_language() -> str:
    language = st.session_state.get("ui_language")
    if language is None:
        language = preferred_language(backendslib.load_config())
    return language


def t(key: str, **values: object) -> str:
    return translate(ui_language(), key, **values)


def _report_failure(message_key: str, exc: BaseException, log, *, trace: str = "") -> None:
    """Show a failure in a form that can be handed to someone else.

    The message goes in a banner for the eye and *also* into the code block,
    because the banner is the line a user actually needs to quote and the code
    block is the only part with a copy button. An unexpected exception brings
    its traceback along: it is the difference between "it failed" and knowing
    where.
    """
    st.error(t(message_key, error=exc))
    parts = [f"{type(exc).__name__}: {exc}"]
    if trace:
        parts.append(trace.rstrip())
    output = log.getvalue().strip()
    if output:
        parts.append(output)
    st.code("\n\n".join(parts) or t("no_output"))


def save_ui_language() -> None:
    config = backendslib.load_config()
    config["language"] = ui_language()
    backendslib.save_config(config)


def language_selector() -> None:
    if "ui_language" not in st.session_state:
        st.session_state["ui_language"] = preferred_language(backendslib.load_config())
    st.radio(
        "언어 / Language",
        SUPPORTED_LANGUAGES,
        format_func=lambda language: LANGUAGE_LABELS[language],
        key="ui_language",
        horizontal=True,
        on_change=save_ui_language,
    )


def lake_from_argv() -> Path | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--lake", type=Path, default=None)
    try:
        known, _ = parser.parse_known_args(sys.argv[1:])
    except SystemExit:
        return None
    return known.lake


def render_snippet(snippet: str) -> str:
    safe = html.escape(snippet)
    return safe.replace(searchlib.MARK_START, "<mark>").replace(
        searchlib.MARK_END, "</mark>"
    )


def _active_project_root() -> Path:
    """Project currently owning new imports, with cwd as the legacy fallback."""
    value = st.session_state.get("_active_project")
    return workspacelib.normalize(value) if value else workspacelib.normalize(Path.cwd())


def _project_output_dir(name: str) -> Path:
    return _active_project_root() / "dokey_out" / slugify(name)


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
    """Walking-skeleton pipeline driven from the browser: save the uploaded
    PDF, run ingest, recover printed pages, build the index, then open the new
    lake. Reuses the exact CLI code paths."""
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
        ocr_endpoint=None,  # resolved: saved backend, else the built-in default
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
        with st.spinner(t("ingesting", name=pdf_upload.name)), contextlib.redirect_stdout(log):
            dokey_cli.ingest(ingest_args)
        if recover_folios:
            folio_args = SimpleNamespace(
                lake=out_dir, pdf=None, source="toc",
                endpoint=None,  # resolved: saved backend, else the default
                all_pages=False, verify=8, dpi=200, rebuild=False,
            )
            try:
                with st.spinner(t("recovering_pages")), \
                        contextlib.redirect_stdout(log):
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
    except Exception as exc:  # surface any pipeline error in the browser
        _report_failure(
            "ingest_error", exc, log, trace=traceback.format_exc()
        )
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
) -> None:
    """The smart one-shot path, mirroring `dokey auto`: recognize the TOC
    source, estimate the page offset, smoke-test every section start, pick the
    section overlap from how the document breaks, ingest, and index — all with
    no manual page offset. ``None`` overrides mean "let auto decide".

    ``read_method`` is the same choice as the CLI's ``--convert``: 'auto' hands
    the document to the layout converter only when its pages are images,
    'always' does so regardless, 'never' stays on the text layer."""
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
        page_offset=page_offset,  # None -> estimate from the document
        toc_page=toc_pages,  # None -> auto-detect the contents page(s)
        section_overlap=section_overlap,  # None -> detect clean vs mid-page
        ocr_endpoint=None,  # resolved: saved backend, else the built-in default
        convert=read_method,  # auto: only when the pages are images
        blocks=None,  # the PDF is the source; there is no separate stream
    )
    log = io.StringIO()
    try:
        with st.spinner(t("ingesting", name=pdf_upload.name)), \
                contextlib.redirect_stdout(log):
            dokey_cli.run_auto(auto_args)  # builds the search index itself
        if recover_folios:
            folio_args = SimpleNamespace(
                lake=out_dir, pdf=None, source="toc",
                endpoint=None,  # resolved: saved backend, else the default
                all_pages=False, verify=8, dpi=200, rebuild=False,
            )
            try:
                with st.spinner(t("recovering_pages")), \
                        contextlib.redirect_stdout(log):
                    dokey_cli.run_folios(folio_args)
            except SystemExit as exc:
                # The auto run already built the index; just note the skip.
                st.warning(t("skipped_page_recovery", error=exc))
    except SystemExit as exc:
        _report_failure("ingest_failed", exc, log)
        return
    except Exception as exc:  # surface any pipeline error in the browser
        _report_failure(
            "ingest_error", exc, log, trace=traceback.format_exc()
        )
        return

    st.success(t("ingested", path=out_dir))
    with st.expander(t("ingest_log"), expanded=False):
        st.code(log.getvalue() or t("no_output"))
    st.session_state["_new_lake"] = str(out_dir)
    st.rerun()


def _parse_optional_int(text: str) -> tuple[int | None, bool]:
    """(value, ok). Empty -> (None, ok); a bare integer -> (int, ok); anything
    else -> (None, not ok) so the caller can flag it."""
    text = text.strip()
    if not text:
        return None, True
    try:
        return int(text), True
    except ValueError:
        return None, False


def _parse_int_list(text: str) -> list[int] | None:
    """Comma- or space-separated integers; None when none parse."""
    values = []
    for part in text.replace(",", " ").split():
        try:
            values.append(int(part))
        except ValueError:
            pass
    return values or None


@st.cache_data(show_spinner=False, ttl=30, max_entries=4)
def _backend_health(endpoint: str):
    """Ask the OCR server whether it is there -- at most once every 30 seconds.

    Every widget on this page reruns the whole script, and a server that is not
    running costs the full connect timeout before anything can be drawn: 0.8 s
    of dead air per keystroke, per button, per toggle. Reachability does not
    change that fast, and the panel's own button forces a fresh answer when it
    does.
    """
    return backendslib.probe(endpoint, timeout=0.8)


def backend_panel() -> None:
    """Bring-your-own OCR serving: dokey ships no models. Show the effective
    endpoint's health, persist a new one, or pick from discovered local servers
    (LM Studio, llama.cpp llama-server, Ollama)."""
    with st.expander(
        t("ocr_backend"), expanded=False, icon=":material/cable:"
    ):
        endpoint, source = backendslib.resolve_endpoint(None)
        backend = _backend_health(endpoint)
        if backend is not None:
            names = ", ".join(backend.models[:3]) or t("no_model_loaded")
            st.success(t("online", models=names))
        else:
            st.warning(t("offline_ocr"))
        st.caption(f"{endpoint} · {t(f'backend_source_{source}')}")
        new_url = st.text_input(
            t("endpoint"),
            value="",
            key="be_url",
            placeholder=t("endpoint_placeholder"),
        )
        columns = st.columns(3)
        if columns[0].button(t("save"), key="be_save", disabled=not new_url.strip()):
            backendslib.set_saved_endpoint(new_url)
            st.rerun()
        if columns[1].button(t("detect"), key="be_detect"):
            st.session_state["be_found"] = backendslib.discover()
        if columns[2].button(t("recheck"), key="be_recheck", help=t("recheck_help")):
            _backend_health.clear()
            st.rerun()
        found = st.session_state.get("be_found", [])
        if st.session_state.get("be_detect") and not found:
            st.caption(t("no_backend_found"))
        for i, item in enumerate(found):
            row = st.columns([4, 1])
            row[0].caption(f"{item.endpoint} · {', '.join(item.models[:2]) or '?'}")
            if row[1].button(t("use"), key=f"be_use_{i}"):
                backendslib.set_saved_endpoint(item.endpoint)
                st.session_state.pop("be_found", None)
                st.rerun()
        st.caption(t("backend_caption"))


def converter_panel() -> None:
    """Every document converter on this machine, where converting happens.

    Drawn in the import view, not the sidebar: the sidebar is navigation,
    and the tools that read a document belong beside the act of adding one.
    dokey ships no converter -- what it discovered on PATH and in the
    interpreter is listed, and the reader can make one the default. The
    default fills the *setting* rung of the resolution ladder (flag >
    setting > discovery); with none set, discovery order is evidence order
    -- the converter that keeps more goes first. Each entry says what it
    keeps and which formats it is offered for, so comparing tools does not
    require running them.
    """
    with st.expander(
        t("converter_backend"), expanded=False, icon=":material/sync_alt:"
    ):
        saved = convertlib.load_converter()
        entries: list[tuple[object, bool]] = []
        seen: set[str] = set()
        for converter, is_saved in (
            ([(saved, True)] if saved else [])
            + [(found, False) for found in converterslib.discover()]
        ):
            if converter.kind in seen:
                continue
            seen.add(converter.kind)
            entries.append((converter, is_saved))
        if not entries:
            st.caption(t("converter_offline"))
            st.caption(t("converter_panel_caption"))
            return
        for converter, is_saved in entries:
            info, action = st.columns([3, 1], vertical_alignment="center")
            badge = f" · **{t('converter_default_badge')}**" if is_saved else ""
            info.markdown(
                f"**{converter.kind}** — "
                f"{converterslib.yields_label(converter.kind)}{badge}"
            )
            info.caption(converter.display())
            info.caption(
                t(
                    "converter_accepts",
                    formats=" ".join(
                        sorted(converterslib.accepted_suffixes(converter.kind))
                    ),
                )
            )
            if is_saved:
                if action.button(
                    t("converter_clear_default"),
                    key="conv_clear",
                    help=t("converter_clear_default_help"),
                ):
                    convertlib.save_converter(None)
                    st.rerun()
            elif action.button(
                t("converter_make_default"), key=f"conv_use_{converter.kind}"
            ):
                convertlib.save_converter(converter)
                st.rerun()
        st.caption(t("converter_panel_caption"))


def import_open() -> bool:
    return bool(st.session_state.get("_import_open"))


def import_control(lake: Path | None) -> None:
    """Open and close the import view. The form itself is not in here.

    Adding a book is a dozen controls -- a file, a name, a split depth, a
    language, and the overrides that correct a wrong guess. Stacked in the
    sidebar they became a column of labels 300 px wide and a screen tall, read
    top to bottom with no way to see how they relate. The sidebar keeps the
    one control that is genuinely navigation: the way in.
    """
    if lake is None:
        return  # nothing to switch between: the view is already the whole page
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
    """The whole add-a-book flow, in the pane with room to lay it out.

    The converter panel stands here too, under the forms: converting is part
    of adding a document, so the place to see and set the tools is the place
    where they are about to be used -- not the sidebar, which is navigation.
    """
    st.subheader(t("ingest_book"))
    st.caption(t("adding_to_project", project=_active_project_root().name))
    upload = _document_picker()
    _ingest_form_for(upload)
    converter_panel()


def _ingest_form_for(upload) -> None:
    """The form for what was picked, by what the format is."""
    # HWP and Markdown are flow formats with no pages, so the PDF page-offset
    # / overlap / TOC controls do not apply; both take the heading-unitized
    # path. Markdown needs no converter at all -- it is ingested as-is. A
    # workbook is neither: its unit is the sheet, so none of the heading
    # questions apply to it either.
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
    "pdf", "hwp", "hwpx", "md", "markdown",
    "xlsx", "xlsm", "xlsb", "xls", "ods",
    "docx", "pptx", "html", "htm", "epub",
]


def _document_picker():
    """Choose locally from the active project, with upload as the web fallback."""
    if not HAS_FILE_PICKER:
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
        chosen = choose_file(
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
        return SelectedFile(selected_path)
    st.caption(t("document_file"))
    return None


def _hwp_ingest_form(upload) -> None:
    """HWP/HWPX ingest: convert via the BYO converter and unitize by heading.

    The converter is auto-discovered (or set once from a terminal with
    ``dokey hwp --set``); the UI only surfaces its status and runs the ingest.
    """
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
    """Save the uploaded HWP, run the exact CLI ingest path, open the new lake."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_hwp_"))
    hwp_path = work / upload.name
    hwp_path.write_bytes(upload.getvalue())

    name = lake_name.strip() or Path(upload.name).stem
    out_dir = _project_output_dir(name)
    args = SimpleNamespace(input=hwp_path, output_dir=out_dir)

    log = io.StringIO()
    try:
        with st.spinner(t("ingesting", name=upload.name)), \
                contextlib.redirect_stdout(log):
            dokey_cli.run_hwp_ingest(args)
    except SystemExit as exc:
        _report_failure("ingest_failed", exc, log)
        return
    except Exception as exc:  # surface any pipeline error in the browser
        _report_failure(
            "ingest_error", exc, log, trace=traceback.format_exc()
        )
        return

    st.success(t("ingested", path=out_dir))
    with st.expander(t("ingest_log"), expanded=False):
        st.code(log.getvalue() or t("no_output"))
    st.session_state["_new_lake"] = str(out_dir)
    st.rerun()


def _sheet_read_summary(staged) -> str | None:
    """What the native read would yield, measured on the file itself.

    The read is the standard library (or xlrd), so it is cheap enough to run
    before anyone presses the button -- and a form that shows "4 regions, 1
    table, 2 banners, 87 merges" is the difference between a path a reader
    trusts and one that merely claims to work. Cached per file version, and a
    failure costs a missing caption, never the form.
    """
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
    """The reading paths this workbook offers, each labelled with its cost.

    ``None`` is the native read. Converter routes are listed only where they
    can actually serve -- a kind must yield the block stream (sheet identity
    travels in it) and accept the format -- so what the box offers is exactly
    what would run, with the trade written on it.
    """
    options: list[tuple[str | None, str]] = []
    blocked = False
    if suffix in sheetslib.LEGACY_SUFFIXES:
        options.append((None, t("sheet_read_native_legacy")))
        if not sheetslib.can_read_legacy():
            blocked = True
    elif not sheetslib.needs_converter(Path(f"x{suffix}")):
        options.append((None, t("sheet_read_native")))
    saved = convertlib.load_converter()
    seen: set[str] = set()
    for converter in ([saved] if saved else []) + converterslib.discover():
        if converter.kind in seen:
            continue
        seen.add(converter.kind)
        if not converterslib.accepts(converter.kind, suffix):
            continue
        # Both converter routes are offered, each labelled with what it
        # loses: the block route keeps sheet-tagged tables; the markdown
        # route keeps sheets as headings and tables, and nothing else.
        if "blocks" in converterslib.adapter_yields(converter.kind):
            label = t("sheet_read_converter", kind=converter.kind)
        else:
            label = t("sheet_read_converter_md", kind=converter.kind)
        options.append((converter.kind, label))
    return options, blocked


def _sheet_ingest_form(upload) -> None:
    """Spreadsheet ingest: one sheet becomes one section, named by the workbook.

    The reading path is the form's first question, out in the open: the
    native read (cells, merges, charts, anchors -- the default) against the
    converter route (tables only), each labelled with what it keeps. The
    prose unitizer is never involved, so none of the depth or language
    questions are asked; what *is* shown is what the chosen path would read.
    """
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


def run_sheet_ingest_ui(upload, lake_name: str, converter: str | None = None) -> None:
    """Save the workbook, run the exact CLI sheet-ingest path, open the new lake."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_sheet_"))
    book_path = work / upload.name
    book_path.write_bytes(upload.getvalue())

    name = lake_name.strip() or Path(upload.name).stem
    out_dir = _project_output_dir(name)
    args = SimpleNamespace(
        input=book_path,
        output_dir=out_dir,
        converter=converter,  # the form's pick is this run's instruction
    )

    log = io.StringIO()
    try:
        with st.spinner(t("ingesting", name=upload.name)), \
                contextlib.redirect_stdout(log):
            dokey_cli.run_sheet_ingest(args)
    except SystemExit as exc:
        _report_failure("ingest_failed", exc, log)
        return
    except Exception as exc:  # surface any pipeline error in the browser
        _report_failure(
            "ingest_error", exc, log, trace=traceback.format_exc()
        )
        return

    st.success(t("ingested", path=out_dir))
    with st.expander(t("ingest_log"), expanded=False):
        st.code(log.getvalue() or t("no_output"))
    st.session_state["_new_lake"] = str(out_dir)
    st.rerun()


SECTION_DEPTHS = ("auto", "clause", "subclause")


def _section_depth_input(key: str) -> str:
    """Let the reader say how finely the document should be cut.

    The same question the CLI asks with --section-depth, worth asking here too:
    a corpus that has to be uniform wants clause or subclause, and a single
    document being read wants auto.
    """
    return st.selectbox(
        t("section_depth"),
        SECTION_DEPTHS,
        format_func=lambda value: t(f"section_depth_{value}"),
        key=key,
        help=t("section_depth_help"),
    )


LANGUAGE_PROFILES = ("auto", "none", "ko")


def _language_profile_input(key: str) -> str:
    return st.selectbox(
        t("language_profile"),
        LANGUAGE_PROFILES,
        format_func=lambda value: t(f"language_profile_{value}"),
        key=key,
        help=t("language_profile_help"),
    )


def _write_items_input(key: str) -> bool:
    return st.checkbox(
        t("write_items"), value=True, key=key, help=t("write_items_help")
    )


def _run_button(key: str, *, disabled: bool = False) -> bool:
    """The one button that writes something, kept off the full page width."""
    return st.columns([1, 4])[0].button(
        t("run_ingest"),
        key=key,
        disabled=disabled,
        type="primary",
        icon=":material/library_add:",
        use_container_width=True,
    )


@st.cache_data(show_spinner=False, max_entries=8)
def _preview_markdown(
    key: str, name: str, depth, profile: str, blocks_key: str | None, _read, _read_blocks
):
    """What unitizing this render would produce, without writing anything.

    Keyed by ``key`` -- a document's identity, not its contents -- so a rerun
    neither reads the file nor hashes it. Streamlit ignores arguments whose
    names start with an underscore, which is what makes the readers passable.
    """
    text = _read().decode("utf-8", errors="replace")
    result = mdunit.unitize(text, fallback_title=Path(name).stem, max_level=depth, profile=profile)
    pages = None
    if blocks_key and _read_blocks is not None:
        work = Path(tempfile.mkdtemp(prefix="dokey_preview_"))
        stream = work / "blocks.json"
        stream.write_bytes(_read_blocks())
        parsed = blockslib.read_blocks(stream)
        if parsed:
            pages = blockslib.locate_sections(result.sections, parsed)
    rows = []
    for index, section in enumerate(result.sections):
        span = pages[index] if pages else None
        rows.append(
            {
                t("preview_level"): section.level,
                t("preview_title"): section.title,
                t("preview_pages"): f"{span[0]}–{span[1]}" if span else "",
                t("preview_chars"): len(mdunit.section_page_text(section)),
            }
        )
    ladder = " > ".join(result.report.heading_ladder.get("order", ())) or "-"
    return rows, "the document's own headings", result.report.max_level, ladder


@st.cache_data(show_spinner=False, max_entries=8)
def _preview_pdf(key: str, name: str, depth, profile: str, _read):
    """The table of contents dokey would split this PDF on.

    The same resolver the ingest uses, minus OCR: a preview must not spend
    minutes rendering the front matter through a model. Keyed by identity for
    the same reason as the Markdown preview: a book is megabytes, and a rerun
    should not pay for them.
    """
    work = Path(tempfile.mkdtemp(prefix="dokey_preview_"))
    pdf_path = work / name
    pdf_path.write_bytes(_read())
    reader = dokey_cli.open_reader(pdf_path)
    level = depth if isinstance(depth, int) else (2 if depth == "subclause" else 1)
    found = tocsource.resolve(
        reader,
        pdf_path,
        max_level=level,
        profile=profile,
        ocr_client=None,  # a preview never waits on OCR
        allow_printed=importlib.util.find_spec("fitz") is not None,
    )
    rows = [
        {
            t("preview_level"): entry.level,
            t("preview_title"): entry.title,
            t("preview_pages"): entry.page,
            t("preview_chars"): "",
        }
        for entry in found.entries
    ]
    return rows, found.label, level, found.note or "-"


def _staged_key(staged) -> str:
    """Say which document this is, and which version of it, without reading it.

    A locally chosen file answers with a path; asking it for its bytes is a
    full read, and staging happens on every rerun. Size and modification time
    tell the previews apart for the price of a stat.
    """
    path = getattr(staged, "path", None)
    if path is not None:
        try:
            stat = Path(path).stat()
        except OSError:  # chosen and then moved: let the preview report it
            return f"{path}:missing"
        return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
    uploaded = getattr(staged, "file_id", None)
    if uploaded:
        return f"upload:{uploaded}"
    return f"upload:{staged.name}:{getattr(staged, 'size', '?')}"


def _offer_preview(kind: str, staged, depth, profile: str, blocks=None) -> None:
    """Hand the main pane a document it could preview, and what to preview it as.

    The forms live in the sidebar, which is the narrowest column on the page.
    A table of a document's sections is the widest thing the app shows, and so
    the offer to look at one belongs beside the table rather than beside the
    settings. Streamlit runs the sidebar first, so the file and the settings
    chosen there are already in session state when the main pane draws the
    control that acts on them.

    What is handed over is the file, not its contents: an offer nobody takes
    must not cost a read of the book.
    """
    st.session_state["_preview"] = {
        "kind": kind,
        "name": staged.name,
        "staged": staged,
        "key": _staged_key(staged),
        "depth": depth,
        "profile": profile,
        "blocks": blocks,
        "blocks_key": _staged_key(blocks) if blocks is not None else None,
    }


def clear_preview() -> None:
    st.session_state.pop("_preview", None)


def preview_pane() -> bool:
    """Offer the staged document's table of contents, and draw it when asked.

    True only when the preview took the pane; a declined offer costs one line
    and leaves the library listing below it.
    """
    request = st.session_state.get("_preview")
    if not request:
        return False
    control, named = st.columns([1, 2])
    show = control.toggle(
        t("preview_toc"), key="show_preview", help=t("preview_toc_help")
    )
    named.caption(request["name"])
    if not show:
        return False
    blocks = request["blocks"]
    try:
        with st.spinner(t("preview_reading")):
            if request["kind"] == "md":
                rows, source, depth, ladder = _preview_markdown(
                    request["key"],
                    request["name"],
                    request["depth"],
                    request["profile"],
                    request["blocks_key"],
                    _read=request["staged"].getvalue,
                    _read_blocks=blocks.getvalue if blocks is not None else None,
                )
            else:
                rows, source, depth, ladder = _preview_pdf(
                    request["key"],
                    request["name"],
                    request["depth"],
                    request["profile"],
                    _read=request["staged"].getvalue,
                )
    except Exception as exc:  # a preview must never take the page down with it
        st.warning(t("preview_failed", error=exc))
        return False
    if not rows:
        st.info(t("preview_empty"))
        return True
    st.caption(
        t(
            "preview_source",
            source=source,
            count=len(rows),
            # A document with no ladder to speak of reports no depth at all;
            # say what was asked for rather than printing "None".
            depth=depth if depth is not None else t("section_depth_auto"),
        )
    )
    if ladder and ladder != "-":
        st.caption(t("preview_ladder", ladder=ladder))
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=560)
    st.caption(t("preview_not_extracted"))
    return True


def _flow_ingest_form(upload) -> None:
    """Flow-document ingest: convert, then unitize by heading.

    A flow format states no pages, so by the evidence rule a markdown-only
    converter loses nothing structural -- the lightest tool on the machine is
    enough, and the form says which one will run.
    """
    st.caption(t("flow_input_caption"))
    # Every converter the machine offers, one entry per kind, the saved one
    # first -- so the default the box shows is the same converter choose()
    # would pick, and picking another is this run's instruction.
    saved = convertlib.load_converter()
    offered: list = []
    seen: set[str] = set()
    for converter in ([saved] if saved else []) + converterslib.discover():
        if converter.kind in seen:
            continue
        seen.add(converter.kind)
        offered.append(converter)
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
    """Save the document, run the exact CLI flow-ingest path, open the lake."""
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
        converter=converter,  # the form's pick is this run's instruction
    )

    log = io.StringIO()
    try:
        with st.spinner(t("ingesting", name=upload.name)), \
                contextlib.redirect_stdout(log):
            dokey_cli.run_flow_ingest(args)
    except SystemExit as exc:
        _report_failure("ingest_failed", exc, log)
        return
    except Exception as exc:  # surface any pipeline error in the browser
        _report_failure(
            "ingest_error", exc, log, trace=traceback.format_exc()
        )
        return

    st.success(t("ingested", path=out_dir))
    with st.expander(t("ingest_log"), expanded=False):
        st.code(log.getvalue() or t("no_output"))
    st.session_state["_new_lake"] = str(out_dir)
    st.rerun()


def _md_ingest_form(upload) -> None:
    """Markdown/Markdown-render ingest: unitized by heading, no converter needed.

    This is the fast lane for text a user already has (e.g. a Docling render):
    dokey keeps layout reconstruction upstream and just unitizes + indexes.
    """
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
                t("source_blocks"), type=["json"], key="md_blocks",
                help=t("source_blocks_help"),
            )
        with overrides[1]:
            write_items = _write_items_input("md_items")
    if upload is not None:
        _offer_preview("md", upload, depth, profile, blocks_upload)
    if _run_button("md_run"):
        run_md_ingest_ui(upload, lake_name, depth, profile, blocks_upload, write_items)


def run_md_ingest_ui(
    upload,
    lake_name: str,
    section_depth: str = "auto",
    profile: str = "auto",
    blocks_upload=None,
    write_items: bool = True,
) -> None:
    """Save the uploaded Markdown, run the exact CLI ingest path, open the lake."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_md_"))
    md_path = work / upload.name
    md_path.write_bytes(upload.getvalue())

    # Saved under the render's own name, which is also where the ingest looks
    # for it when nobody uploads one.
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
        with st.spinner(t("ingesting", name=upload.name)), \
                contextlib.redirect_stdout(log):
            dokey_cli.run_md_ingest(args)
    except SystemExit as exc:
        _report_failure("ingest_failed", exc, log)
        return
    except Exception as exc:  # surface any pipeline error in the browser
        _report_failure(
            "ingest_error", exc, log, trace=traceback.format_exc()
        )
        return

    st.success(t("ingested", path=out_dir))
    with st.expander(t("ingest_log"), expanded=False):
        st.code(log.getvalue() or t("no_output"))
    st.session_state["_new_lake"] = str(out_dir)
    st.rerun()


def converter_status() -> bool:
    """Show every converter in reach and what each one keeps.

    Nothing has to be configured for one to be used: dokey looks on PATH and
    in the interpreter running dokey, exactly as the CLI does. The caption
    exists so the answer is visible before a scanned book is added rather
    than after it indexes empty -- and so the tools stop being invisible
    machinery: which ones the machine offers, and how much survives each, is
    stated where the choice will matter.
    """
    saved = convertlib.load_converter()
    entries = []
    seen: set[str] = set()
    for converter in ([saved] if saved else []) + converterslib.discover():
        if converter.kind in seen:
            continue
        seen.add(converter.kind)
        entries.append(
            f"{converter.kind} — {converterslib.yields_label(converter.kind)}"
        )
    if not entries:
        st.caption(t("converter_offline"))
        return False
    st.caption(t("converters_discovered", list=" · ".join(entries)))
    return True


def _auto_ingest_form(pdf_upload) -> None:
    """Zero-config ingest: choose a book and add it.

    Three questions worth asking on the way in -- what to call it, how finely
    to cut it, what language it is written in -- stand side by side, and the
    overrides that only correct a wrong guess stay folded away behind them.
    """
    has_converter = converter_status()
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
            offset_text = st.text_input(
                t("page_offset_auto"), value="", key="auto_offset",
                help=t("page_offset_auto_help"),
            )
        with overrides[1]:
            overlap_choice = st.selectbox(
                t("section_overlap"),
                ["auto", "0", "1", "2"],
                format_func=lambda value: t("overlap_auto") if value == "auto" else value,
                key="auto_overlap",
                help=t("section_overlap_auto_help"),
            )
            toc_page_text = st.text_input(
                t("toc_page_pin"), value="", key="auto_tocpage",
                help=t("toc_page_pin_help"),
            )
    if pdf_upload is not None:
        _offer_preview("pdf", pdf_upload, depth, profile)
    if _run_button("auto_run", disabled=pdf_upload is None):
        page_offset, ok = _parse_optional_int(offset_text)
        if not ok:
            st.error(t("invalid_number"))
            return
        section_overlap = None if overlap_choice == "auto" else int(overlap_choice)
        toc_pages = _parse_int_list(toc_page_text)
        run_ingest_auto_ui(
            pdf_upload, page_offset, section_overlap, toc_pages, recover, lake_name,
            read_method if has_converter else "never",
            depth,
            profile,
            write_items,
        )


def _manual_ingest_form(pdf_upload) -> None:
    """Full manual control: pick the TOC source (including an external TOC
    file), the page offset, and the overlap by hand."""
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
    recover = st.checkbox(
        t("recover_printed"), value=True, key="ing_recover"
    )
    if _run_button("ing_run", disabled=pdf_upload is None):
        run_ingest_ui(
            pdf_upload, toc_source, toc_upload, toc_format,
            int(page_offset), int(section_overlap), recover, lake_name,
        )


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(workspacelib.normalize(path)))


def _widget_key(prefix: str, path: str | Path) -> str:
    digest = hashlib.sha1(_path_key(path).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _matching_path(value: str | Path | None, choices: list[Path]) -> Path | None:
    if value is None:
        return None
    key = _path_key(value)
    return next((path for path in choices if _path_key(path) == key), None)


def _activate_project(project: Path) -> None:
    st.session_state["_active_project"] = str(project)
    st.session_state.pop("_active_lake", None)
    st.session_state.pop("_ingest_local_file", None)
    st.session_state.pop("query", None)
    clear_preview()
    workspacelib.remember_active_project(project)


def _activate_lake(project: Path, lake: Path) -> None:
    st.session_state["_active_project"] = str(project)
    st.session_state["_active_lake"] = str(lake)
    st.session_state.pop("query", None)
    clear_preview()
    workspacelib.remember_lake(project, lake)


def _add_project_control() -> None:
    """Register one stable project root; typing is only the no-Tk fallback."""
    if HAS_FOLDER_PICKER:
        if st.button(
            t("add_project"),
            key="project_add",
            help=t("add_project_help"),
            icon=":material/create_new_folder:",
            use_container_width=True,
        ):
            chosen = choose_folder(t("add_project_title"))
            if chosen:
                try:
                    project = workspacelib.register_project(chosen)
                except NotADirectoryError:
                    st.error(t("not_project_folder", path=chosen))
                else:
                    _activate_project(project)
                    st.rerun()
            else:
                st.caption(t("browse_cancelled"))
        return

    project_path = st.text_input(
        t("project_folder_path"),
        value="",
        key="project_path",
        help=t("project_folder_path_help"),
    )
    if st.button(
        t("add_project"),
        key="project_path_add",
        disabled=not project_path.strip(),
        icon=":material/create_new_folder:",
        use_container_width=True,
    ):
        try:
            project = workspacelib.register_project(project_path)
        except NotADirectoryError:
            st.error(t("not_project_folder", path=project_path))
        else:
            _activate_project(project)
            st.rerun()


def pick_lake(cli_lake: Path | None) -> Path | None:
    """Render a persistent project explorer and return its active library."""
    cwd = workspacelib.normalize(Path.cwd())
    projects = workspacelib.project_roots(cwd, cli_lake)
    new_lake_value = st.session_state.pop("_new_lake", None)
    new_lake = (
        workspacelib.normalize(new_lake_value) if new_lake_value is not None else None
    )
    if new_lake is not None:
        owner = workspacelib.project_for_lake(new_lake, projects, cwd=cwd)
        if owner.is_dir() and _matching_path(owner, projects) is None:
            projects.insert(0, owner)

    st.markdown(f"#### {t('projects')}")
    _add_project_control()

    active_project = _matching_path(
        st.session_state.get("_active_project"), projects
    )
    if active_project is None and new_lake is not None:
        active_project = workspacelib.project_for_lake(new_lake, projects, cwd=cwd)
        active_project = _matching_path(active_project, projects)
    if active_project is None and cli_lake is not None:
        owner = workspacelib.project_for_lake(cli_lake, projects, cwd=cwd)
        active_project = _matching_path(owner, projects)
    if active_project is None:
        active_project = workspacelib.remembered_active_project(projects)
    if active_project is None:
        active_project = projects[0] if projects else None

    for project in projects:
        selected = active_project is not None and _path_key(project) == _path_key(
            active_project
        )
        if st.button(
            project.name or str(project),
            key=_widget_key(_NAV_PREFIXES[0], project),
            help=str(project),
            type="primary" if selected else "secondary",
            icon=":material/folder_open:" if selected else ":material/folder:",
            use_container_width=True,
        ):
            _activate_project(project)
            st.rerun()

    if active_project is None:
        st.info(t("no_project"))
        return None

    st.session_state["_active_project"] = str(active_project)
    lakes = searchlib.find_lakes(active_project)
    # No box around the tree: indentation and the active row's own weight say
    # what belongs to what, and a border here would only fence off navigation
    # from the rest of a column that is nothing but navigation.
    if not lakes:
        st.info(t("project_empty"))
    else:
        active_lake = _matching_path(st.session_state.get("_active_lake"), lakes)
        if active_lake is None and new_lake is not None:
            active_lake = _matching_path(new_lake, lakes)
        if active_lake is None and cli_lake is not None:
            active_lake = _matching_path(cli_lake, lakes)
        if active_lake is None:
            active_lake = _matching_path(
                workspacelib.remembered_lake(active_project), lakes
            )
        if active_lake is None:
            active_lake = lakes[0]

        grouped: dict[Path, list[Path]] = {}
        for lake in lakes:
            relative = lake.relative_to(active_project)
            grouped.setdefault(relative.parent, []).append(lake)
        for folder, folder_lakes in grouped.items():
            folder_label = (
                t("project_root") if folder == Path(".") else folder.as_posix()
            )
            st.caption(folder_label)
            for lake in folder_lakes:
                selected = _path_key(lake) == _path_key(active_lake)
                if st.button(
                    lake.name,
                    key=_widget_key(_NAV_PREFIXES[1], lake),
                    help=str(lake),
                    type="primary" if selected else "secondary",
                    icon=":material/database:",
                    use_container_width=True,
                ):
                    _activate_lake(active_project, lake)
                    st.rerun()
        st.session_state["_active_lake"] = str(active_lake)

    if (
        _matching_path(active_project, workspacelib.saved_projects()) is not None
        and _path_key(active_project) != _path_key(cwd)
        and st.button(
            t("forget_project"),
            key=_widget_key("forget_project", active_project),
            help=t("forget_project_help"),
            icon=":material/remove_circle_outline:",
            use_container_width=True,
        )
    ):
        workspacelib.forget_project(active_project)
        st.session_state.pop("_active_project", None)
        st.session_state.pop("_active_lake", None)
        st.rerun()

    if not lakes:
        return None
    if new_lake is not None and _matching_path(new_lake, lakes) is not None:
        _activate_lake(active_project, new_lake)
    return active_lake


def sidebar() -> tuple[Path | None, int]:
    # Straight into the projects: no logo, no wordmark. The reader knows what
    # they opened, and the top of a navigation column is worth more than a badge.
    lake = pick_lake(lake_from_argv())
    import_control(lake)
    backend_panel()
    with st.expander(
        t("appearance"), expanded=False, icon=":material/translate:"
    ):
        language_selector()
    if lake is None:
        return None, 10
    try:
        with st.spinner(t("building_search_index")):
            stats = searchlib.ensure_index(lake)
    except (FileNotFoundError, ValueError) as exc:
        st.error(t("index_error", error=exc))
        st.stop()
    with st.expander(
        t("search_settings"), expanded=False, icon=":material/tune:"
    ):
        if st.button(t("rebuild_index"), key="rebuild"):
            with st.spinner(t("rebuilding_search_index")):
                stats = searchlib.ensure_index(lake, rebuild=True)
        st.caption(t("index_stats", sections=stats.sections, pages=stats.pages))
        if stats.created:
            st.caption(t("index_built", created=stats.created))
        if not stats.has_page_text:
            st.warning(t("no_page_text"))
        limit = st.slider(t("max_results"), 5, 50, 10, key="limit")
    return lake, limit


def result_card(lake: Path, hit: searchlib.SectionHit) -> None:
    with st.container(border=True):
        badge = f" · :blue[{t('title_match')}]" if hit.matched_title else ""
        # A top-level section is its own parent; the breadcrumb would repeat it.
        crumb = f"{hit.parent} › {hit.title}" if hit.parent != hit.title else hit.title
        st.markdown(f"**{crumb}**{badge}")
        if hit.printed_start_page is not None:
            meta = t(
                "book_pages",
                start=hit.printed_start_page,
                end=hit.printed_end_page,
            )
        else:
            meta = t(
                "content_pages",
                start=hit.content_start_page,
                end=hit.content_end_page,
            )
        meta += " · " + t(
            "pdf_pages", start=hit.pdf_start_page, end=hit.pdf_end_page
        )
        if hit.pages:
            pages = ", ".join(str(page) for page in hit.pages[:8])
            if len(hit.pages) > 8:
                pages += ", …"
            meta += " · " + t("matched_pdf_pages", pages=pages)
        st.caption(meta)
        for snippet in hit.snippets:
            st.markdown(
                f"<div>… {render_snippet(snippet)} …</div>",
                unsafe_allow_html=True,
            )
        artifact = searchlib.resolve_artifact(lake, hit)
        if artifact is None:
            return
        columns = st.columns([1, 1, 4])
        columns[0].download_button(
            t("download_pdf"),
            data=artifact.read_bytes(),
            file_name=artifact.name,
            key=f"download_{hit.section_id}",
        )
        if sys.platform == "win32" and columns[1].button(
            t("open"), key=f"open_{hit.section_id}"
        ):
            os.startfile(artifact)
        columns[2].caption(str(artifact))


def browse_sections(lake: Path) -> None:
    frame = pd.read_json(lake / "silver" / "sections.jsonl", lines=True)
    wanted = ["index", "parent", "title"]
    if "printed_start_page" in frame.columns:
        wanted += ["printed_start_page", "printed_end_page"]
    else:
        wanted += ["content_start_page", "content_end_page"]
    wanted += ["pdf_start_page", "pdf_end_page", "page_count"]
    if "folio_source" in frame.columns:
        wanted += ["folio_source"]
    columns = [column for column in wanted if column in frame.columns]
    display = frame[columns].rename(
        columns={
            "index": t("column_index"),
            "parent": t("column_parent"),
            "title": t("column_title"),
            "printed_start_page": t("column_book_start"),
            "printed_end_page": t("column_book_end"),
            "content_start_page": t("column_content_start"),
            "content_end_page": t("column_content_end"),
            "pdf_start_page": t("column_pdf_start"),
            "pdf_end_page": t("column_pdf_end"),
            "page_count": t("column_page_count"),
            "folio_source": t("column_folio_source"),
        }
    )
    compact_pages = {
        t("column_index"): st.column_config.NumberColumn(width="small"),
        t("column_parent"): st.column_config.TextColumn(width="medium"),
        t("column_title"): st.column_config.TextColumn(width="large"),
        t("column_book_start"): st.column_config.NumberColumn(width="small"),
        t("column_book_end"): st.column_config.NumberColumn(width="small"),
        t("column_content_start"): st.column_config.NumberColumn(width="small"),
        t("column_content_end"): st.column_config.NumberColumn(width="small"),
        t("column_pdf_start"): st.column_config.NumberColumn(width="small"),
        t("column_pdf_end"): st.column_config.NumberColumn(width="small"),
        t("column_page_count"): st.column_config.NumberColumn(width="small"),
        t("column_folio_source"): st.column_config.TextColumn(width="small"),
    }
    st.dataframe(
        display,
        column_config={
            column: compact_pages[column]
            for column in display.columns
            if column in compact_pages
        },
        width="stretch",
        hide_index=True,
        height=560,
    )


def configure_page() -> None:
    """Open with a sidebar narrower than Streamlit's 300 px default.

    A pixel width is passed where ``initial_sidebar_state`` takes one; it sets
    only the starting width, so the drag handle and whatever the reader last
    dragged it to still win. Releases that take the state words alone raise on
    the number, and get the plain call instead -- the layout is worth having
    either way.
    """
    page = {
        "page_title": "Dokey",
        "page_icon": str(_LOGO_PATH) if _LOGO_PATH.exists() else "📚",
        "layout": "wide",
    }
    try:
        st.set_page_config(**page, initial_sidebar_state=_SIDEBAR_WIDTH)
    except StreamlitAPIException:
        st.set_page_config(**page)


configure_page()
st.markdown(_MARK_CSS + _PROJECT_CSS, unsafe_allow_html=True)

with st.sidebar:
    active_lake, max_results = sidebar()

# A project with nothing in it has one useful thing to offer, so it offers it.
importing = import_open() or active_lake is None
clear_preview()  # each run re-earns the offer the import view makes below

if active_lake is None:
    active_project = _active_project_root()
    st.subheader(active_project.name)
    st.caption(str(active_project))
    st.info(t("project_empty_main"))
else:
    st.subheader(active_lake.name)
    try:
        active_relative = active_lake.relative_to(_active_project_root()).as_posix()
    except ValueError:
        active_relative = str(active_lake)
    st.caption(
        t("project_breadcrumb", project=_active_project_root().name, path=active_relative)
    )

query = (
    ""
    if importing
    else st.text_input(t("search"), key="query", placeholder=t("search_placeholder"))
)

if importing:
    # The form, and directly under it the table that says what the form would
    # do. Question and answer in one column, both as wide as the page.
    import_view()
    preview_pane()
elif query.strip():
    results = searchlib.search(active_lake, query, limit=max_results)
    if not results:
        st.info(t("no_matches"))
    for hit in results:
        result_card(active_lake, hit)
else:
    # Nothing being added and nothing being searched: the library it is.
    browse_sections(active_lake)
