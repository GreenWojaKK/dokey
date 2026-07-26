"""Bring-your-own HWP conversion: discover, persist, and shell out to an
external HWP/HWPX -> Markdown converter, then unitize the Markdown by heading.

dokey ships no HWP parser, exactly as it ships no OCR model. The Korean word
processor formats -- ``.hwp`` (an MS-CFB container of compressed records) and
``.hwpx`` (a ZIP of OWPML XML) -- are read by a converter the user installs on
their own machine, the reference one being ``hwp2md`` (a Rust CLI). dokey only
invokes it at arm's length: a separate process, communicating through files and
CLI arguments. Because dokey contains none of the converter's code and does not
bundle its binary, dokey stays MIT-licensed regardless of the converter's own
license (``hwp2md`` is GPL-3.0). This mirrors ``backends.py`` for OCR serving.

Unlike a PDF, an HWP document is a flow of text with no intrinsic pages; the
converter's Markdown is unitized by heading in :mod:`dokey.mdunit`, the shared,
format-neutral seam (the same one that ingests a Docling render or a plain
Markdown file). This module is only the converter half: discover it, persist
it, and run it. The effective converter is resolved in a fixed order, like the
OCR endpoint:

  1. an explicit command (``dokey hwp --set ...``) saved in ``~/.dokey/config.json``
  2. auto-discovery: ``hwp2md`` on PATH, else ``hwp2md`` inside WSL
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import backends as backendslib
from .names import slugify

CONVERTER_NAME = "hwp2md"
HWP_SUFFIXES = (".hwp", ".hwpx")

# hwp2md writes an INFO log to stdout by default, which would contaminate the
# Markdown; silence it with the global --log-level and write to a file with -o.
_QUIET = ("--log-level", "error")


@dataclass(frozen=True)
class Converter:
    """A resolved HWP->Markdown command prefix.

    ``argv`` is everything up to and including the tool (e.g. ``("hwp2md",)``
    or ``("wsl.exe", "-e", "/home/u/.cargo/bin/hwp2md")``); the ``to-md``
    invocation is appended at call time. ``wsl`` marks that the tool runs inside
    WSL, so Windows path arguments must be translated to ``/mnt/<drive>/...``.
    """

    argv: tuple[str, ...]
    wsl: bool = False

    def display(self) -> str:
        return " ".join(self.argv) + ("  (via WSL)" if self.wsl else "")


# --- converter resolution (mirrors backends.resolve_endpoint) ----------------


def is_hwp(path: Path) -> bool:
    return path.suffix.lower() in HWP_SUFFIXES


def load_converter() -> Converter | None:
    raw = backendslib.load_config().get("hwp_converter")
    if isinstance(raw, dict) and isinstance(raw.get("argv"), list) and raw["argv"]:
        return Converter(tuple(str(a) for a in raw["argv"]), bool(raw.get("wsl", False)))
    return None


def save_converter(converter: Converter | None) -> Path:
    config = backendslib.load_config()
    if converter is None:
        config.pop("hwp_converter", None)
    else:
        config["hwp_converter"] = {"argv": list(converter.argv), "wsl": converter.wsl}
    return backendslib.save_config(config)


def converter_from_command(command: str, wsl: bool | None = None) -> Converter:
    """Parse a shell-style command string into a Converter.

    ``wsl`` is inferred from the leading token (``wsl``/``wsl.exe``) unless
    given explicitly.
    """
    import shlex

    argv = tuple(shlex.split(command, posix=(sys.platform != "win32")))
    if not argv:
        raise ValueError("Empty converter command.")
    if wsl is None:
        head = Path(argv[0]).name.lower()
        wsl = head in {"wsl", "wsl.exe"}
    return Converter(argv, wsl)


def discover_converter(name: str = CONVERTER_NAME) -> Converter | None:
    """Find a converter without configuration: on PATH first, then inside WSL.

    The WSL probe runs a login shell so a cargo-installed ``~/.cargo/bin`` is on
    PATH, and records the resolved absolute path so later calls need no shell.
    """
    native = shutil.which(name)
    if native:
        return Converter((native,), wsl=False)
    if sys.platform == "win32" and shutil.which("wsl.exe"):
        # Source cargo's env first: a non-interactive login shell skips ~/.bashrc
        # (its early "not interactive" return), so a `cargo install`ed binary in
        # ~/.cargo/bin is otherwise off PATH. Only the default distro is probed;
        # a converter in another distro is reached via `dokey hwp --set
        # "wsl.exe -d <distro> -e <path>"`.
        try:
            found = subprocess.run(
                ["wsl.exe", "-e", "bash", "-lc",
                 f"source ~/.cargo/env 2>/dev/null; command -v {name}"],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        line = (found.stdout or "").strip().splitlines()
        if found.returncode == 0 and line and line[0].strip():
            return Converter(("wsl.exe", "-e", line[0].strip()), wsl=True)
    return None


def resolve_converter(explicit: Converter | None = None) -> tuple[Converter | None, str]:
    """Effective converter and its provenance: explicit > saved > discovered."""
    if explicit is not None:
        return explicit, "flag"
    saved = load_converter()
    if saved is not None:
        return saved, "config"
    found = discover_converter()
    if found is not None:
        return found, "discovered"
    return None, "none"


# --- conversion --------------------------------------------------------------


def _to_wsl_path(path: Path) -> str:
    """Translate a Windows path to its WSL mount (``C:\\a\\b`` -> ``/mnt/c/a/b``)."""
    resolved = path.resolve()
    drive = resolved.drive  # e.g. "C:"
    if len(drive) == 2 and drive[1] == ":":
        tail = resolved.as_posix()[2:]  # strip "C:", keep leading "/"
        return f"/mnt/{drive[0].lower()}{tail}"
    return resolved.as_posix()


def convert_to_markdown(input_path: Path, converter: Converter) -> str:
    """Run the converter's ``to-md`` and return the Markdown it produced.

    Writes to a temp file via ``-o`` (not stdout) so converter log noise and
    the Windows<->WSL boundary cannot corrupt the text.
    """
    if not input_path.is_file():
        raise SystemExit(f"HWP file not found: {input_path}")
    work = Path(tempfile.mkdtemp(prefix="dokey_hwp_"))
    out_md = work / (slugify(input_path.stem) + ".md")
    in_arg = _to_wsl_path(input_path) if converter.wsl else str(input_path)
    out_arg = _to_wsl_path(out_md) if converter.wsl else str(out_md)
    command = [*converter.argv, *_QUIET, "to-md", in_arg, "-o", out_arg]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Could not run the HWP converter ({converter.display()}): {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"HWP conversion timed out after 300s: {exc}") from exc
    if proc.returncode != 0 or not out_md.exists():
        detail = (proc.stderr or proc.stdout or "").strip() or "no output"
        raise SystemExit(
            f"HWP conversion failed ({converter.display()}, exit "
            f"{proc.returncode}):\n{detail}"
        )
    return out_md.read_text(encoding="utf-8")
