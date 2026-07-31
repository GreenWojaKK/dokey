"""Preview rendering for staged inputs."""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from dokey import blocks as blockslib
from dokey import cli as dokey_cli
from dokey import mdunit
from dokey import tocsource
from dokey.ui.common import t


@st.cache_data(show_spinner=False, max_entries=8)
def _preview_markdown(
    key: str, name: str, depth, profile: str, blocks_key: str | None, _read, _read_blocks
):
    """Return the sections a staged Markdown render would produce."""
    text = _read().decode("utf-8", errors="replace")
    result = mdunit.unitize(
        text,
        fallback_title=Path(name).stem,
        max_level=depth,
        profile=profile,
    )
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
    """Return the table of contents used for a staged PDF preview."""
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
        ocr_client=None,
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
    """Identify a staged input version without reading its contents."""
    path = getattr(staged, "path", None)
    if path is not None:
        try:
            stat = Path(path).stat()
        except OSError:
            return f"{path}:missing"
        return f"{path}:{stat.st_size}:{stat.st_mtime_ns}"
    uploaded = getattr(staged, "file_id", None)
    if uploaded:
        return f"upload:{uploaded}"
    return f"upload:{staged.name}:{getattr(staged, 'size', '?')}"


def _offer_preview(kind: str, staged, depth, profile: str, blocks=None) -> None:
    """Store a lazy preview request for the main pane."""
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


def preview_pane(translation_key: str = "preview_toc") -> bool:
    """Render the current preview request when its control is enabled."""
    request = st.session_state.get("_preview")
    if not request:
        return False
    control, named = st.columns([1, 2])
    show = control.toggle(
        t(translation_key),
        key="show_preview",
        help=t("preview_toc_help"),
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
    except Exception as exc:
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
            depth=depth if depth is not None else t("section_depth_auto"),
        )
    )
    if ladder and ladder != "-":
        st.caption(t("preview_ladder", ladder=ladder))
    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        height=560,
    )
    st.caption(t("preview_not_extracted"))
    return True
