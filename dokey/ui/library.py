"""Search results and library browsing for the Streamlit UI."""

from __future__ import annotations

import html
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from dokey import search as searchlib
from dokey.ui.common import t


_MARK_CSS = (
    "<style>mark{background:rgba(255,200,0,.45);color:inherit;"
    "padding:0 .12em;border-radius:3px}</style>"
)


def render_snippet(snippet: str) -> str:
    safe = html.escape(snippet)
    return safe.replace(searchlib.MARK_START, "<mark>").replace(
        searchlib.MARK_END,
        "</mark>",
    )


def result_card(lake: Path, hit: searchlib.SectionHit) -> None:
    with st.container(border=True):
        badge = f" · :blue[{t('title_match')}]" if hit.matched_title else ""
        crumb = f"{hit.parent} › {hit.title}" if hit.parent != hit.title else hit.title
        st.markdown(f"**{crumb}**{badge}")
        if hit.printed_start_page is not None:
            meta = t(
                "printed_pages",
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
            "pdf_pages",
            start=hit.pdf_start_page,
            end=hit.pdf_end_page,
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
            t("open"),
            key=f"open_{hit.section_id}",
        ):
            os.startfile(artifact)
        columns[2].caption(str(artifact))


def browse_sections(lake: Path) -> None:
    frame = pd.read_json(lake / "sections.jsonl", lines=True)
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
            "printed_start_page": t("column_printed_start"),
            "printed_end_page": t("column_printed_end"),
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
        t("column_printed_start"): st.column_config.NumberColumn(width="small"),
        t("column_printed_end"): st.column_config.NumberColumn(width="small"),
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
