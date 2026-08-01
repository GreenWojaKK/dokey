from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

from .. import backends as backendslib


def run_backend(args: argparse.Namespace) -> None:
    if args.set_url and args.clear:
        raise SystemExit("Pass either --set or --clear, not both.")
    if args.set_url:
        path = backendslib.set_saved_endpoint(args.set_url)
        print(f"Saved OCR backend: {backendslib.chat_endpoint(args.set_url)}")
        print(f"  config: {path}")
    elif args.clear:
        backendslib.set_saved_endpoint(None)
        print("Cleared the saved OCR backend; the built-in default applies.")

    endpoint, source = backendslib.resolve_endpoint(None)
    backend = backendslib.probe(endpoint)
    status = "online" if backend is not None else "offline"
    print(f"OCR backend: {endpoint} ({source}, {status})")
    if backend is not None and backend.models:
        shown = ", ".join(backend.models[:6])
        if len(backend.models) > 6:
            shown += ", ..."
        print(f"  models: {shown}")

    if not args.no_discover:
        print("Scanning well-known local ports ...")
        found = backendslib.discover()
        if not found:
            print(
                "  no OpenAI-compatible server found; start one "
                "(LM Studio, llama.cpp llama-server, Ollama)"
            )
        for item in found:
            models = ", ".join(item.models[:4]) or "?"
            print(f"  {item.endpoint}  models: {models}")
        if found:
            print("Choose one with: dokey backend --set <url>")


def _streamlit_command() -> list[str]:
    """The command that starts Streamlit in a child process.

    From source that is ``python -m streamlit``. In a frozen build there is
    no Python beside the executable -- ``sys.executable`` is dokey itself --
    so the exe re-invokes itself with a sentinel argument that hands the
    child process to Streamlit before dokey's own parser ever runs.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-streamlit"]
    return [sys.executable, "-m", "streamlit"]


def run_streamlit(arguments: list[str]) -> None:
    """Become Streamlit: the frozen build's stand-in for ``-m streamlit``."""
    from streamlit.web import cli as streamlit_cli

    sys.argv = ["streamlit", *arguments]
    sys.exit(streamlit_cli.main())


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_ui(port: int, timeout: float = 45.0) -> bool:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/_stcore/health", timeout=2
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def run_app(args: argparse.Namespace) -> None:
    if importlib.util.find_spec("streamlit") is None:
        raise SystemExit(
            "Streamlit is not installed. Install the app extras first:\n"
            "  python -m pip install -e .[app]"
        )
    if importlib.util.find_spec("webview") is None:
        raise SystemExit(
            "pywebview is not installed. Install the optional app extra first:\n"
            "  python -m pip install -e .[app]\n"
            "or\n"
            "  python -m pip install pywebview\n"
            "(Or use the browser UI instead: dokey ui)"
        )
    port = args.port or _free_port()
    package_root = Path(__file__).resolve().parents[1]
    app_path = package_root / "ui_app.py"
    command = [
        *_streamlit_command(), "run", str(app_path),
        "--server.port", str(port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    if args.lake is not None:
        command += ["--", "--lake", str(args.lake)]
    server = subprocess.Popen(command, cwd=os.getcwd())
    try:
        if not _wait_for_ui(port):
            raise SystemExit(
                "The UI server did not come up; run `dokey ui` to see its output."
            )
        import webview

        webview.create_window(
            "Dokey",
            f"http://127.0.0.1:{port}",
            width=1280,
            height=860,
            # pywebview disables text selection by default, which makes the
            # desktop window a place where an error message can be read but not
            # copied -- exactly the text a user needs to hand to someone else.
            # The browser UI never had this problem; the app should not either.
            text_select=True,
        )
        assets_dir = package_root / "assets"
        # winforms needs a real .ico; gtk/cocoa load PNG directly.
        icon_path = assets_dir / ("logo.ico" if sys.platform == "win32" else "logo.png")
        webview.start(icon=str(icon_path) if icon_path.exists() else None)
    finally:
        server.terminate()


def run_ui(args: argparse.Namespace) -> None:
    if importlib.util.find_spec("streamlit") is None:
        raise SystemExit(
            "Streamlit is not installed. Install the optional UI dependency first:\n"
            "  python -m pip install -e .[ui]\n"
            "or\n"
            "  python -m pip install streamlit"
        )
    package_root = Path(__file__).resolve().parents[1]
    app_path = package_root / "ui_app.py"
    command = [*_streamlit_command(), "run", str(app_path)]
    if args.port is not None:
        command += ["--server.port", str(args.port)]
    script_args = []
    if args.lake is not None:
        script_args += ["--lake", str(args.lake)]
    if script_args:
        command += ["--", *script_args]
    returncode = subprocess.call(command, cwd=os.getcwd())
    if returncode != 0:
        raise SystemExit(returncode)
