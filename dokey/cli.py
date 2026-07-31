from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

from . import backends as backendslib
from . import converters as converterslib
from . import hwp as hwplib
from . import mdunit
from . import search as searchlib
from . import sheets as sheetslib
# The public surface: what main dispatches to, plus the handful of names
# outside callers (the UI, tests) reach through ``dokey.cli``. The private
# helpers stay inside their command modules.
from .commands.common import resolve_lake
from .commands.documents import (
    run_convert,
    run_flow_ingest,
    run_hwp_backend,
    run_hwp_ingest,
    run_md_ingest,
)
from .commands.folios import run_folios
from .commands.lake import ingest_entries
from .commands.parser import build_parser
from .commands.pdf import ingest, open_reader, run_auto, run_probe
from .commands.runtime import run_app, run_backend, run_ui
from .commands.search import run_index, run_search
from .commands.sheets import run_sheet_ingest


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:]) if argv is None else list(argv)
    if not arguments:
        # A double-clicked dokey.exe lands here: launch the app instead of
        # printing a usage error into a console that closes immediately.
        launch_default()
        return
    parser = build_parser()
    args = parser.parse_args(arguments)

    if args.command == "auto":
        if hwplib.is_hwp(args.input):
            run_hwp_ingest(args)
        elif sheetslib.is_spreadsheet(args.input):
            run_sheet_ingest(args)
        elif converterslib.is_flow_document(args.input):
            run_flow_ingest(args)
        elif mdunit.is_markdown(args.input):
            run_md_ingest(args)
        else:
            run_auto(args)
    elif args.command == "ingest":
        if hwplib.is_hwp(args.input):
            run_hwp_ingest(args)
        elif sheetslib.is_spreadsheet(args.input):
            run_sheet_ingest(args)
        elif converterslib.is_flow_document(args.input):
            run_flow_ingest(args)
        elif mdunit.is_markdown(args.input):
            run_md_ingest(args)
        else:
            ingest(args)
    elif args.command == "convert":
        run_convert(args)
    elif args.command == "hwp":
        run_hwp_backend(args)
    elif args.command == "index":
        run_index(args)
    elif args.command == "search":
        run_search(args)
    elif args.command == "ui":
        run_ui(args)
    elif args.command == "folios":
        run_folios(args)
    elif args.command == "probe":
        run_probe(args)
    elif args.command == "backend":
        run_backend(args)
    elif args.command == "app":
        run_app(args)
    else:  # pragma: no cover
        parser.error(f"Unsupported command: {args.command}")


def _ensure_workspace_cwd() -> Path:
    """Give a bare launch a stable working directory.

    When started from a real project directory (lakes present under cwd), keep
    it. Otherwise — the double-click case, where Windows hands us the Scripts
    folder — switch to the user workspace so lake discovery and new ingests
    land in one predictable, writable place."""
    if searchlib.find_lakes(Path.cwd()):
        return Path.cwd()
    workspace = backendslib.workspace_dir()
    workspace.mkdir(parents=True, exist_ok=True)
    os.chdir(workspace)
    return workspace


def launch_default() -> None:
    """The no-argument surface: open the friendliest available UI."""
    if importlib.util.find_spec("streamlit") is None:
        build_parser().print_help()
        raise SystemExit(
            "\nTo launch the UI by double-click, install the app extras first:\n"
            "  python -m pip install -e .[app]"
        )
    _ensure_workspace_cwd()
    namespace = argparse.Namespace(lake=None, port=None)
    if importlib.util.find_spec("webview") is not None:
        run_app(namespace)
    else:
        run_ui(namespace)


def _alert(message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Dokey", 0x10)
    else:
        print(message, file=sys.stderr)


def main_app() -> None:
    """Entry point of the windowed ``dokey-app`` executable (gui-scripts).

    A GUI process has no console: nothing printed is ever seen, so failures
    must surface as a message box instead of vanishing stderr."""
    try:
        launch_default()
    except SystemExit as exc:
        if exc.code not in (0, None):
            _alert(str(exc))
    except Exception as exc:  # pragma: no cover - last-resort surface
        _alert(f"Dokey failed to start:\n{exc}")


if __name__ == "__main__":
    main()
