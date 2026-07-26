"""Audit dokey's Markdown unitizer over a directory of renders.

Unitizing a render means dropping page furniture, and a drop without a record
is a loss. This script is the record at corpus scale: it runs the unitizer over
every ``.md`` under a directory and reports what came out (sections per
document, section length) alongside what went in but not out (how many
characters were dropped, and which dropped lines carry enough prose to be worth
a human look).

    python scripts/check_md_corpus.py input/kosha_guide
    python scripts/check_md_corpus.py input/kosha_guide --max-level 2 --json out.json

The suspect list is the point. A run that drops running headers shows document
codes and publisher marks there; a run that has started eating content shows
sentences, and the thresholds in :mod:`dokey.mdunit` need revisiting.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dokey import mdunit  # noqa: E402
from dokey import profiles as profileslib  # noqa: E402

_FLAT = re.compile(r"\s+")
_HANGUL = re.compile(r"[가-힣]")
# Enough syllables to be a phrase rather than a code or a mark.
SUSPECT_MIN_SYLLABLES = 5


def flat(text: str) -> str:
    return _FLAT.sub("", text)


def audit(path: Path, max_level: int | None, profile: str) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    result = mdunit.unitize(
        text, fallback_title=path.stem, max_level=max_level, profile=profile
    )
    active = profileslib.resolve(profile, text)
    scan = mdunit._scan(text)
    keys = mdunit._running_mark_keys(scan, active)
    dropped, _ = mdunit._furniture_lines(scan, keys, active)
    table_lines, _ = mdunit._furniture_tables(scan, keys, active)
    suspects = Counter()
    for index in dropped | table_lines:
        line = scan.lines[index].strip()
        if len(_HANGUL.findall(line)) >= SUSPECT_MIN_SYLLABLES:
            suspects[line] += 1
    kept = flat("".join(section.title + section.body for section in result.sections))
    source = flat(text.replace("#", ""))
    return {
        "doc": path.stem,
        "sections": len(result.sections),
        "report": result.report,
        "suspects": suspects,
        "source_chars": len(source),
        "kept_chars": len(kept),
        "section_chars": [
            len(mdunit.section_page_text(section)) for section in result.sections
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path, help="Directory searched for .md files")
    parser.add_argument(
        "--max-level",
        type=int,
        default=None,
        help="Section depth cap. Default: 1 where levels are derived from numbering.",
    )
    parser.add_argument(
        "--profile", default="auto", choices=profileslib.AVAILABLE, help="Language profile."
    )
    parser.add_argument("--json", type=Path, default=None, help="Write the audit as JSON.")
    parser.add_argument(
        "--suspects", type=int, default=25, help="How many suspect lines to print."
    )
    args = parser.parse_args(argv)

    files = sorted(args.directory.rglob("*.md"))
    if not files:
        parser.error(f"No .md files under {args.directory}")

    audits = [audit(path, args.max_level, args.profile) for path in files]
    sections = [row["sections"] for row in audits]
    lengths = [length for row in audits for length in row["section_chars"]]
    source_chars = sum(row["source_chars"] for row in audits)
    kept_chars = sum(row["kept_chars"] for row in audits)
    suspects: Counter = Counter()
    marks: Counter = Counter()
    for row in audits:
        suspects.update(row["suspects"])
        marks.update(row["report"].running_marks)
    empty = [row["doc"] for row in audits if row["sections"] == 0]

    print(f"documents            : {len(audits)}")
    print(
        f"sections             : {sum(sections)}  "
        f"(median {statistics.median(sections):.0f}/doc, max {max(sections)})"
    )
    if lengths:
        print(f"section length       : median {statistics.median(lengths):.0f} chars")
    print(
        f"text kept            : {kept_chars:,} / {source_chars:,} "
        f"({kept_chars / max(1, source_chars):.2%})"
    )
    print(f"headings             : {sum(r['report'].headings for r in audits)}")
    print(f"  folded into parents: {sum(r['report'].subheadings_folded for r in audits)}")
    print(f"  repeated titles    : {sum(r['report'].repeat_titles_demoted for r in audits)}")
    print(f"  prose fragments    : {sum(r['report'].fragments_demoted for r in audits)}")
    print(f"  split titles rejoined: {sum(r['report'].titles_rejoined for r in audits)}")
    print(f"  title echoes       : {sum(r['report'].title_echoes_demoted for r in audits)}")
    print(f"  empty headings     : {sum(r['report'].empty_headings_demoted for r in audits)}")
    print(f"running-mark lines   : {sum(r['report'].running_mark_lines for r in audits)}")
    print(f"furniture tables     : {sum(r['report'].furniture_tables_dropped for r in audits)}")
    print(f"documents with no sections: {len(empty)} {empty[:5]}")

    print("\ntop running marks dropped")
    for text, count in marks.most_common(15):
        print(f"  {count:7d}  {text[:60]!r}")

    print(
        f"\nsuspect drops ({len(suspects)} distinct, {sum(suspects.values())} occurrences)"
        " -- dropped lines carrying prose"
    )
    for text, count in suspects.most_common(args.suspects):
        print(f"  {count:7d}  {text[:70]!r}")

    if args.json:
        payload = {
            "documents": len(audits),
            "sections": sum(sections),
            "kept_chars": kept_chars,
            "source_chars": source_chars,
            "per_document": [
                {"doc": row["doc"], "sections": row["sections"], **row["report"].as_dict()}
                for row in audits
            ],
            "suspects": [
                {"text": text, "count": count} for text, count in suspects.most_common()
            ],
        }
        args.json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
