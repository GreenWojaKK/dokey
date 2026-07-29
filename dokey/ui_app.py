"""Streamlit search UI. Run via `dokey ui` or:

    streamlit run dokey/ui_app.py -- --lake dokey_out/<lake>
"""
from __future__ import annotations

import argparse
import contextlib
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

# Streamlit executes this file without package context; make the repo root
# importable so an uninstalled checkout still works.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dokey import backends as backendslib
from dokey import cli as dokey_cli
from dokey import convert as convertlib
from dokey import hwp as hwplib
from dokey import mdunit
from dokey import blocks as blockslib
from dokey import search as searchlib
from dokey import tocsource
from dokey.i18n import (
    LANGUAGE_LABELS,
    SUPPORTED_LANGUAGES,
    preferred_language,
    translate,
)
from dokey.pickers import HAS_FOLDER_PICKER, choose_folder
from dokey.names import slugify

_MARK_CSS = (
    "<style>mark{background:rgba(255,200,0,.45);color:inherit;"
    "padding:0 .12em;border-radius:3px}</style>"
)

_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.png"


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
    out_dir = Path.cwd() / "dokey_out" / slugify(name)

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
    out_dir = Path.cwd() / "dokey_out" / slugify(name)

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


def backend_panel() -> None:
    """Bring-your-own OCR serving: dokey ships no models. Show the effective
    endpoint's health, persist a new one, or pick from discovered local servers
    (LM Studio, llama.cpp llama-server, Ollama)."""
    with st.expander(t("ocr_backend"), expanded=False):
        endpoint, source = backendslib.resolve_endpoint(None)
        backend = backendslib.probe(endpoint, timeout=0.8)
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
        columns = st.columns(2)
        if columns[0].button(t("save"), key="be_save", disabled=not new_url.strip()):
            backendslib.set_saved_endpoint(new_url)
            st.rerun()
        if columns[1].button(t("detect"), key="be_detect"):
            st.session_state["be_found"] = backendslib.discover()
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


def ingest_panel() -> None:
    clear_preview()  # each run re-earns the offer made below
    with st.expander(t("ingest_book"), expanded=False):
        upload = st.file_uploader(
            t("document_file"),
            type=["pdf", "hwp", "hwpx", "md", "markdown"],
            key="ing_pdf",
        )
        # HWP and Markdown are flow formats with no pages, so the PDF page-offset
        # / overlap / TOC controls do not apply; both take the heading-unitized
        # path. Markdown needs no converter at all -- it is ingested as-is.
        if upload is not None and hwplib.is_hwp(Path(upload.name)):
            _hwp_ingest_form(upload)
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
    lake_name = st.text_input(
        t("library_name_optional"), value="", key="hwp_name"
    )
    if st.button(
        t("run_ingest"),
        key="hwp_run",
        disabled=converter is None,
        type="primary",
    ):
        run_hwp_ingest_ui(upload, lake_name)


def run_hwp_ingest_ui(upload, lake_name: str) -> None:
    """Save the uploaded HWP, run the exact CLI ingest path, open the new lake."""
    work = Path(tempfile.mkdtemp(prefix="dokey_ui_hwp_"))
    hwp_path = work / upload.name
    hwp_path.write_bytes(upload.getvalue())

    name = lake_name.strip() or Path(upload.name).stem
    out_dir = Path.cwd() / "dokey_out" / slugify(name)
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


@st.cache_data(show_spinner=False, max_entries=8)
def _preview_markdown(payload: bytes, name: str, depth, profile: str, blocks: bytes | None):
    """What unitizing this render would produce, without writing anything."""
    text = payload.decode("utf-8", errors="replace")
    result = mdunit.unitize(text, fallback_title=Path(name).stem, max_level=depth, profile=profile)
    pages = None
    if blocks:
        work = Path(tempfile.mkdtemp(prefix="dokey_preview_"))
        stream = work / "blocks.json"
        stream.write_bytes(blocks)
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
def _preview_pdf(payload: bytes, name: str, depth, profile: str):
    """The table of contents dokey would split this PDF on.

    The same resolver the ingest uses, minus OCR: a preview must not spend
    minutes rendering the front matter through a model.
    """
    work = Path(tempfile.mkdtemp(prefix="dokey_preview_"))
    pdf_path = work / name
    pdf_path.write_bytes(payload)
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


def _offer_preview(
    kind: str, name: str, payload: bytes, depth, profile: str, blocks: bytes | None = None
) -> None:
    """Hand the main pane a document it could preview, and what to preview it as.

    The forms live in the sidebar, which is the narrowest column on the page.
    A table of a document's sections is the widest thing the app shows, and so
    the offer to look at one belongs beside the table rather than beside the
    settings. Streamlit runs the sidebar first, so the file and the settings
    chosen there are already in session state when the main pane draws the
    control that acts on them.
    """
    st.session_state["_preview"] = {
        "kind": kind,
        "name": name,
        "payload": payload,
        "depth": depth,
        "profile": profile,
        "blocks": blocks,
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
    try:
        with st.spinner(t("preview_reading")):
            if request["kind"] == "md":
                rows, source, depth, ladder = _preview_markdown(
                    request["payload"],
                    request["name"],
                    request["depth"],
                    request["profile"],
                    request["blocks"],
                )
            else:
                rows, source, depth, ladder = _preview_pdf(
                    request["payload"],
                    request["name"],
                    request["depth"],
                    request["profile"],
                )
    except Exception as exc:  # a preview must never take the page down with it
        st.warning(t("preview_failed", error=exc))
        return False
    if not rows:
        st.info(t("preview_empty"))
        return True
    st.caption(t("preview_source", source=source, count=len(rows), depth=depth))
    if ladder and ladder != "-":
        st.caption(t("preview_ladder", ladder=ladder))
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=560)
    st.caption(t("preview_not_extracted"))
    return True


def _md_ingest_form(upload) -> None:
    """Markdown/Markdown-render ingest: unitized by heading, no converter needed.

    This is the fast lane for text a user already has (e.g. a Docling render):
    dokey keeps layout reconstruction upstream and just unitizes + indexes.
    """
    st.caption(t("md_input_caption"))
    lake_name = st.text_input(
        t("library_name_optional"), value="", key="md_name"
    )
    depth = _section_depth_input("md_depth")
    with st.expander(t("advanced_overrides"), expanded=False):
        profile = _language_profile_input("md_profile")
        blocks_upload = st.file_uploader(
            t("source_blocks"), type=["json"], key="md_blocks",
            help=t("source_blocks_help"),
        )
        write_items = _write_items_input("md_items")
    if upload is not None:
        _offer_preview(
            "md",
            upload.name,
            upload.getvalue(),
            depth,
            profile,
            blocks_upload.getvalue() if blocks_upload is not None else None,
        )
    if st.button(t("run_ingest"), key="md_run", type="primary"):
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
    out_dir = Path.cwd() / "dokey_out" / slugify(name)
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
    """Show whether a layout converter is in reach, and say so either way.

    Nothing has to be configured for one to be used: dokey looks for it on
    PATH and in the interpreter running dokey, exactly as the CLI does. The
    caption exists so the answer is visible before a scanned book is added
    rather than after it indexes empty.
    """
    converter, source = convertlib.resolve_converter()
    if converter is None:
        st.caption(t("converter_offline"))
        return False
    known = {"config", "discovered", "flag"}
    st.caption(
        t(
            "converter_online",
            cmd=converter.display(),
            source=t(f"converter_source_{source}") if source in known else source,
        )
    )
    return True


def _auto_ingest_form(pdf_upload) -> None:
    """Zero-config ingest: upload and add. Overrides are tucked away and only
    needed to correct a wrong guess."""
    has_converter = converter_status()
    with st.expander(t("advanced_overrides"), expanded=False):
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
        depth = _section_depth_input("auto_depth")
        profile = _language_profile_input("auto_profile")
        write_items = _write_items_input("auto_items")
    recover = st.checkbox(t("recover_printed"), value=True, key="auto_recover")
    lake_name = st.text_input(
        t("library_name_optional"), value="", key="auto_name"
    )
    if pdf_upload is not None:
        _offer_preview(
            "pdf", pdf_upload.name, pdf_upload.getvalue(), depth, profile
        )
    if st.button(
        t("run_ingest"),
        key="auto_run",
        disabled=pdf_upload is None,
        type="primary",
    ):
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
        toc_upload = st.file_uploader(
            t("toc_file_label"), type=["csv", "txt"], key="ing_toc"
        )
        toc_format = st.selectbox(
            t("toc_format"),
            ["auto", "csv", "text"],
            format_func=lambda value: t(f"format_{value}"),
            key="ing_toc_fmt",
        )
    page_offset = st.number_input(
        t("page_offset"), value=0, step=1, key="ing_offset"
    )
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
    lake_name = st.text_input(
        t("library_name_optional"), value="", key="ing_name"
    )
    if st.button(
        t("run_ingest"),
        key="ing_run",
        disabled=pdf_upload is None,
        type="primary",
    ):
        run_ingest_ui(
            pdf_upload, toc_source, toc_upload, toc_format,
            int(page_offset), int(section_overlap), recover, lake_name,
        )


def pick_lake(cli_lake: Path | None) -> Path:
    candidates = [str(path) for path in searchlib.find_lakes(Path.cwd())]
    new_lake = st.session_state.pop("_new_lake", None)
    if new_lake and new_lake not in candidates:
        candidates.insert(0, new_lake)
    if cli_lake is not None and str(cli_lake) not in candidates:
        candidates.insert(0, str(cli_lake))
    opened = st.session_state.get("_opened_lake")
    if opened and opened not in candidates:
        candidates.insert(0, opened)

    # The list holds what is under the folder dokey was started in; anything
    # else is opened the way a folder is normally opened.
    if HAS_FOLDER_PICKER:
        if st.button(
            t("browse_library"), key="lake_browse", help=t("browse_library_help")
        ):
            chosen = choose_folder(t("browse_library_title"))
            if chosen:
                st.session_state["_opened_lake"] = chosen
                st.session_state["lake_select"] = chosen
                st.rerun()
            st.caption(t("browse_cancelled"))

    if not candidates:
        st.info(t("no_library"))
        st.stop()
    if new_lake and new_lake in candidates:
        st.session_state["lake_select"] = new_lake  # auto-select the fresh lake
    selected = st.selectbox(t("library"), candidates, key="lake_select")
    if HAS_FOLDER_PICKER:
        lake_str = selected
    else:
        # No Tk to open a dialog with: typing the path is the fallback, not the
        # first offer.
        custom = st.text_input(
            t("custom_library_path"),
            value="",
            key="lake_path",
            help=t("custom_library_path_help"),
        )
        lake_str = custom.strip() or selected
    lake = Path(lake_str)
    if not (lake / "silver" / "sections.jsonl").exists():
        st.error(t("not_library", path=lake))
        st.stop()
    return lake


def sidebar() -> tuple[Path, int]:
    if _LOGO_PATH.exists():
        logo_col, title_col = st.columns([1, 4])
        logo_col.image(str(_LOGO_PATH))
        title_col.title("Dokey")
    else:
        st.title("Dokey")
    language_selector()
    ingest_panel()
    backend_panel()
    lake = pick_lake(lake_from_argv())
    try:
        with st.spinner(t("building_search_index")):
            stats = searchlib.ensure_index(lake)
    except (FileNotFoundError, ValueError) as exc:
        st.error(t("index_error", error=exc))
        st.stop()
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
    st.dataframe(display, hide_index=True, height=560)


st.set_page_config(
    page_title="Dokey",
    page_icon=str(_LOGO_PATH) if _LOGO_PATH.exists() else "📚",
    layout="wide",
)
st.markdown(_MARK_CSS, unsafe_allow_html=True)

with st.sidebar:
    active_lake, max_results = sidebar()

st.subheader(active_lake.name)
query = st.text_input(
    t("search"),
    key="query",
    placeholder=t("search_placeholder"),
)

if query.strip():
    results = searchlib.search(active_lake, query, limit=max_results)
    if not results:
        st.info(t("no_matches"))
    for hit in results:
        result_card(active_lake, hit)
elif not preview_pane():
    # Nothing being looked at and nothing being searched: the library it is.
    browse_sections(active_lake)
