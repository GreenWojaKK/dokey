"""Streamlit entry point for the local search and import UI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitAPIException

# Streamlit executes this file without package context. Keep the repository
# importable when the checkout has not been installed.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dokey import search as searchlib
from dokey.ui.common import _active_project_root, ingest_notice, t
from dokey.ui.ingest import import_open, import_view
from dokey.ui.library import _MARK_CSS, browse_sections, result_card
from dokey.ui.navigation import PROJECT_CSS, sidebar as _render_sidebar
from dokey.ui.preview import clear_preview, preview_pane as _render_preview_pane


_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.png"
_SIDEBAR_WIDTH = 240


def lake_from_argv() -> Path | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--lake", type=Path, default=None)
    try:
        known, _ = parser.parse_known_args(sys.argv[1:])
    except SystemExit:
        return None
    return known.lake


def sidebar() -> tuple[Path | None, int]:
    """Render the sidebar using the optional command-line library."""
    return _render_sidebar(lake_from_argv())


def preview_pane() -> bool:
    """Render the staged-input preview in the main pane."""
    return _render_preview_pane(translation_key="preview_toc")


def configure_page() -> None:
    """Set the wide layout and a compact initial sidebar width."""
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
st.markdown(_MARK_CSS + PROJECT_CSS, unsafe_allow_html=True)

with st.sidebar:
    active_lake, max_results = sidebar()

importing = import_open() or active_lake is None
clear_preview()

if not importing:
    st.subheader(active_lake.name)
    try:
        active_relative = active_lake.relative_to(_active_project_root()).as_posix()
    except ValueError:
        active_relative = str(active_lake)
    st.caption(
        t(
            "project_breadcrumb",
            project=_active_project_root().name,
            path=active_relative,
        )
    )
    # An ingest that just finished lands here: this library is its result, and
    # the notice under the name is what the form could not say before rerunning.
    ingest_notice()
elif active_lake is None:
    active_project = _active_project_root()
    st.subheader(active_project.name)
    st.caption(str(active_project))
    st.info(t("project_empty_main"))

query = (
    ""
    if importing
    else st.text_input(t("search"), key="query", placeholder=t("search_placeholder"))
)

if importing:
    import_view()
    preview_pane()
elif query.strip():
    results = searchlib.search(active_lake, query, limit=max_results)
    if not results:
        st.info(t("no_matches"))
    for hit in results:
        result_card(active_lake, hit)
else:
    browse_sections(active_lake)
