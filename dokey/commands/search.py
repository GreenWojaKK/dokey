from __future__ import annotations

import argparse
import sys

from .. import search as searchlib
from .common import resolve_lake


def run_index(args: argparse.Namespace) -> None:
    lake = resolve_lake(args.lake)
    stale = args.rebuild or searchlib.is_stale(lake)
    stats = searchlib.ensure_index(lake, rebuild=args.rebuild)
    action = "Built" if stale else "Up to date"
    print(
        f"{action}: {stats.db_path} "
        f"({stats.sections} sections, {stats.pages} pages)"
    )
    if not stats.has_page_text:
        print(
            "Note: no bronze/pages.jsonl, so only section titles are searchable. "
            "Re-run ingest without --no-page-text for full-text search."
        )


def run_search(args: argparse.Namespace) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    lake = resolve_lake(args.lake)
    if args.rebuild or searchlib.is_stale(lake):
        stats = searchlib.ensure_index(lake, rebuild=args.rebuild)
        print(
            f"Built index: {stats.db_path} "
            f"({stats.sections} sections, {stats.pages} pages)"
        )
    query = " ".join(args.query)
    hits = searchlib.search(lake, query, limit=args.limit)
    if not hits:
        print(f"No matches for: {query}")
        return

    for rank, hit in enumerate(hits, start=1):
        flag = "  [title match]" if hit.matched_title else ""
        # A top-level section is its own parent; printing the breadcrumb then
        # just says the title twice.
        crumb = f"{hit.parent} > {hit.title}" if hit.parent != hit.title else hit.title
        print(f"{rank:2d}. {crumb}{flag}")
        pages = ", ".join(str(page) for page in hit.pages[:8])
        if len(hit.pages) > 8:
            pages += ", ..."
        if hit.printed_start_page is not None:
            location = (
                f"    printed pp. {hit.printed_start_page}-{hit.printed_end_page}"
                f" | pdf {hit.pdf_start_page}-{hit.pdf_end_page}"
            )
        else:
            location = (
                f"    content {hit.content_start_page}-{hit.content_end_page}"
                f" | pdf {hit.pdf_start_page}-{hit.pdf_end_page}"
            )
        if pages:
            location += f" | matched pdf pages: {pages}"
        print(location)
        for snippet in hit.snippets:
            rendered = snippet.replace(searchlib.MARK_START, "«").replace(
                searchlib.MARK_END, "»"
            )
            print(f"    ... {rendered} ...")
        artifact = searchlib.resolve_artifact(lake, hit)
        if artifact is not None:
            print(f"    {artifact}")
        print()
