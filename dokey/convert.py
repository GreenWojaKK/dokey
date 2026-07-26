"""Bring-your-own document conversion: run a layout converter out of process.

dokey reads a PDF's text layer with pypdf and nothing else. When a PDF has no
usable text layer -- a scan -- or when its layout needs real reconstruction
(multi-column, tables, formulas), the text has to come from somewhere else.
That somewhere is a converter the user installs, the reference one being
`Docling <https://github.com/docling-project/docling>`_, and dokey invokes it
exactly as it invokes an HWP converter or an OCR server: **a separate process**,
talking through files and CLI arguments.

The separation is not ceremony. Docling brings torch, transformers, and
onnxruntime with it; installing it into dokey's core would end dokey's one
useful property, that ``pip install dokey`` is a pypdf-sized install that runs
anywhere. So the dependency is optional (``pip install dokey[docling]``), the
core never imports it, and a converter that is merely *on PATH* works just as
well as one installed through the extra.

Resolution order mirrors ``hwp.py`` and ``backends.py``:

  1. an explicit command (``dokey convert --set ...``) saved in ``~/.dokey/config.json``
  2. ``docling`` on PATH
  3. the ``docling`` package installed in the interpreter running dokey, invoked
     as ``<python> -m docling.cli.main`` -- which is what makes the extra work
     even when the environment's scripts directory is not on PATH

Two measured cautions are enforced here rather than left to the user:

*OCR is opt-in.* Docling runs OCR by default. On a PDF that already has a text
layer that is wasted work; worse, on Korean scans the default engine (a Chinese
PP-OCR model) transcribes Hanja that was never on the page -- 54 such
characters in one measured document. So dokey passes ``--no-ocr`` unless asked,
and when asked without an engine it says what the default costs.

*The converter's own Markdown is not the contract.* A render loses page
numbers, bounding boxes, and the body/furniture distinction; ``--to json``
keeps them. Markdown is the default here because the Markdown path is what
ingests today, but the JSON is what a citation-grade pipeline should consume.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import backends as backendslib

CONVERTER_NAME = "docling"
DEFAULT_TIMEOUT = 1800  # a scanned book with OCR is minutes of work, not seconds
OUTPUT_SUFFIXES = {"md": ".md", "json": ".json"}


@dataclass(frozen=True)
class Converter:
    """A resolved document-converter command prefix.

    ``argv`` is everything up to and including the tool (``("docling",)``, or
    ``(sys.executable, "-m", "docling.cli.main")``); the ``convert`` invocation
    and its options are appended at call time.
    """

    argv: tuple[str, ...]
    kind: str = CONVERTER_NAME

    def display(self) -> str:
        return " ".join(self.argv)


@dataclass(frozen=True)
class Options:
    """Converter settings worth keeping between runs.

    The OCR engine especially: ``dokey auto`` hands a scanned PDF over on its
    own, with no chance for the user to name an engine at that moment, and the
    converter's default writes Hanja onto Korean scans. Saving the choice once
    (``dokey convert --set "docling" --ocr-engine easyocr --ocr-lang ko,en``)
    is what keeps that automatic path from being automatically wrong.
    """

    ocr_engine: str | None = None
    ocr_lang: str | None = None
    device: str | None = None
    images: str | None = None

    def merged(self, **overrides) -> "Options":
        """Explicit flags win over saved settings; unset flags change nothing."""
        given = {key: value for key, value in overrides.items() if value is not None}
        return Options(
            ocr_engine=given.get("ocr_engine", self.ocr_engine),
            ocr_lang=given.get("ocr_lang", self.ocr_lang),
            device=given.get("device", self.device),
            images=given.get("images", self.images),
        )

    def describe(self) -> str:
        parts = [
            f"{key}={value}"
            for key, value in (
                ("ocr-engine", self.ocr_engine),
                ("ocr-lang", self.ocr_lang),
                ("device", self.device),
                ("images", self.images),
            )
            if value
        ]
        return ", ".join(parts) if parts else "none saved"


def load_options() -> Options:
    raw = backendslib.load_config().get("doc_converter")
    if not isinstance(raw, dict):
        return Options()
    return Options(
        ocr_engine=raw.get("ocr_engine"),
        ocr_lang=raw.get("ocr_lang"),
        device=raw.get("device"),
        images=raw.get("images"),
    )


def save_options(options: Options) -> Path:
    config = backendslib.load_config()
    entry = config.get("doc_converter")
    entry = dict(entry) if isinstance(entry, dict) else {}
    for key, value in (
        ("ocr_engine", options.ocr_engine),
        ("ocr_lang", options.ocr_lang),
        ("device", options.device),
        ("images", options.images),
    ):
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    config["doc_converter"] = entry
    return backendslib.save_config(config)


def load_converter() -> Converter | None:
    raw = backendslib.load_config().get("doc_converter")
    if isinstance(raw, dict) and isinstance(raw.get("argv"), list) and raw["argv"]:
        return Converter(
            tuple(str(part) for part in raw["argv"]), str(raw.get("kind", CONVERTER_NAME))
        )
    return None


def save_converter(converter: Converter | None) -> Path:
    """Save the command, leaving the saved options (OCR engine, device) alone.

    Re-pointing dokey at a different build of the converter is not a statement
    about which OCR engine to use, and losing that choice silently is how a
    Korean scan ends up transcribed by a Chinese model.
    """
    config = backendslib.load_config()
    if converter is None:
        config.pop("doc_converter", None)
    else:
        entry = config.get("doc_converter")
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["argv"] = list(converter.argv)
        entry["kind"] = converter.kind
        config["doc_converter"] = entry
    return backendslib.save_config(config)


def converter_from_command(command: str) -> Converter:
    import shlex

    argv = tuple(shlex.split(command, posix=(sys.platform != "win32")))
    if not argv:
        raise ValueError("Empty converter command.")
    kind = CONVERTER_NAME if CONVERTER_NAME in Path(argv[0]).name.lower() else "custom"
    return Converter(argv, kind)


def discover_converter(name: str = CONVERTER_NAME) -> Converter | None:
    """Find a converter without configuration: on PATH, else importable here."""
    on_path = shutil.which(name)
    if on_path:
        return Converter((on_path,), CONVERTER_NAME)
    if importlib.util.find_spec("docling") is not None:
        # Installed via `pip install dokey[docling]` but its console script may
        # not be on PATH (a venv that was never activated, conda on Windows).
        # Still a separate process: dokey does not import docling.
        return Converter((sys.executable, "-m", "docling.cli.main"), CONVERTER_NAME)
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


def build_command(
    converter: Converter,
    input_path: Path,
    output_dir: Path,
    *,
    to: str = "md",
    ocr: bool = False,
    ocr_engine: str | None = None,
    ocr_lang: str | None = None,
    device: str | None = None,
    images: str = "placeholder",
    extra: tuple[str, ...] = (),
) -> list[str]:
    command = [
        *converter.argv,
        "convert",
        str(input_path),
        "--to",
        to,
        "--output",
        str(output_dir),
        "--abort-on-error",
        # Docling embeds every figure as base64 by default. Measured on three
        # book pages: 1,397,804 of 1,402,431 characters were image data and
        # 4,627 were text. dokey indexes text, so a figure is marked where it
        # sits and its pixels stay out of the lake.
        "--image-export-mode",
        images,
    ]
    command.append("--ocr" if ocr else "--no-ocr")
    if ocr and ocr_engine:
        command += ["--ocr-engine", ocr_engine]
    if ocr and ocr_lang:
        command += ["--ocr-lang", ocr_lang]
    if device:
        command += ["--device", device]
    command += list(extra)
    return command


def convert(
    input_path: Path,
    converter: Converter,
    *,
    to: str = "md",
    ocr: bool = False,
    ocr_engine: str | None = None,
    ocr_lang: str | None = None,
    device: str | None = None,
    images: str = "placeholder",
    extra: tuple[str, ...] = (),
    timeout: int = DEFAULT_TIMEOUT,
    work_dir: Path | None = None,
    runner=subprocess.run,
) -> Path:
    """Convert a document and return the path to what the converter wrote."""
    if to not in OUTPUT_SUFFIXES:
        raise SystemExit(f"Unsupported conversion target: {to} (use md or json)")
    if not input_path.is_file():
        raise SystemExit(f"File not found: {input_path}")
    out_dir = work_dir or Path(tempfile.mkdtemp(prefix="dokey_convert_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(
        converter,
        input_path,
        out_dir,
        to=to,
        ocr=ocr,
        ocr_engine=ocr_engine,
        ocr_lang=ocr_lang,
        device=device,
        images=images,
        extra=extra,
    )
    try:
        proc = runner(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Could not run the converter ({converter.display()}): {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(
            f"Conversion timed out after {timeout}s. Large scans are slow; raise "
            f"--timeout, or convert once yourself and ingest the result."
        ) from exc

    produced = sorted(out_dir.glob(f"*{OUTPUT_SUFFIXES[to]}"))
    if getattr(proc, "returncode", 1) != 0 or not produced:
        detail = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").strip()
        raise SystemExit(
            f"Conversion failed ({converter.display()}, exit "
            f"{getattr(proc, 'returncode', '?')}):\n{detail or 'no output'}"
        )
    # A directory input yields several files; a single input yields one, and the
    # converter names it after the source stem.
    for candidate in produced:
        if candidate.stem == input_path.stem:
            return candidate
    return produced[0]


def install_hint() -> str:
    return (
        "No document converter found. dokey ships no layout engine; bring your own.\n"
        "The reference converter is Docling, which dokey runs at arm's length --\n"
        "a separate process -- so dokey's own install stays pypdf-sized:\n"
        "  pip install dokey[docling]     # or: pip install docling\n"
        "Point dokey at any converter with a compatible CLI:\n"
        '  dokey convert --set "docling"\n'
        '  dokey convert --set "C:/tools/docling.exe"'
    )


def ocr_engine_caution(ocr: bool, ocr_engine: str | None) -> str | None:
    """Warn once about the default OCR engine on non-Latin scans."""
    if not ocr or ocr_engine:
        return None
    return (
        "OCR is on with the converter's default engine. Measured on Korean "
        "scans, that default (a Chinese PP-OCR model) transcribes Hanja that is "
        "not on the page. For Korean, pass --ocr-engine easyocr --ocr-lang ko,en."
    )
