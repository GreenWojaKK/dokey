"""Settings controls for the Streamlit UI."""

from __future__ import annotations

import streamlit as st

from dokey import backends as backendslib
from dokey.i18n import (
    LANGUAGE_LABELS,
    SUPPORTED_LANGUAGES,
    preferred_language,
)
from dokey.ui.common import t, ui_language


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


@st.cache_data(show_spinner=False, ttl=30, max_entries=4)
def _backend_health(endpoint: str):
    """Return a short-lived reachability result for the configured backend."""
    return backendslib.probe(endpoint, timeout=0.8)


def backend_panel() -> None:
    """Render backend health, configuration, and discovery controls."""
    with st.expander(
        t("ocr_backend"),
        expanded=False,
        icon=":material/cable:",
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
        if columns[0].button(
            t("save"),
            key="be_save",
            disabled=not new_url.strip(),
        ):
            backendslib.set_saved_endpoint(new_url)
            st.rerun()
        if columns[1].button(t("detect"), key="be_detect"):
            st.session_state["be_found"] = backendslib.discover()
        if columns[2].button(
            t("recheck"),
            key="be_recheck",
            help=t("recheck_help"),
        ):
            _backend_health.clear()
            st.rerun()
        found = st.session_state.get("be_found", [])
        if st.session_state.get("be_detect") and not found:
            st.caption(t("no_backend_found"))
        for i, item in enumerate(found):
            row = st.columns([4, 1])
            row[0].caption(
                f"{item.endpoint} · {', '.join(item.models[:2]) or '?'}"
            )
            if row[1].button(t("use"), key=f"be_use_{i}"):
                backendslib.set_saved_endpoint(item.endpoint)
                st.session_state.pop("be_found", None)
                st.rerun()
        st.caption(t("backend_caption"))
