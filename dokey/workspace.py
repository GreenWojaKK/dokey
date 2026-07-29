"""Persistent project folders and their selected dokey libraries.

A library is one ingested document lake.  A project is the stable folder that
owns one or more libraries, normally under ``project/dokey_out``.  Keeping the
project roots in the existing dokey config lets the UI behave like a workspace:
pick a project folder once, then move between its libraries without reopening
paths on every launch.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import backends as backendslib


def normalize(path: str | Path) -> Path:
    """Return a stable absolute spelling without requiring the path to exist."""
    return Path(path).expanduser().resolve(strict=False)


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(normalize(path)))


def _unique_paths(values) -> list[Path]:
    found: list[Path] = []
    keys: set[str] = set()
    for value in values:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            continue
        path = normalize(value)
        key = _path_key(path)
        if key in keys:
            continue
        keys.add(key)
        found.append(path)
    return found


def saved_projects() -> list[Path]:
    values = backendslib.load_config().get("projects", [])
    if not isinstance(values, list):
        return []
    return _unique_paths(values)


def infer_project(lake: str | Path, cwd: str | Path | None = None) -> Path:
    """Infer a useful project root from a library supplied on the command line."""
    lake_path = normalize(lake)
    if cwd is not None:
        cwd_path = normalize(cwd)
        if lake_path == cwd_path or lake_path.is_relative_to(cwd_path):
            return cwd_path
    if lake_path.parent.name.casefold() == "dokey_out":
        return lake_path.parent.parent
    return lake_path.parent


def project_roots(
    cwd: str | Path,
    cli_lake: str | Path | None = None,
) -> list[Path]:
    """Projects visible to the UI: saved roots, a CLI lake's root, then cwd.

    The current working directory is always a usable implicit project.  This
    preserves the old launch behavior while registered projects provide the
    durable workspace model.
    """
    values: list[str | Path] = [*saved_projects()]
    if cli_lake is not None:
        values.append(infer_project(cli_lake, cwd))
    values.append(cwd)
    return [path for path in _unique_paths(values) if path.is_dir()]


def project_for_lake(
    lake: str | Path,
    projects: list[Path],
    *,
    cwd: str | Path | None = None,
) -> Path:
    """Return the deepest registered project containing ``lake``."""
    lake_path = normalize(lake)
    owners = [
        normalize(project)
        for project in projects
        if lake_path == normalize(project)
        or lake_path.is_relative_to(normalize(project))
    ]
    if owners:
        return max(owners, key=lambda path: len(path.parts))
    return infer_project(lake_path, cwd)


def register_project(root: str | Path) -> Path:
    path = normalize(root)
    if not path.is_dir():
        raise NotADirectoryError(path)
    config = backendslib.load_config()
    current = config.get("projects", [])
    if not isinstance(current, list):
        current = []
    projects = _unique_paths([*current, path])
    config["projects"] = [str(item) for item in projects]
    config["active_project"] = str(path)
    backendslib.save_config(config)
    return path


def forget_project(root: str | Path) -> None:
    key = _path_key(root)
    config = backendslib.load_config()
    current = config.get("projects", [])
    if not isinstance(current, list):
        current = []
    projects = [
        path
        for path in _unique_paths(current)
        if _path_key(path) != key
    ]
    config["projects"] = [str(path) for path in projects]
    active = config.get("active_project")
    if isinstance(active, str) and active.strip() and _path_key(active) == key:
        config.pop("active_project", None)
    selections = config.get("selected_lakes", {})
    if isinstance(selections, dict):
        config["selected_lakes"] = {
            project: lake
            for project, lake in selections.items()
            if not isinstance(project, str) or _path_key(project) != key
        }
    backendslib.save_config(config)


def remember_active_project(root: str | Path) -> None:
    config = backendslib.load_config()
    config["active_project"] = str(normalize(root))
    backendslib.save_config(config)


def remembered_active_project(projects: list[Path]) -> Path | None:
    value = backendslib.load_config().get("active_project")
    if not isinstance(value, str) or not value.strip():
        return None
    key = _path_key(value)
    return next((project for project in projects if _path_key(project) == key), None)


def remember_lake(project: str | Path, lake: str | Path) -> None:
    project_path = normalize(project)
    lake_path = normalize(lake)
    if lake_path != project_path and not lake_path.is_relative_to(project_path):
        raise ValueError(f"Library {lake_path} is outside project {project_path}")
    config = backendslib.load_config()
    selections = config.get("selected_lakes", {})
    if not isinstance(selections, dict):
        selections = {}
    selections[str(project_path)] = str(lake_path)
    config["selected_lakes"] = selections
    config["active_project"] = str(project_path)
    backendslib.save_config(config)


def remembered_lake(project: str | Path) -> Path | None:
    project_path = normalize(project)
    selections = backendslib.load_config().get("selected_lakes", {})
    if not isinstance(selections, dict):
        return None
    for saved_project, lake in selections.items():
        if _path_key(saved_project) == _path_key(project_path):
            return normalize(lake)
    return None
