"""Native file-manager dialogs, for the places a path would otherwise be typed.

A library is a directory on the machine dokey is running on, and typing its
path is the least reliable way to name it: it is long, it is case- and
separator-sensitive, and the one authority on where it is -- the file manager
-- is one click away. So dokey opens the platform's own folder chooser.

The dialog runs in its own interpreter. Two reasons, both practical: a Tk event
loop started inside Streamlit's script-runner thread is a way to hang the
server, and the dialog has to outlive the rerun that the click triggers. The
answer comes back through a file rather than stdout, because a chosen path may
carry characters the console codepage cannot spell -- which on a Korean Windows
is most of them.

Tk is in the standard library but not in every build (a slim Linux Python often
omits it), so the caller is told when there is no picker and can fall back to
asking for the path.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HAS_FOLDER_PICKER = importlib.util.find_spec("tkinter") is not None
HAS_FILE_PICKER = HAS_FOLDER_PICKER

# How long to leave the dialog open before giving up on it. Ten minutes is not
# a guess about how long choosing takes; it is a backstop against a dialog that
# never returns, e.g. one opened on a desktop nobody is looking at.
DIALOG_TIMEOUT = 600

_SNIPPET = """
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
chosen = filedialog.askdirectory(title={title}, mustexist=True) or ""
root.destroy()
Path({answer}).write_text(chosen, encoding="utf-8")
"""

_FILE_SNIPPET = """
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
chosen = filedialog.askopenfilename(
    title={title},
    initialdir={initial_dir},
    filetypes=[
        (
            "Dokey documents",
            "*.pdf *.hwp *.hwpx *.md *.markdown "
            "*.xlsx *.xlsm *.xlsb *.xls *.ods "
            "*.docx *.pptx *.html *.htm *.epub",
        ),
        ("All files", "*.*"),
    ],
) or ""
root.destroy()
Path({answer}).write_text(chosen, encoding="utf-8")
"""


@dataclass(frozen=True)
class SelectedFile:
    """A local selection with the small UploadedFile interface ingest expects."""

    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    def getvalue(self) -> bytes:
        return self.path.read_bytes()


def folder_dialog_snippet(title: str, answer_path: Path) -> str:
    """The child program that shows the dialog and writes down the answer."""
    return _SNIPPET.format(title=json.dumps(title), answer=json.dumps(str(answer_path)))


def file_dialog_snippet(
    title: str, initial_dir: str | Path, answer_path: Path
) -> str:
    """The child program for a file chooser rooted at the active project."""
    return _FILE_SNIPPET.format(
        title=json.dumps(title),
        initial_dir=json.dumps(str(initial_dir)),
        answer=json.dumps(str(answer_path)),
    )


def choose_folder(title: str, *, runner=subprocess.run) -> str | None:
    """Open the folder chooser; return the chosen path, or None if there is none.

    None covers all three ways this ends without a folder: no Tk to draw the
    dialog with, a dialog the user cancelled, and a dialog that never came
    back. The caller treats them alike -- nothing was chosen, so nothing
    changes.
    """
    if not HAS_FOLDER_PICKER:
        return None
    with tempfile.TemporaryDirectory(prefix="dokey_pick_") as tmp:
        answer = Path(tmp) / "folder.txt"
        answer.write_text("", encoding="utf-8")
        try:
            runner(
                [sys.executable, "-c", folder_dialog_snippet(title, answer)],
                capture_output=True,
                text=True,
                timeout=DIALOG_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        chosen = answer.read_text(encoding="utf-8").strip()
    return chosen or None


def choose_file(
    title: str,
    *,
    initial_dir: str | Path,
    runner=subprocess.run,
) -> str | None:
    """Choose a supported document, starting in ``initial_dir``."""
    if not HAS_FILE_PICKER:
        return None
    with tempfile.TemporaryDirectory(prefix="dokey_pick_") as tmp:
        answer = Path(tmp) / "file.txt"
        answer.write_text("", encoding="utf-8")
        try:
            runner(
                [
                    sys.executable,
                    "-c",
                    file_dialog_snippet(title, initial_dir, answer),
                ],
                capture_output=True,
                text=True,
                timeout=DIALOG_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        chosen = answer.read_text(encoding="utf-8").strip()
    return chosen or None
