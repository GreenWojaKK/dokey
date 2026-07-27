"""Export what an ingest produced, and what it could not do, as CSV.

A corpus run leaves two kinds of number behind. The first is description --
how many sections, how long, how deep the ladder went -- and the second is
*defect*: text dropped that looked like prose, a document that yielded nothing,
a clause rung that fell below the split depth so its sections came out coarse.
Both belong in the record, and the second belongs there most: an ingest that
reports only its successes cannot be audited.

    python scripts/report_csv.py input/kosha_guide --out dokey_out/reports

Four files come out:

Each file counts a different thing, so their row counts are not meant to add
up to each other:

``summary.csv``    one row per corpus-level measure
``documents.csv``  one row per document: sizes, demotions, ladder, defect flags
``sections.csv``   one row per section -- the unit the manifest is made of
``defects.csv``    one row per defect instance, with the document it is in
``ladders.csv``    one row per *series per document*: its rung, and whether the
                   document or the profile's prior decided it
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dokey import ladder as ladderlib  # noqa: E402
from dokey import mdunit, paths  # noqa: E402
from dokey import profiles as profileslib  # noqa: E402

_HANGUL = re.compile(r"[가-힣]")
# A dropped line with this many syllables is a phrase, not a page mark: worth a
# human's eye even when the rule that dropped it was right.
SUSPECT_MIN_SYLLABLES = 5
# A section this long is not a citation unit, whatever the ladder said.
OVERSIZE_SECTION_CHARS = 20000


def audit(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    result = mdunit.unitize(text, fallback_title=path.stem)
    report = result.report
    profile = profileslib.resolve("auto", text)

    sizes = [len(mdunit.section_page_text(s)) for s in result.sections]
    segment = paths.SegmentReport()
    unordered = 0
    section_rows: list[dict] = []
    for section, items in paths.segment_sections(
        result.sections, profile=profile, ladder=result.ladder, report=segment
    ):
        unordered += sum(1 for item in items if not item.ordered)
        section_rows.append(
            {
                "doc_id": path.stem,
                "field": path.parent.name,
                "index": section.order,
                "level": section.level,
                "title": section.title,
                "parent": "" if section.parent == section.title else section.parent,
                "chars": len(mdunit.section_page_text(section)),
                "items": len(items),
            }
        )

    # which dropped lines carried prose
    scan = mdunit._scan(text)
    keys = mdunit._running_mark_keys(scan, profile)
    dropped, _ = mdunit._furniture_lines(scan, keys, profile)
    tables, _ = mdunit._furniture_tables(scan, keys, profile)
    suspects = [
        scan.lines[index].strip()
        for index in sorted(dropped | tables)
        if len(_HANGUL.findall(scan.lines[index])) >= SUSPECT_MIN_SYLLABLES
    ]

    ladder = result.ladder
    rank = dict(ladder.rank) if ladder else {}
    source = dict(ladder.source) if ladder else {}
    clause_rung = rank.get("integer")
    cap = report.max_level
    return {
        "doc_id": path.stem,
        "field": path.parent.name,
        "profile": report.profile,
        "chars_in": len(text),
        "headings": report.headings,
        "sections": len(result.sections),
        "section_chars_median": int(statistics.median(sizes)) if sizes else 0,
        "section_chars_max": max(sizes) if sizes else 0,
        "split_depth": cap if cap is not None else "",
        "ladder": " > ".join(ladder.order) if ladder else "",
        "rungs_observed": sum(1 for v in source.values() if v == "observed"),
        "rungs_from_prior": sum(1 for v in source.values() if v == "prior"),
        "items": segment.items,
        "items_unordered": unordered,
        "items_irregular": segment.irregular,
        "skipped_rungs": segment.skipped_rungs,
        "running_mark_lines": report.running_mark_lines,
        "furniture_tables": report.furniture_tables_dropped,
        "repeat_titles_demoted": report.repeat_titles_demoted,
        "title_echoes_demoted": report.title_echoes_demoted,
        "empty_headings_demoted": report.empty_headings_demoted,
        "fragments_demoted": report.fragments_demoted,
        "titles_rejoined": report.titles_rejoined,
        "subheadings_folded": report.subheadings_folded,
        "suspect_drops": len(suspects),
        "_sections": section_rows,
        "_suspects": suspects,
        "_rank": rank,
        "_source": source,
        "_sizes": sizes,
        "_clause_rung": clause_rung,
    }


def defects(row: dict) -> list[dict]:
    """Everything about this document that a person should look at."""
    found: list[dict] = []
    doc = row["doc_id"]
    if row["sections"] == 0:
        found.append(
            {
                "doc_id": doc,
                "kind": "no_sections",
                "detail": "the render carries no readable text (a scan converted with OCR off)",
                "count": 1,
            }
        )
    for line in row["_suspects"]:
        found.append(
            {
                "doc_id": doc,
                "kind": "suspect_drop",
                "detail": f"dropped as a running mark but reads as prose: {line[:80]}",
                "count": 1,
            }
        )
    cap = row["split_depth"]
    if row["_clause_rung"] and cap and row["_clause_rung"] > cap:
        found.append(
            {
                "doc_id": doc,
                "kind": "clause_below_split",
                "detail": (
                    f"clause numbering sits at rung {row['_clause_rung']} but the "
                    f"split depth is {cap}: the document reuses a series at two "
                    "depths, so its sections came out coarse"
                ),
                "count": 1,
            }
        )
    oversize = [size for size in row["_sizes"] if size > OVERSIZE_SECTION_CHARS]
    if oversize:
        found.append(
            {
                "doc_id": doc,
                "kind": "oversize_section",
                "detail": (
                    f"{len(oversize)} section(s) over {OVERSIZE_SECTION_CHARS} "
                    f"characters (largest {max(oversize)}): too large to cite"
                ),
                "count": len(oversize),
            }
        )
    if row["items_unordered"]:
        found.append(
            {
                "doc_id": doc,
                "kind": "unordered_list",
                "detail": (
                    f"{row['items_unordered']} item(s) whose numbering never "
                    "advances (auto-numbering that did not resolve); addressed "
                    "by position"
                ),
                "count": row["items_unordered"],
            }
        )
    if row["items_irregular"]:
        found.append(
            {
                "doc_id": doc,
                "kind": "irregular_series",
                "detail": (
                    f"{row['items_irregular']} item(s) in an off-ladder series "
                    "(1) written as 1), (가) as 가.); kept and marked"
                ),
                "count": row["items_irregular"],
            }
        )
    return found


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel opens Korean correctly only with the BOM.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path, help="Directory of .md renders")
    parser.add_argument(
        "--out", type=Path, default=Path("dokey_out/reports"), help="Where to write"
    )
    parser.add_argument(
        "--glob",
        default="**/*.md",
        help=(
            "Which files count as documents. The default sweeps every .md "
            "below the directory, which also picks up a README describing the "
            "corpus; pass '*/*.md' for a <field>/<doc>.md layout."
        ),
    )
    args = parser.parse_args(argv)

    files = sorted(args.directory.glob(args.glob))
    if not files:
        parser.error(f"No .md files under {args.directory}")

    rows = []
    section_rows: list[dict] = []
    for position, path in enumerate(files, 1):
        row = audit(path)
        rows.append(row)
        section_rows.extend(row["_sections"])
        if position % 100 == 0:
            print(f"  {position}/{len(files)}", flush=True)

    document_fields = [
        key for key in rows[0] if not key.startswith("_")
    ]
    write_csv(args.out / "documents.csv", rows, document_fields)
    write_csv(
        args.out / "sections.csv",
        section_rows,
        ["doc_id", "field", "index", "level", "title", "parent", "chars", "items"],
    )

    defect_rows = [entry for row in rows for entry in defects(row)]
    write_csv(
        args.out / "defects.csv", defect_rows, ["doc_id", "kind", "detail", "count"]
    )

    ladder_rows = [
        {
            "doc_id": row["doc_id"],
            "series": kind,
            "rung": rung,
            "decided_by": row["_source"].get(kind, "prior"),
        }
        for row in rows
        for kind, rung in sorted(row["_rank"].items(), key=lambda item: item[1])
    ]
    write_csv(
        args.out / "ladders.csv", ladder_rows, ["doc_id", "series", "rung", "decided_by"]
    )

    sizes = [size for row in rows for size in row["_sizes"]]
    kinds = Counter(entry["kind"] for entry in defect_rows)
    totals = [
        ("documents", len(rows)),
        ("documents_without_sections", sum(1 for r in rows if r["sections"] == 0)),
        ("sections", sum(r["sections"] for r in rows)),
        ("sections_median_per_document", statistics.median(r["sections"] for r in rows)),
        ("section_chars_median", int(statistics.median(sizes)) if sizes else 0),
        ("headings_read", sum(r["headings"] for r in rows)),
        ("headings_folded_into_parents", sum(r["subheadings_folded"] for r in rows)),
        ("running_mark_lines_dropped", sum(r["running_mark_lines"] for r in rows)),
        ("furniture_tables_dropped", sum(r["furniture_tables"] for r in rows)),
        ("repeat_titles_demoted", sum(r["repeat_titles_demoted"] for r in rows)),
        ("title_echoes_demoted", sum(r["title_echoes_demoted"] for r in rows)),
        ("empty_headings_demoted", sum(r["empty_headings_demoted"] for r in rows)),
        ("fragments_demoted", sum(r["fragments_demoted"] for r in rows)),
        ("titles_rejoined", sum(r["titles_rejoined"] for r in rows)),
        ("addressed_items", sum(r["items"] for r in rows)),
        ("items_unordered", sum(r["items_unordered"] for r in rows)),
        ("items_irregular", sum(r["items_irregular"] for r in rows)),
        ("skipped_rungs", sum(r["skipped_rungs"] for r in rows)),
        ("ladder_rungs_from_document", sum(r["rungs_observed"] for r in rows)),
        ("ladder_rungs_from_prior", sum(r["rungs_from_prior"] for r in rows)),
        ("defects_total", len(defect_rows)),
    ] + [(f"defects_{kind}", count) for kind, count in sorted(kinds.items())]
    write_csv(
        args.out / "summary.csv",
        [{"measure": name, "value": value} for name, value in totals],
        ["measure", "value"],
    )

    print(f"\nwrote {args.out / 'summary.csv'}")
    print(f"      {args.out / 'documents.csv'}  ({len(rows)} rows)")
    print(f"      {args.out / 'defects.csv'}    ({len(defect_rows)} rows)")
    print(f"      {args.out / 'ladders.csv'}    ({len(ladder_rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
