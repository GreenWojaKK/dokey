"""Shared helpers and controls for the Streamlit UI."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from dokey import backends as backendslib
from dokey import workspace as workspacelib
from dokey.i18n import preferred_language, translate
from dokey.names import slugify


def ui_language() -> str:
    language = st.session_state.get("ui_language")
    if language is None:
        language = preferred_language(backendslib.load_config())
    return language


def t(key: str, **values: object) -> str:
    return translate(ui_language(), key, **values)


def _report_failure(
    message_key: str,
    exc: BaseException,
    log,
    *,
    trace: str = "",
) -> None:
    """Display a copyable failure message and any captured diagnostics."""
    st.error(t(message_key, error=exc))
    parts = [f"{type(exc).__name__}: {exc}"]
    if trace:
        parts.append(trace.rstrip())
    output = log.getvalue().strip()
    if output:
        parts.append(output)
    st.code("\n\n".join(parts) or t("no_output"))


def _active_project_root() -> Path:
    """Return the project that receives new imports."""
    value = st.session_state.get("_active_project")
    return workspacelib.normalize(value) if value else workspacelib.normalize(Path.cwd())


def _project_output_dir(name: str) -> Path:
    return _active_project_root() / "dokey_out" / slugify(name)


SECTION_DEPTHS = ("auto", "clause", "subclause")


def _section_depth_input(key: str) -> str:
    """Render the section-granularity selector."""
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
        t("write_items"),
        value=True,
        key=key,
        help=t("write_items_help"),
    )


def _run_button(key: str, *, disabled: bool = False) -> bool:
    """Render the primary import action at a compact width."""
    return st.columns([1, 4])[0].button(
        t("run_ingest"),
        key=key,
        disabled=disabled,
        type="primary",
        icon=":material/library_add:",
        use_container_width=True,
    )
