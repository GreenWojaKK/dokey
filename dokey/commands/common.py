from __future__ import annotations

import argparse
import re
from pathlib import Path

from .. import mdunit
from .. import search as searchlib


def _default_lake_dir(input_pdf: Path, converter: str | None = None) -> Path:
    """Where a lake lands when the caller named no directory.

    A document that reached the lake through a converter takes that
    converter's name into the path. Two converters are not two runs of one
    thing: they read the document differently, keep different evidence, and
    yield different sections -- which is the whole reason to run both. Landing
    them on one directory means the second replaces the first, and the
    comparison is destroyed by the act of making it. Where dokey read the
    document itself there is nothing to tell apart, and the path stays the
    document's name alone.
    """
    # Keep the (often non-ASCII) document name readable in the lake path;
    # strip only the characters the filesystem rejects.
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", input_pdf.stem).strip() or "document"
    return Path("dokey_out") / (f"{stem}-{converter}" if converter else stem)


def _lake_dir(args, input_path: Path, converter: str | None = None) -> Path:
    """The directory the caller named, or the default for this source."""
    return getattr(args, "output_dir", None) or _default_lake_dir(
        input_path, converter
    )


def _section_depth_arg(value: str):
    """``auto``, ``clause``, ``subclause``, or a rung number."""
    if value in mdunit.SECTION_DEPTH_CHOICES:
        return value
    try:
        depth = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected a number or one of {', '.join(mdunit.SECTION_DEPTH_CHOICES)}, "
            f"got {value!r}"
        ) from None
    if depth < 1:
        raise argparse.ArgumentTypeError("section depth starts at 1")
    return depth


def _section_depth(args: argparse.Namespace):
    """What the caller asked for, with the older flag still honoured."""
    requested = getattr(args, "section_depth", None)
    if requested is not None:
        return requested
    return getattr(args, "outline_max_level", None)


def _outline_max_level(args: argparse.Namespace) -> int:
    """Split depth for the PDF outline path.

    The flag now defaults to unset so the Markdown path can tell "the user
    asked for depth 1" from "the user said nothing" -- there, saying nothing
    means *honor the file's own heading levels*. For an outline, saying nothing
    still means depth 1, which is what it always meant.
    """
    level = _section_depth(args)
    if isinstance(level, int):
        return level
    # An outline states its own levels, so a named depth maps to a count:
    # clauses are its top level, subclauses one below.
    return 2 if level == "subclause" else 1


def resolve_lake(lake: Path | None) -> Path:
    if lake is not None:
        if not (lake / "silver" / "sections.jsonl").exists():
            raise SystemExit(
                f"Not a lake directory (no silver/sections.jsonl): {lake}"
            )
        return lake
    candidates = searchlib.find_lakes(Path.cwd())
    if len(candidates) == 1:
        print(f"Using lake: {candidates[0]}")
        return candidates[0]
    if not candidates:
        raise SystemExit("No lake found under the current directory. Pass --lake.")
    listing = "\n".join(f"  {path}" for path in candidates)
    raise SystemExit(f"Multiple lakes found; pass --lake:\n{listing}")
