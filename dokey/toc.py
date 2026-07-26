from __future__ import annotations

import csv
import re
from pathlib import Path

from .models import TocEntry

PAGE_LINE_RE = re.compile(r"^(?P<title>.+?)\s+(?P<page>\d+)\s*$")
PARENT_PREFIXES = (
    "Part ",
    "Knowledge Area:",
)
EXAMPLE_PARENT_SUFFIXES = (
    "Examples",
    "Topics",
    "Research",
)


def read_toc(path: Path, toc_format: str = "auto") -> list[TocEntry]:
    detected_format = detect_format(path, toc_format)
    if detected_format == "csv":
        return read_toc_csv(path)
    if detected_format == "text":
        return read_toc_text(path.read_text(encoding="utf-8-sig"))
    raise ValueError(f"Unsupported TOC format: {detected_format}")


def detect_format(path: Path, toc_format: str) -> str:
    if toc_format != "auto":
        return toc_format
    if path.suffix.lower() == ".csv":
        return "csv"
    return "text"


def read_toc_csv(path: Path) -> list[TocEntry]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    if not rows:
        raise ValueError(f"TOC CSV is empty: {path}")

    fields = set(rows[0])
    page_key = "page" if "page" in fields else "content_start_page"
    if {"parent", "title", page_key}.issubset(fields):
        return [
            TocEntry(
                level=1,
                parent=clean(row["parent"]),
                title=required(row, "title"),
                page=int(required(row, page_key)),
            )
            for row in rows
        ]

    if {"level", "title", "page"}.issubset(fields):
        entries = [
            TocEntry(
                level=int(required(row, "level")),
                title=required(row, "title"),
                page=int(required(row, "page")),
            )
            for row in rows
        ]
        return mark_leaf_entries(fill_parents(entries))

    raise ValueError(
        "TOC CSV must contain either parent,title,page or level,title,page columns."
    )


def read_toc_text(text: str) -> list[TocEntry]:
    entries: list[TocEntry] = []
    current_parent_level = 0

    for raw_line in text.splitlines():
        parsed = parse_toc_line(raw_line, current_parent_level)
        if parsed is None:
            continue
        level, title, page = parsed
        entries.append(TocEntry(level=level, title=title, page=page))

        if looks_like_parent(title):
            current_parent_level = level

    if not entries:
        raise ValueError("No TOC entries found in text.")

    return mark_leaf_entries(fill_parents(entries))


def parse_toc_line(
    raw_line: str,
    current_parent_level: int,
) -> tuple[int, str, int] | None:
    stripped = raw_line.strip()
    if not stripped or stripped in {"Contents", "Articles"}:
        return None

    stripped = stripped.replace("\u2022", "*")
    bullet_level: int | None = None
    if stripped.startswith("*"):
        bullet_level = 0
        stripped = stripped[1:].strip()
    elif stripped.startswith("o "):
        bullet_level = 1
        stripped = stripped[2:].strip()

    match = PAGE_LINE_RE.match(stripped)
    if match is None:
        return None

    title = match.group("title").strip()
    page = int(match.group("page"))

    if bullet_level is not None:
        level = bullet_level
    elif looks_like_parent(title):
        level = 0
    else:
        level = current_parent_level + 1

    return level, title, page


def mark_leaf_entries(entries: list[TocEntry]) -> list[TocEntry]:
    leaf_entries: list[TocEntry] = []
    for index, entry in enumerate(entries):
        next_entry = entries[index + 1] if index + 1 < len(entries) else None
        has_child = next_entry is not None and next_entry.level > entry.level
        if not has_child:
            leaf_entries.append(entry)
    return leaf_entries


def fill_parents(entries: list[TocEntry]) -> list[TocEntry]:
    stack: list[TocEntry] = []
    filled: list[TocEntry] = []

    for entry in entries:
        while stack and stack[-1].level >= entry.level:
            stack.pop()

        parent = entry.parent
        if parent is None:
            parent = stack[-1].title if stack else entry.title

        filled.append(
            TocEntry(
                level=entry.level,
                title=entry.title,
                page=entry.page,
                parent=parent,
            )
        )
        stack.append(entry)

    return filled


def looks_like_parent(title: str) -> bool:
    return title.startswith(PARENT_PREFIXES) or title.endswith(EXAMPLE_PARENT_SUFFIXES)


def clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def required(row: dict[str, str], key: str) -> str:
    value = clean(row.get(key))
    if value is None:
        raise ValueError(f"Missing required TOC column value: {key}")
    return value
