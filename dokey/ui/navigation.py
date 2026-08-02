"""Project navigation and sidebar controls."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import streamlit as st

from dokey import pickers as pickerslib
from dokey import search as searchlib
from dokey import workspace as workspacelib
from dokey.ui.common import t
from dokey.ui.ingest import import_control, import_open
from dokey.ui.preview import clear_preview
from dokey.ui.settings import backend_panel, language_selector


_NAV_PREFIXES = ("nav_project", "nav_lake")


def _nav_rows(
    suffix: str = "",
    prefixes: tuple[str, ...] = _NAV_PREFIXES,
) -> str:
    """Address every navigation row by its widget-key prefix."""
    return ", ".join(
        f'[data-testid="stSidebar"] [class*="st-key-{prefix}_"] button{suffix}'
        for prefix in prefixes
    )


def _nav_selected(suffix: str = "") -> str:
    """Address the selected navigation row."""
    return ", ".join(
        f'[data-testid="stSidebar"] [class*="st-key-{prefix}_"] '
        f'[data-testid="stBaseButton-primary"]{suffix}'
        for prefix in _NAV_PREFIXES
    )


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

# Public alias for the composition facade.
PROJECT_CSS = _PROJECT_CSS


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(workspacelib.normalize(path)))


def _widget_key(prefix: str, path: str | Path) -> str:
    digest = hashlib.sha1(_path_key(path).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _matching_path(
    value: str | Path | None,
    choices: list[Path],
) -> Path | None:
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
    # Choosing a library is asking to look at one, so it ends an import in
    # progress rather than leaving the form open over a library the user has
    # just navigated away from. (Choosing a *project* does not: the import
    # form names the project it would write to, so switching there retargets
    # the import instead of abandoning it.)
    st.session_state["_active_project"] = str(project)
    st.session_state["_active_lake"] = str(lake)
    st.session_state.pop("query", None)
    st.session_state["_import_open"] = False
    clear_preview()
    workspacelib.remember_lake(project, lake)


def _add_project_control() -> None:
    """Register a project root with the available folder-selection control."""
    if pickerslib.HAS_FOLDER_PICKER:
        if st.button(
            t("add_project"),
            key="project_add",
            help=t("add_project_help"),
            icon=":material/create_new_folder:",
            use_container_width=True,
        ):
            chosen = pickerslib.choose_folder(t("add_project_title"))
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
    """Render the project explorer and return its active library."""
    cwd = workspacelib.normalize(Path.cwd())
    projects = workspacelib.project_roots(cwd, cli_lake)
    new_lake_value = st.session_state.pop("_new_lake", None)
    new_lake = (
        workspacelib.normalize(new_lake_value)
        if new_lake_value is not None
        else None
    )
    if new_lake is not None:
        owner = workspacelib.project_for_lake(new_lake, projects, cwd=cwd)
        if owner.is_dir() and _matching_path(owner, projects) is None:
            projects.insert(0, owner)

    st.markdown(f"#### {t('projects')}")
    _add_project_control()

    active_project = _matching_path(
        st.session_state.get("_active_project"),
        projects,
    )
    if active_project is None and new_lake is not None:
        active_project = workspacelib.project_for_lake(
            new_lake,
            projects,
            cwd=cwd,
        )
        active_project = _matching_path(active_project, projects)
    if active_project is None and cli_lake is not None:
        owner = workspacelib.project_for_lake(cli_lake, projects, cwd=cwd)
        active_project = _matching_path(owner, projects)
    if active_project is None:
        active_project = workspacelib.remembered_active_project(projects)
    if active_project is None:
        active_project = projects[0] if projects else None

    for project in projects:
        selected = (
            active_project is not None
            and _path_key(project) == _path_key(active_project)
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
    if not lakes:
        st.info(t("project_empty"))
    else:
        # A library that was just built is an instruction, the same as
        # clicking its row, so it outranks the remembered selection: the app
        # lands on it in this run instead of showing the previous library
        # once more and moving only on the next interaction.
        active_lake = (
            _matching_path(new_lake, lakes) if new_lake is not None else None
        )
        if active_lake is None:
            active_lake = _matching_path(
                st.session_state.get("_active_lake"),
                lakes,
            )
        if active_lake is None and cli_lake is not None:
            active_lake = _matching_path(cli_lake, lakes)
        if active_lake is None:
            active_lake = _matching_path(
                workspacelib.remembered_lake(active_project),
                lakes,
            )
        if active_lake is None:
            active_lake = lakes[0]

        grouped: dict[Path, list[Path]] = {}
        for lake in lakes:
            relative = lake.relative_to(active_project)
            grouped.setdefault(relative.parent, []).append(lake)
        for folder, folder_lakes in grouped.items():
            folder_label = (
                t("project_root")
                if folder == Path(".")
                else folder.as_posix()
            )
            st.caption(folder_label)
            for lake in folder_lakes:
                selected = (
                    not import_open()
                    and _path_key(lake) == _path_key(active_lake)
                )
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


def sidebar(cli_lake: Path | None) -> tuple[Path | None, int]:
    """Render sidebar navigation, settings, and search-index controls."""
    lake = pick_lake(cli_lake)
    import_control(lake)
    backend_panel()
    with st.expander(
        t("appearance"),
        expanded=False,
        icon=":material/translate:",
    ):
        language_selector()
    if lake is None or import_open():
        return lake, 10
    try:
        with st.spinner(t("building_search_index")):
            stats = searchlib.ensure_index(lake)
    except (FileNotFoundError, ValueError) as exc:
        st.error(t("index_error", error=exc))
        st.stop()
    with st.expander(
        t("search_settings"),
        expanded=False,
        icon=":material/tune:",
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
