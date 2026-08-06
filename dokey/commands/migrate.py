"""Move a layered lake to the flat layout, once.

The bronze/silver/gold layering promised a refinement chain the lake never
had: nothing in silver was derived from bronze, and the index read both. The
real division -- what the file stated against what dokey decided -- travels
in the rows themselves (``basis``, ``header_basis``, ``converted_by``), so
the folders only repeated a claim that was not true. The flat layout keeps
two directories, and both are containers rather than layers: ``by_section``
for the per-section artifacts, ``media`` for the drawn and embedded files.

Migration is a move, not a re-ingest: nothing is re-read and nothing is
re-decided. The three path facts recorded inside the files -- ``output_file``
in the manifests, ``media``/``drawing`` in the sheet rows -- are rewritten to
the new places, and the derived search index is deleted rather than patched,
since the next search rebuilds it from the manifest it finds.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# What marks a lake that has not been migrated yet.
LAYERED_MARKER = Path("silver") / "sections.jsonl"

# The manifests carry output_file paths; the JSON variant escapes its
# backslashes, the CSV does not, and a lake built on POSIX uses none.
_SECTION_REWRITES = (
    ("artifacts\\\\by_section", "by_section"),
    ("artifacts\\by_section", "by_section"),
    ("artifacts/by_section", "by_section"),
)
_MEDIA_REWRITES = (("artifacts/media/", "media/"),)


def find_layered_lakes(root: Path) -> list[Path]:
    """Layered lakes at root or up to two levels below it, sorted."""
    found = set()
    if (root / LAYERED_MARKER).is_file():
        found.add(root)
    for pattern in ("*/silver/sections.jsonl", "*/*/silver/sections.jsonl"):
        for marker in root.glob(pattern):
            found.add(marker.parent.parent)
    return sorted(found)


def _move_up(item: Path, lake: Path, notes: list[str]) -> int:
    """Move one file to the lake root; a name already taken is left alone."""
    name = "ingest.json" if item.name == "md_ingest.json" else item.name
    target = lake / name
    if target.exists():
        notes.append(f"kept {item.relative_to(lake)}: {name} already exists")
        return 0
    item.rename(target)
    return 1


def _rewrite(path: Path, replacements) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return False
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text == original:
        return False
    # Keep the BOM the CSV was written with, so Excel keeps opening it.
    encoding = "utf-8-sig" if path.suffix == ".csv" else "utf-8"
    path.write_text(text, encoding=encoding)
    return True


def migrate_lake(lake: Path) -> None:
    """Flatten one layered lake in place."""
    notes: list[str] = []
    moved = 0

    for folder in ("silver", "bronze", "raw"):
        layer = lake / folder
        if not layer.is_dir():
            continue
        for item in sorted(layer.iterdir()):
            if item.is_file():
                moved += _move_up(item, lake, notes)

    # The index is derived; the next search rebuilds it from the manifest.
    index = lake / "gold" / "search.db"
    removed_index = False
    if index.is_file():
        index.unlink()
        removed_index = True

    for child, target_name in (("by_section", "by_section"), ("media", "media")):
        source = lake / "artifacts" / child
        target = lake / target_name
        if source.is_dir() and not target.exists():
            source.rename(target)
            moved += 1
        elif source.is_dir():
            notes.append(f"kept artifacts/{child}: {target_name}/ already exists")

    for folder in ("silver", "bronze", "gold", "raw", "artifacts"):
        layer = lake / folder
        if layer.is_dir():
            try:
                layer.rmdir()
            except OSError:
                notes.append(f"left {folder}/: not empty")

    rewritten = []
    for name in (
        "sections.csv",
        "sections.json",
        "sections.jsonl",
        "sections.prefolio.jsonl",
    ):
        if _rewrite(lake / name, _SECTION_REWRITES):
            rewritten.append(name)
    for name in ("objects.jsonl", "sheet_figures.jsonl"):
        if _rewrite(lake / name, _MEDIA_REWRITES):
            rewritten.append(name)

    print(f"{lake}: {moved} item(s) moved to the lake root")
    if rewritten:
        print(f"  paths rewritten inside: {', '.join(rewritten)}")
    if removed_index:
        print("  removed the derived search index; the next search rebuilds it")
    for note in notes:
        print(f"  {note}")


def run_migrate(args: argparse.Namespace) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    root = args.path
    if not root.is_dir():
        raise SystemExit(f"Not a directory: {root}")
    lakes = find_layered_lakes(root)
    if not lakes:
        if (root / "sections.jsonl").is_file():
            print(f"{root}: already flat; nothing to migrate.")
            return
        raise SystemExit(
            f"No layered lake found under {root} (looked for "
            f"{LAYERED_MARKER.as_posix()} up to two levels deep)."
        )
    for lake in lakes:
        migrate_lake(lake)
    print(f"Migrated {len(lakes)} lake(s).")
