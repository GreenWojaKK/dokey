from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Private-use sentinels so highlight markers can never collide with page text.
# Consumers map these to their own markup (CLI: guillemets, UI: <mark>).
MARK_START = "\ue000"
MARK_END = "\ue001"

_SNIPPETS_PER_SECTION = 3
_MAX_PAGE_HITS = 2000
_TITLE_BOOST = 1000.0
_TOKENIZE = "unicode61 remove_diacritics 2"


@dataclass(frozen=True)
class IndexStats:
    db_path: Path
    sections: int
    pages: int
    has_page_text: bool
    created: str | None


@dataclass(frozen=True)
class SectionHit:
    section_id: int
    parent: str
    parent_folder: str
    title: str
    content_start_page: int
    content_end_page: int
    pdf_start_page: int
    pdf_end_page: int
    page_count: int
    output_file: str
    score: float
    matched_title: bool
    pages: tuple[int, ...]
    snippets: tuple[str, ...]
    printed_start_page: int | None = None
    printed_end_page: int | None = None


def index_path(lake: Path) -> Path:
    return lake / "search.db"


def find_lakes(root: Path) -> list[Path]:
    """Directories under root (depth <= 2) that contain sections.jsonl.

    The manifest is the marker because it is the one file every lake has:
    a lake is flat, so the marker sits at its root.
    """
    found = set()
    if (root / "sections.jsonl").exists():
        found.add(root)
    for pattern in ("*/sections.jsonl", "*/*/sections.jsonl"):
        for manifest in root.glob(pattern):
            # A sections.jsonl inside a silver/ folder is an unmigrated
            # layered lake showing through, not a flat lake called "silver";
            # `dokey migrate` is the answer there.
            if manifest.parent.name != "silver":
                found.add(manifest.parent)
    return sorted(found)


def _sections_path(lake: Path) -> Path:
    return lake / "sections.jsonl"


def _pages_path(lake: Path) -> Path:
    return lake / "pages.jsonl"


def _fingerprint(lake: Path) -> str:
    parts = []
    for path in (_sections_path(lake), _pages_path(lake)):
        if path.exists():
            stat = path.stat()
            parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
        else:
            parts.append(f"{path.name}:absent")
    return "|".join(parts)


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8-sig") as input_file:
        for line in input_file:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_index(lake: Path, db_path: Path | None = None) -> IndexStats:
    sections_path = _sections_path(lake)
    if not sections_path.exists():
        raise FileNotFoundError(
            f"No section manifest at {sections_path}. Run `dokey ingest` first."
        )
    sections = _read_jsonl(sections_path)
    if not sections:
        raise ValueError(f"Empty section manifest: {sections_path}")

    pages_path = _pages_path(lake)
    has_page_text = pages_path.exists()
    pages = _read_jsonl(pages_path) if has_page_text else []

    target = db_path or index_path(lake)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(target.name + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    created = datetime.now().astimezone().isoformat(timespec="seconds")
    connection = sqlite3.connect(str(tmp_path))
    try:
        connection.executescript(
            f"""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE sections (
              id INTEGER PRIMARY KEY,
              parent TEXT NOT NULL,
              parent_folder TEXT NOT NULL,
              title TEXT NOT NULL,
              content_start_page INTEGER NOT NULL,
              content_end_page INTEGER NOT NULL,
              pdf_start_page INTEGER NOT NULL,
              pdf_end_page INTEGER NOT NULL,
              page_count INTEGER NOT NULL,
              output_file TEXT NOT NULL,
              printed_start_page INTEGER,
              printed_end_page INTEGER
            );
            CREATE INDEX idx_sections_pdf_range
              ON sections (pdf_start_page, pdf_end_page);
            CREATE TABLE pages (page INTEGER PRIMARY KEY, text TEXT NOT NULL);
            CREATE VIRTUAL TABLE page_fts USING fts5(
              text, content='pages', content_rowid='page', tokenize='{_TOKENIZE}'
            );
            CREATE VIRTUAL TABLE section_fts USING fts5(
              title, parent, content='sections', content_rowid='id',
              tokenize='{_TOKENIZE}'
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO sections VALUES (
              :index, :parent, :parent_folder, :title,
              :content_start_page, :content_end_page,
              :pdf_start_page, :pdf_end_page, :page_count, :output_file,
              :printed_start_page, :printed_end_page
            )
            """,
            [
                {
                    **row,
                    "printed_start_page": row.get("printed_start_page"),
                    "printed_end_page": row.get("printed_end_page"),
                }
                for row in sections
            ],
        )
        connection.executemany(
            "INSERT INTO pages VALUES (:page, :text)",
            [{"page": row["page"], "text": row.get("text", "")} for row in pages],
        )
        connection.execute(
            "INSERT INTO page_fts (rowid, text) SELECT page, text FROM pages"
        )
        connection.execute(
            "INSERT INTO section_fts (rowid, title, parent)"
            " SELECT id, title, parent FROM sections"
        )
        connection.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ("fingerprint", _fingerprint(lake)),
                ("created", created),
                ("lake", str(lake)),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    os.replace(tmp_path, target)
    return IndexStats(
        db_path=target,
        sections=len(sections),
        pages=len(pages),
        has_page_text=has_page_text,
        created=created,
    )


def is_stale(lake: Path, db_path: Path | None = None) -> bool:
    target = db_path or index_path(lake)
    if not target.exists():
        return True
    try:
        connection = sqlite3.connect(str(target))
        try:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'fingerprint'"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return True
    return row is None or row[0] != _fingerprint(lake)


def get_index_stats(lake: Path, db_path: Path | None = None) -> IndexStats:
    target = db_path or index_path(lake)
    connection = sqlite3.connect(str(target))
    try:
        sections = connection.execute("SELECT count(*) FROM sections").fetchone()[0]
        pages = connection.execute("SELECT count(*) FROM pages").fetchone()[0]
        meta = dict(connection.execute("SELECT key, value FROM meta"))
    finally:
        connection.close()
    return IndexStats(
        db_path=target,
        sections=sections,
        pages=pages,
        has_page_text=pages > 0,
        created=meta.get("created"),
    )


def ensure_index(lake: Path, rebuild: bool = False) -> IndexStats:
    if rebuild or is_stale(lake):
        return build_index(lake)
    return get_index_stats(lake)


def _match_parses(connection: sqlite3.Connection, expression: str) -> bool:
    # FTS5 parses the MATCH expression only when the query cursor takes its
    # first step, so force one step: fetchone() (sqlite3 defers SELECT
    # execution past execute()) and LIMIT 1, not 0 (LIMIT 0 halts before the
    # FTS filter ever runs).
    try:
        connection.execute(
            "SELECT 1 FROM page_fts WHERE page_fts MATCH ? LIMIT 1", (expression,)
        ).fetchone()
        return True
    except sqlite3.OperationalError:
        return False


def _effective_match(connection: sqlite3.Connection, query: str) -> str | None:
    """Return a MATCH expression FTS5 accepts, quoting terms if raw syntax fails."""
    if _match_parses(connection, query):
        return query
    terms = [t for t in query.split() if any(c.isalnum() for c in t)]
    if not terms:
        return None
    quoted = " ".join('"' + t.replace('"', '""') + '"' for t in terms)
    if _match_parses(connection, quoted):
        return quoted
    return None


def search(
    lake: Path,
    query: str,
    limit: int = 10,
    db_path: Path | None = None,
) -> list[SectionHit]:
    if not query.strip():
        return []
    target = db_path or index_path(lake)
    if not target.exists():
        raise FileNotFoundError(
            f"No search index at {target}. Run `dokey index --lake {lake}` first."
        )

    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    try:
        match = _effective_match(connection, query)
        if match is None:
            return []

        page_rows = connection.execute(
            """
            SELECT s.*, p.page AS hit_page,
                   snippet(page_fts, 0, ?, ?, ' … ', 12) AS snip,
                   bm25(page_fts) AS page_score
            FROM page_fts
            JOIN pages p ON p.page = page_fts.rowid
            JOIN sections s
              ON p.page BETWEEN s.pdf_start_page AND s.pdf_end_page
            WHERE page_fts MATCH ?
            ORDER BY page_score
            LIMIT ?
            """,
            (MARK_START, MARK_END, match, _MAX_PAGE_HITS),
        ).fetchall()
        title_rows = connection.execute(
            """
            SELECT s.*, bm25(section_fts) AS title_score
            FROM section_fts
            JOIN sections s ON s.id = section_fts.rowid
            WHERE section_fts MATCH ?
            ORDER BY title_score
            """,
            (match,),
        ).fetchall()
    finally:
        connection.close()

    merged: dict[int, dict] = {}
    for row in page_rows:
        entry = merged.setdefault(
            row["id"],
            {"row": row, "score": row["page_score"], "matched_title": False,
             "pages": [], "snippets": []},
        )
        entry["score"] = min(entry["score"], row["page_score"])
        entry["pages"].append(row["hit_page"])
        if len(entry["snippets"]) < _SNIPPETS_PER_SECTION:
            entry["snippets"].append(row["snip"])
    for row in title_rows:
        entry = merged.setdefault(
            row["id"],
            {"row": row, "score": row["title_score"], "matched_title": False,
             "pages": [], "snippets": []},
        )
        entry["matched_title"] = True
        entry["score"] = min(entry["score"], row["title_score"]) - _TITLE_BOOST

    hits = []
    for entry in merged.values():
        row = entry["row"]
        hits.append(
            SectionHit(
                section_id=row["id"],
                parent=row["parent"],
                parent_folder=row["parent_folder"],
                title=row["title"],
                content_start_page=row["content_start_page"],
                content_end_page=row["content_end_page"],
                pdf_start_page=row["pdf_start_page"],
                pdf_end_page=row["pdf_end_page"],
                page_count=row["page_count"],
                output_file=row["output_file"],
                score=entry["score"],
                matched_title=entry["matched_title"],
                pages=tuple(sorted(entry["pages"])),
                snippets=tuple(entry["snippets"]),
                printed_start_page=row["printed_start_page"],
                printed_end_page=row["printed_end_page"],
            )
        )
    hits.sort(key=lambda hit: (hit.score, hit.section_id))
    return hits[:limit]


def resolve_artifact(lake: Path, hit: SectionHit) -> Path | None:
    """Locate the split PDF for a hit, tolerating manifests written elsewhere."""
    recorded = Path(hit.output_file)
    if recorded.exists():
        return recorded
    rebuilt = lake / "by_section" / hit.parent_folder / recorded.name
    if rebuilt.exists():
        return rebuilt
    return None
