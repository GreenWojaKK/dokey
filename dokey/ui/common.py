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


def finish_ingest(out_dir: Path, log) -> None:
    """End a successful ingest: close the import view, land on the library.

    An import that succeeded is over, and what the user asked for now exists,
    so the app leaves the form and goes to the library it just made -- which
    is both the result and the proof that it worked. The run's own output
    travels across the rerun in session state: printed here it would be
    discarded a line later, and it carries every decision the ingest made.
    """
    st.session_state["_ingest_done"] = {
        "lake": str(out_dir),
        "log": log.getvalue().strip(),
    }
    st.session_state["_new_lake"] = str(out_dir)
    st.session_state["_import_open"] = False
    # The document has been added; leaving it staged would hand the next
    # import a form already filled with the document that is now on the shelf.
    st.session_state.pop("_ingest_local_file", None)
    st.rerun()


def ingest_notice() -> None:
    """Report the last ingest once, on the library it produced."""
    done = st.session_state.pop("_ingest_done", None)
    if not done:
        return
    st.success(t("ingested", path=done["lake"]))
    with st.expander(t("ingest_log"), expanded=False):
        st.code(done["log"] or t("no_output"))


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
