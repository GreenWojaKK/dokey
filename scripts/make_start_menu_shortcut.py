"""Create a Start Menu shortcut for dokey-app.exe with a custom icon.

pip's generated ``dokey-app.exe`` launcher embeds a zip archive appended
after the PE image (the actual entry-point script lives there), so rewriting
its PE resources in place (e.g. via BeginUpdateResource/EndUpdateResource)
zeroes that trailer and breaks the launcher. A .lnk shortcut sidesteps this
entirely: Windows lets a shortcut's icon differ from its target's own icon,
so Explorer/Start Menu/a pinned taskbar icon can show the dokey logo while
dokey-app.exe itself stays untouched.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import win32com.client

REPO_ROOT = Path(__file__).resolve().parent.parent
ICON = REPO_ROOT / "dokey" / "assets" / "logo.ico"
SHORTCUT = (
    Path(os.environ["APPDATA"])
    / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Dokey.lnk"
)


def find_launcher() -> Path | None:
    """Locate ``dokey-app.exe``: on PATH, else beside this interpreter.

    A pip install puts the launcher in the environment's scripts directory,
    which is on PATH only when that environment is active -- so fall back to
    the directory the running interpreter came from.
    """
    on_path = shutil.which("dokey-app")
    if on_path:
        return Path(on_path)
    candidate = Path(sys.executable).parent / "Scripts" / "dokey-app.exe"
    if candidate.exists():
        return candidate
    candidate = Path(sys.executable).parent / "dokey-app.exe"
    return candidate if candidate.exists() else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Path to dokey-app.exe. Default: found on PATH or beside this Python.",
    )
    args = parser.parse_args()

    TARGET = args.target or find_launcher()
    if TARGET is None:
        raise SystemExit(
            "dokey-app.exe not found. Install dokey (pip install -e .) or pass "
            "--target with the launcher's path."
        )
    if not TARGET.exists():
        raise SystemExit(f"launcher not found: {TARGET}")
    if not ICON.exists():
        raise SystemExit(f"icon not found: {ICON}")

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(SHORTCUT))
    shortcut.TargetPath = str(TARGET)
    shortcut.WorkingDirectory = str(TARGET.parent)
    shortcut.IconLocation = f"{ICON},0"
    shortcut.Description = "Dokey"
    shortcut.save()
    print(f"wrote {SHORTCUT}")


if __name__ == "__main__":
    main()
