from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from dokey import backends as backendslib
from dokey import blocks as blockslib
from dokey import bodytoc
from dokey import convert as convertlib
from dokey import folios as folioslib
from dokey import i18n as i18nlib
from dokey import ladder as ladderlib
from dokey import mdunit
from dokey import ocr as ocrlib
from dokey import paths as pathslib
from dokey import profiles as profileslib
from dokey import search as searchlib
from dokey import tocsource
from dokey.cli import main
from dokey.models import TocEntry
from dokey.ranges import build_ranges
from dokey.toc import read_toc_text


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakeReader:
    def __init__(self, texts):
        self.pages = [_FakePage(t) for t in texts]


TOC_TEXT = """\
Contents
Articles
* Part 1: Example Book 1
o Introduction 1
o Background 3
* Knowledge Area: Example Systems 6
o Example Systems 6
o Example System Concepts 9
Unbulleted Leaf Under Current Parent 12
"""


class DokeyTests(unittest.TestCase):
    def test_text_toc_parser_keeps_parent_context(self) -> None:
        entries = read_toc_text(TOC_TEXT)

        self.assertEqual([entry.title for entry in entries], [
            "Introduction",
            "Background",
            "Example Systems",
            "Example System Concepts",
            "Unbulleted Leaf Under Current Parent",
        ])
        self.assertEqual(entries[0].parent, "Part 1: Example Book")
        self.assertEqual(entries[-1].parent, "Knowledge Area: Example Systems")

    def test_ingest_pipeline_writes_lake_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "book.pdf"
            toc_path = tmp_path / "toc.txt"
            output_dir = tmp_path / "lake"

            writer = PdfWriter()
            for _ in range(15):
                writer.add_blank_page(width=72, height=72)
            with pdf_path.open("wb") as output:
                writer.write(output)
            toc_path.write_text(TOC_TEXT, encoding="utf-8")

            main([
                "ingest",
                "--input",
                str(pdf_path),
                "--toc",
                str(toc_path),
                "--output-dir",
                str(output_dir),
                "--no-page-text",
                "--section-overlap",
                "0",
            ])

            sections_csv = output_dir / "silver" / "sections.csv"
            sections_json = output_dir / "silver" / "sections.json"
            sections_jsonl = output_dir / "silver" / "sections.jsonl"
            self.assertTrue((output_dir / "raw" / "book.pdf").exists())
            self.assertTrue(sections_csv.exists())
            self.assertTrue(sections_json.exists())
            self.assertTrue(sections_jsonl.exists())

            with sections_csv.open(encoding="utf-8-sig") as input_file:
                rows = list(csv.DictReader(input_file))
            self.assertEqual(len(rows), 5)
            self.assertEqual(rows[0]["pdf_start_page"], "1")
            self.assertEqual(rows[0]["pdf_end_page"], "2")
            self.assertEqual(rows[-1]["pdf_start_page"], "12")
            self.assertEqual(rows[-1]["pdf_end_page"], "15")

            first_pdf = Path(rows[0]["output_file"])
            last_pdf = Path(rows[-1]["output_file"])
            self.assertTrue(first_pdf.exists())
            self.assertTrue(last_pdf.exists())
            self.assertEqual(len(PdfReader(str(first_pdf)).pages), 2)
            self.assertEqual(len(PdfReader(str(last_pdf)).pages), 4)

    def test_ingest_pipeline_can_use_pdf_outline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "book.pdf"
            output_dir = tmp_path / "lake"

            writer = PdfWriter()
            for _ in range(6):
                writer.add_blank_page(width=72, height=72)
            chapter = writer.add_outline_item("Chapter 1", 0)
            writer.add_outline_item("Section 1.1", 0, parent=chapter)
            writer.add_outline_item("Section 1.2", 2, parent=chapter)
            with pdf_path.open("wb") as output:
                writer.write(output)

            main([
                "ingest",
                "--input",
                str(pdf_path),
                "--toc-from-outline",
                "--output-dir",
                str(output_dir),
                "--no-page-text",
                "--section-overlap",
                "0",
            ])

            sections_csv = output_dir / "silver" / "sections.csv"
            with sections_csv.open(encoding="utf-8-sig") as input_file:
                rows = list(csv.DictReader(input_file))

            self.assertEqual([row["title"] for row in rows], ["Section 1.1", "Section 1.2"])
            self.assertEqual(rows[0]["parent"], "Chapter 1")
            self.assertEqual(rows[0]["pdf_start_page"], "1")
            self.assertEqual(rows[0]["pdf_end_page"], "2")
            self.assertEqual(rows[1]["pdf_start_page"], "3")
            self.assertEqual(rows[1]["pdf_end_page"], "6")


class I18nTests(unittest.TestCase):
    def test_languages_have_the_same_translation_keys(self) -> None:
        self.assertEqual(
            set(i18nlib.TRANSLATIONS["ko"]),
            set(i18nlib.TRANSLATIONS["en"]),
        )

    def test_korean_is_default_and_saved_english_is_respected(self) -> None:
        self.assertEqual(i18nlib.preferred_language({}), "ko")
        self.assertEqual(
            i18nlib.preferred_language({"language": "en"}),
            "en",
        )
        self.assertEqual(
            i18nlib.preferred_language({"language": "unsupported"}),
            "ko",
        )

    def test_translation_formats_dynamic_values(self) -> None:
        self.assertEqual(
            i18nlib.translate("ko", "index_stats", sections=3, pages=10),
            "섹션 3개 / 페이지 10개 색인됨",
        )
        self.assertEqual(
            i18nlib.translate("en", "index_stats", sections=3, pages=10),
            "3 sections / 10 pages indexed",
        )


SEARCH_SECTIONS = [
    {
        "index": 1,
        "parent_index": 1,
        "parent_item_index": 1,
        "parent": "Chapter 1",
        "parent_folder": "001_Chapter_1",
        "title": "Pressure Basics",
        "content_start_page": 1,
        "content_end_page": 2,
        "pdf_start_page": 1,
        "pdf_end_page": 2,
        "page_count": 2,
        "output_file": "somewhere/artifacts/by_section/001_Chapter_1/001_Pressure_Basics_content_001-002_pdf_001-002.pdf",
    },
    {
        "index": 2,
        "parent_index": 1,
        "parent_item_index": 2,
        "parent": "Chapter 1",
        "parent_folder": "001_Chapter_1",
        "title": "Flow Measurement",
        "content_start_page": 3,
        "content_end_page": 4,
        "pdf_start_page": 3,
        "pdf_end_page": 4,
        "page_count": 2,
        "output_file": "somewhere/artifacts/by_section/001_Chapter_1/002_Flow_Measurement_content_003-004_pdf_003-004.pdf",
    },
    {
        "index": 3,
        "parent_index": 2,
        "parent_item_index": 1,
        "parent": "Chapter 2",
        "parent_folder": "002_Chapter_2",
        "title": "Controller Tuning",
        "content_start_page": 4,
        "content_end_page": 6,
        "pdf_start_page": 4,
        "pdf_end_page": 6,
        "page_count": 3,
        "output_file": "somewhere/artifacts/by_section/002_Chapter_2/001_Controller_Tuning_content_004-006_pdf_004-006.pdf",
    },
]

SEARCH_PAGES = [
    {"page": 1, "text": "Pressure sensors measure static pressure in vessels."},
    {"page": 2, "text": "A differential pressure cell spans the orifice plate."},
    {"page": 3, "text": "Turbine meters report volumetric flow readings."},
    {"page": 4, "text": "Flow compensation and controller interaction in the loop."},
    {"page": 5, "text": "Tuning rules include Ziegler-Nichols methods."},
    {"page": 6, "text": "Cascade structures stabilize the inner loop."},
]


def write_search_lake(lake: Path, with_pages: bool = True) -> None:
    silver = lake / "silver"
    silver.mkdir(parents=True, exist_ok=True)
    with (silver / "sections.jsonl").open("w", encoding="utf-8") as output:
        for row in SEARCH_SECTIONS:
            output.write(json.dumps(row) + "\n")
    if with_pages:
        bronze = lake / "bronze"
        bronze.mkdir(parents=True, exist_ok=True)
        with (bronze / "pages.jsonl").open("w", encoding="utf-8") as output:
            for row in SEARCH_PAGES:
                output.write(json.dumps(row) + "\n")


class SearchTests(unittest.TestCase):
    def test_build_index_reports_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            write_search_lake(lake)

            stats = searchlib.build_index(lake)

            self.assertEqual(stats.sections, 3)
            self.assertEqual(stats.pages, 6)
            self.assertTrue(stats.has_page_text)
            self.assertTrue((lake / "gold" / "search.db").exists())
            self.assertFalse(searchlib.is_stale(lake))

    def test_search_maps_page_hits_to_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            write_search_lake(lake)
            searchlib.build_index(lake)

            hits = searchlib.search(lake, "orifice")
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].section_id, 1)
            self.assertEqual(hits[0].pages, (2,))
            marked = searchlib.MARK_START + "orifice" + searchlib.MARK_END
            self.assertIn(marked, hits[0].snippets[0])

            hits = searchlib.search(lake, "flow")
            by_id = {hit.section_id: hit for hit in hits}
            self.assertIn(2, by_id)
            self.assertIn(3, by_id)
            self.assertEqual(by_id[2].pages, (3, 4))
            self.assertEqual(by_id[3].pages, (4,))

    def test_title_match_is_boosted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            write_search_lake(lake)
            searchlib.build_index(lake)

            hits = searchlib.search(lake, "controller")

            self.assertEqual(hits[0].section_id, 3)
            self.assertTrue(hits[0].matched_title)
            by_id = {hit.section_id: hit for hit in hits}
            self.assertIn(2, by_id)
            self.assertFalse(by_id[2].matched_title)

    def test_bad_fts_syntax_falls_back_to_quoted_terms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            write_search_lake(lake)
            searchlib.build_index(lake)

            hits = searchlib.search(lake, "flow AND (")
            self.assertTrue(hits)
            self.assertIn(2, {hit.section_id for hit in hits})

            self.assertEqual(searchlib.search(lake, "((("), [])
            self.assertEqual(searchlib.search(lake, "   "), [])

    def test_stale_index_is_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            write_search_lake(lake)
            searchlib.build_index(lake)

            with (lake / "bronze" / "pages.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps({"page": 7, "text": "Appendix text."}) + "\n")

            self.assertTrue(searchlib.is_stale(lake))
            stats = searchlib.ensure_index(lake)
            self.assertEqual(stats.pages, 7)

    def test_missing_page_text_builds_title_only_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            write_search_lake(lake, with_pages=False)

            stats = searchlib.build_index(lake)
            self.assertFalse(stats.has_page_text)
            self.assertEqual(stats.pages, 0)

            hits = searchlib.search(lake, "controller")
            self.assertEqual([hit.section_id for hit in hits], [3])
            self.assertTrue(hits[0].matched_title)
            self.assertEqual(searchlib.search(lake, "orifice"), [])

    def test_resolve_artifact_falls_back_to_lake_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            write_search_lake(lake)
            searchlib.build_index(lake)
            hit = searchlib.search(lake, "orifice")[0]

            self.assertIsNone(searchlib.resolve_artifact(lake, hit))

            rebuilt = (
                lake
                / "artifacts"
                / "by_section"
                / hit.parent_folder
                / Path(hit.output_file).name
            )
            rebuilt.parent.mkdir(parents=True, exist_ok=True)
            rebuilt.write_bytes(b"%PDF-1.4 stub")
            self.assertEqual(searchlib.resolve_artifact(lake, hit), rebuilt)

    def test_cli_index_and_search_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            write_search_lake(lake)

            main(["index", "--lake", str(lake)])
            main(["search", "orifice", "--lake", str(lake)])


class FolioParsingTests(unittest.TestCase):
    def test_merged_header_folio(self) -> None:
        text = "text [58, 262, 343, 330]6 BASIC CONTINUOUS CONTROL - I"
        values = [c.value for c in ocrlib.folio_candidates(text, max_folio=600)]
        self.assertIn(6, values)

    def test_page_number_label_is_top_tier(self) -> None:
        text = "page_number [921, 832, 943, 879]19"
        cands = ocrlib.folio_candidates(text, max_folio=600)
        self.assertEqual(cands[0].value, 19)
        self.assertEqual(cands[0].tier, 0)
        self.assertEqual(ocrlib.pick_folio(cands, expected=None), 19)

    def test_out_of_range_token_is_filtered(self) -> None:
        text = "text [0,0,0,0]7\ntext [0,0,0,0]940"
        values = [c.value for c in ocrlib.folio_candidates(text, max_folio=520)]
        self.assertIn(7, values)
        self.assertNotIn(940, values)

    def test_chapter_number_is_not_chosen_over_folio(self) -> None:
        text = (
            "header [648, 262, 895, 337]Chapter 1: Process Instrumentation\n"
            "text [60, 262, 90, 330]7"
        )
        cands = ocrlib.folio_candidates(text, max_folio=600)
        # 7 is the folio; expected anchors selection near 7 even though "1" appears.
        self.assertEqual(ocrlib.pick_folio(cands, expected=7), 7)

    def test_pick_prefers_tier_then_expected(self) -> None:
        cands = [
            ocrlib.FolioCandidate(1, ocrlib._TIER_EDGE),
            ocrlib.FolioCandidate(19, ocrlib._TIER_PAGE_NUMBER),
        ]
        self.assertEqual(ocrlib.pick_folio(cands, expected=1), 19)

    def test_build_map_uses_anchor_and_interpolates(self) -> None:
        # Render is stubbed to hand the page number to the fake OCR client.
        original_render = ocrlib.render_band
        ocrlib.render_band = lambda pdf, page, where, frac, dpi=200: str(page).encode()

        class FakeClient:
            def __init__(self, folios, blank):
                self.folios = folios
                self.blank = set(blank)

            def transcribe(self, png):
                page = int(png.decode())
                if page in self.blank:
                    return "title [0,0,0,0]Chapter opening with no visible number"
                return f"page_number [0,0,0,0]{self.folios[page]}"

        try:
            client = FakeClient({14: 3, 15: 4, 16: 5, 18: 7}, blank={17})
            results = ocrlib.build_folio_map(
                client, Path("dummy.pdf"), [14, 15, 16, 17, 18], max_folio=600
            )
        finally:
            ocrlib.render_band = original_render

        self.assertEqual(results[14].folio, 3)
        self.assertEqual(results[16].folio, 5)
        self.assertEqual(results[18].folio, 7)
        # Page 17 had no readable folio; interpolated from neighbors -> 6.
        self.assertEqual(results[17].folio, 6)
        self.assertEqual(results[17].source, "interpolated")

    def test_save_and_load_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "folios.jsonl"
            results = {
                14: ocrlib.FolioResult(14, 3, "ocr"),
                15: ocrlib.FolioResult(15, 4, "interpolated"),
            }
            ocrlib.save_folio_map(path, results)
            cache = ocrlib.load_cache(path)
            self.assertEqual(cache, {14: 3})  # only OCR-sourced folios are cached


class CalibrationTests(unittest.TestCase):
    def _run(self, offset_fn, misreads, first, last):
        original_render = ocrlib.render_band
        ocrlib.render_band = lambda pdf, page, where, frac, dpi=200: str(page).encode()

        class FakeClient:
            def __init__(self, offset_fn, misreads):
                self.offset_fn = offset_fn
                self.misreads = misreads
                self.calls = 0

            def transcribe(self, png):
                self.calls += 1
                page = int(png.decode())
                folio = self.misreads.get(page, page - self.offset_fn(page))
                return f"page_number [0,0,0,0]{folio}"

        try:
            client = FakeClient(offset_fn, misreads)
            model = ocrlib.calibrate_offsets(
                client, Path("dummy.pdf"), first, last, max_folio=600
            )
        finally:
            ocrlib.render_band = original_render
        return model

    def test_recovers_piecewise_offsets(self) -> None:
        offset_fn = lambda p: 11 if p < 30 else (10 if p < 70 else 9)
        model = self._run(offset_fn, {}, first=15, last=100)
        self.assertEqual(model.folio_at(25), 14)  # 25 - 11
        self.assertEqual(model.folio_at(35), 25)  # 35 - 10
        self.assertEqual(model.folio_at(80), 71)  # 80 - 9
        self.assertEqual({s.offset for s in model.segments}, {11, 10, 9})

    def test_rejects_isolated_misread(self) -> None:
        offset_fn = lambda p: 11 if p < 30 else (10 if p < 70 else 9)
        # page 50 reads a garbage folio; slope-1 confirmation must reject it.
        model = self._run(offset_fn, {50: 1}, first=15, last=100)
        self.assertEqual(model.folio_at(50), 40)  # 50 - 10, not 50 - (50-1)
        self.assertNotIn(49, {s.offset for s in model.segments})


class SectionOverlapTests(unittest.TestCase):
    ENTRIES = [
        TocEntry(level=1, title="1.2 Pressure", page=15, parent="1"),
        TocEntry(level=1, title="1.3 Level", page=16, parent="1"),
        TocEntry(level=1, title="1.4 Flow", page=19, parent="1"),
    ]

    def _ranges(self, overlap):
        return build_ranges(
            self.ENTRIES, Path("out"), total_pdf_pages=30,
            pdf_page_offset=0, max_content_page=None, section_overlap=overlap,
        )

    def test_default_no_overlap_truncates_shared_page(self) -> None:
        r = self._ranges(0)
        self.assertEqual((r[0].pdf_start_page, r[0].pdf_end_page), (15, 15))
        self.assertEqual((r[1].pdf_start_page, r[1].pdf_end_page), (16, 18))

    def test_overlap_keeps_boundary_page_in_earlier_section(self) -> None:
        r = self._ranges(1)
        # 1.2 now keeps page 16, which it shares with 1.3
        self.assertEqual((r[0].pdf_start_page, r[0].pdf_end_page), (15, 16))
        self.assertEqual((r[1].pdf_start_page, r[1].pdf_end_page), (16, 19))
        # the final section has no successor, so overlap does not extend it
        self.assertEqual(r[2].pdf_end_page, 30)

    def test_cli_default_overlap_is_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "book.pdf"
            output_dir = tmp_path / "lake"
            writer = PdfWriter()
            for _ in range(6):
                writer.add_blank_page(width=72, height=72)
            chapter = writer.add_outline_item("Chapter 1", 0)
            writer.add_outline_item("Section 1.1", 0, parent=chapter)
            writer.add_outline_item("Section 1.2", 2, parent=chapter)
            with pdf_path.open("wb") as output:
                writer.write(output)

            # no --section-overlap flag -> default (1)
            main([
                "ingest", "--input", str(pdf_path), "--toc-from-outline",
                "--output-dir", str(output_dir), "--no-page-text",
            ])
            with (output_dir / "silver" / "sections.csv").open(encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
            # 1.1 (pdf 1) shares its boundary page 3 with 1.2 by default
            self.assertEqual(rows[0]["pdf_start_page"], "1")
            self.assertEqual(rows[0]["pdf_end_page"], "3")
            self.assertEqual(rows[1]["pdf_start_page"], "3")


class TocFolioTests(unittest.TestCase):
    def test_section_key(self) -> None:
        self.assertEqual(folioslib.section_key("1.3 Level"), "1.3")
        self.assertEqual(folioslib.section_key("6.10 Loop Diagrams"), "6.10")
        self.assertEqual(folioslib.section_key("10 Motor and Drive Control"), "10")
        self.assertEqual(folioslib.section_key("A.7 SCADA Systems"), "A.7")
        self.assertIsNone(folioslib.section_key("About the Author"))

    def test_find_and_parse_toc(self) -> None:
        toc_page = (
            "Table of Contents\n"
            "1.1 Introduction, 3\n1.2 Pressure, 4\n1.3 Level, 5\n"
            "1.4 Flow, 8\n2.1 Introduction, 19\nAbout the Author, 17\n"
        )
        reader = _FakeReader(["cover", "preface text", toc_page, "1 body text only"])
        toc_pages = folioslib.find_toc_pages(reader)
        self.assertEqual(toc_pages, [3])
        number_map = folioslib.parse_toc_number_map(reader, toc_pages)
        self.assertEqual(number_map["1.3"], 5)
        self.assertEqual(number_map["2.1"], 19)
        self.assertNotIn(None, number_map)  # "About the Author" is skipped

    def test_apply_folios_matches_derives_and_flags_front_matter(self) -> None:
        rows = [
            {"title": "Front Matter", "pdf_start_page": 1, "pdf_end_page": 2},
            {"title": "1.1 Introduction", "pdf_start_page": 14, "pdf_end_page": 14},
            {"title": "1.2 Pressure", "pdf_start_page": 15, "pdf_end_page": 15},
            {"title": "About the Author", "pdf_start_page": 16, "pdf_end_page": 16},
            {"title": "2.1 Intro", "pdf_start_page": 20, "pdf_end_page": 22},
        ]
        stats = folioslib.apply_folios(rows, {"1.1": 3, "1.2": 4, "2.1": 8})

        by_title = {r["title"]: r for r in rows}
        self.assertEqual(by_title["1.1 Introduction"]["printed_start_page"], 3)
        self.assertEqual(by_title["1.1 Introduction"]["folio_source"], "toc")
        self.assertIsNone(by_title["Front Matter"]["printed_start_page"])
        self.assertEqual(by_title["Front Matter"]["folio_source"], "front-matter")
        # nearest matched to pdf 16 is 1.2 (pdf 15, offset 11) -> printed 5
        self.assertEqual(by_title["About the Author"]["printed_start_page"], 5)
        self.assertEqual(by_title["About the Author"]["folio_source"], "derived")
        # slope-1 within the multi-page section 2.1: 8 + (22-20) = 10
        self.assertEqual(by_title["2.1 Intro"]["printed_end_page"], 10)
        self.assertEqual(stats.matched, 3)
        self.assertEqual(stats.derived, 1)
        self.assertEqual(stats.front_matter, 1)
        self.assertEqual((stats.offset_min, stats.offset_max), (11, 12))

    def test_apply_folios_is_idempotent(self) -> None:
        rows = [
            {"title": "1.1 A", "pdf_start_page": 14, "pdf_end_page": 14},
            {"title": "1.2 B", "pdf_start_page": 15, "pdf_end_page": 16},
        ]
        toc_map = {"1.1": 3, "1.2": 4}
        folioslib.apply_folios(rows, toc_map)
        folioslib.apply_folios(rows, toc_map)  # re-run must not accumulate keys
        self.assertNotIn("_offset", rows[0])
        self.assertEqual(rows[0]["printed_start_page"], 3)
        self.assertEqual(rows[1]["printed_end_page"], 5)


class PrintedPageIndexTests(unittest.TestCase):
    def test_printed_pages_surface_in_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lake = Path(tmp) / "lake"
            write_search_lake(lake)
            rows = searchlib._read_jsonl(lake / "silver" / "sections.jsonl")
            for row in rows:
                row["printed_start_page"] = row["pdf_start_page"] + 100
                row["printed_end_page"] = row["pdf_end_page"] + 100
            from dokey.manifest import write_manifest_rows

            write_manifest_rows(lake, rows)
            searchlib.build_index(lake)

            hit = searchlib.search(lake, "orifice")[0]
            self.assertEqual(hit.pdf_start_page, 1)
            self.assertEqual(hit.printed_start_page, 101)


@unittest.skipUnless(
    importlib.util.find_spec("streamlit") is not None, "streamlit not installed"
)
class UiSmokeTests(unittest.TestCase):
    def test_app_renders_and_searches(self) -> None:
        from streamlit.testing.v1 import AppTest

        app_path = Path(__file__).resolve().parents[1] / "dokey" / "ui_app.py"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            write_search_lake(tmp_path / "lake")
            previous_cwd = Path.cwd()
            previous_config = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = str(tmp_path / "config")
            os.chdir(tmp_path)
            try:
                app = AppTest.from_file(str(app_path), default_timeout=30).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.radio(key="ui_language").value, "ko")
                self.assertEqual(app.text_input(key="query").label, "검색")

                app.text_input(key="query").set_value("orifice").run()
                self.assertFalse(app.exception)
                rendered = " ".join(str(block.value) for block in app.markdown)
                self.assertIn("Pressure Basics", rendered)

                app.radio(key="ui_language").set_value("en").run()
                self.assertFalse(app.exception)
                self.assertEqual(app.text_input(key="query").label, "Search")
                self.assertEqual(backendslib.load_config().get("language"), "en")
            finally:
                os.chdir(previous_cwd)
                if previous_config is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous_config


class FolderPickerTests(unittest.TestCase):
    """The folder chooser: its own process, and an answer that survives Korean."""

    def test_the_dialog_program_is_valid_python_for_any_path_or_title(self) -> None:
        from dokey import pickers

        snippet = pickers.folder_dialog_snippet(
            "dokey 라이브러리 폴더 선택", Path(r"C:\사용자\Temp\my folder\answer.txt")
        )
        compile(snippet, "<picker>", "exec")  # quoting is the whole risk here
        self.assertIn("askdirectory", snippet)

    def test_a_chosen_folder_comes_back_whatever_the_console_codepage(self) -> None:
        from dokey import pickers

        if not pickers.HAS_FOLDER_PICKER:
            self.skipTest("tkinter not installed")
        picked = r"C:\문서\내 라이브러리"

        def runner(command, **kwargs):
            # Stand in for the dialog: write the answer where it is expected.
            snippet = command[command.index("-c") + 1]
            target = Path(json.loads(snippet.split("Path(")[1].split(")")[0]))
            target.write_text(picked, encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        self.assertEqual(pickers.choose_folder("pick", runner=runner), picked)

    def test_a_cancelled_dialog_changes_nothing(self) -> None:
        from dokey import pickers

        if not pickers.HAS_FOLDER_PICKER:
            self.skipTest("tkinter not installed")

        def runner(command, **kwargs):  # the user pressed Cancel: empty answer
            return subprocess.CompletedProcess(command, 0, "", "")

        self.assertIsNone(pickers.choose_folder("pick", runner=runner))


@unittest.skipUnless(
    importlib.util.find_spec("streamlit") is not None, "streamlit not installed"
)
class UiIngestPanelTests(unittest.TestCase):
    """The Add-a-book panel offers the smart auto path by default."""

    def _app(self, tmp_path: Path):
        from streamlit.testing.v1 import AppTest

        app_path = Path(__file__).resolve().parents[1] / "dokey" / "ui_app.py"
        write_search_lake(tmp_path / "lake")
        return AppTest.from_file(str(app_path), default_timeout=30)

    def test_auto_is_the_default_mode_with_offset_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            previous_cwd = Path.cwd()
            previous_config = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = str(tmp_path / "config")
            os.chdir(tmp_path)
            try:
                app = self._app(tmp_path).run()
                self.assertFalse(app.exception)
                # Auto is the default; no manual page-offset spinner is shown.
                self.assertEqual(app.radio(key="ing_mode").value, "auto")
                offset_keys = {widget.key for widget in app.text_input}
                self.assertIn("auto_offset", offset_keys)
                number_keys = {widget.key for widget in app.number_input}
                self.assertNotIn("ing_offset", number_keys)
            finally:
                os.chdir(previous_cwd)
                if previous_config is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous_config

    def test_the_layout_converter_is_offered_without_configuring_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            previous_cwd = Path.cwd()
            previous_config = os.environ.get("DOKEY_CONFIG_DIR")
            # An empty config dir: nothing is saved, so whatever the panel
            # offers came from discovery alone.
            os.environ["DOKEY_CONFIG_DIR"] = str(tmp_path / "config")
            os.chdir(tmp_path)
            try:
                app = self._app(tmp_path).run()
                self.assertFalse(app.exception)
                self.assertIn(
                    "auto_convert", {widget.key for widget in app.selectbox}
                )
                self.assertEqual(app.selectbox(key="auto_convert").value, "auto")
            finally:
                os.chdir(previous_cwd)
                if previous_config is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous_config

    def test_a_library_is_opened_with_a_folder_dialog_not_a_typed_path(self) -> None:
        from dokey import pickers

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            previous_cwd = Path.cwd()
            previous_config = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = str(tmp_path / "config")
            os.chdir(tmp_path)
            try:
                app = self._app(tmp_path).run()
                self.assertFalse(app.exception)
                buttons = {widget.key for widget in app.button}
                text_inputs = {widget.key for widget in app.text_input}
                if pickers.HAS_FOLDER_PICKER:
                    self.assertIn("lake_browse", buttons)
                    self.assertNotIn("lake_path", text_inputs)
                else:
                    self.assertIn("lake_path", text_inputs)
            finally:
                os.chdir(previous_cwd)
                if previous_config is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous_config

    def test_manual_mode_reveals_toc_source_and_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            previous_cwd = Path.cwd()
            previous_config = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = str(tmp_path / "config")
            os.chdir(tmp_path)
            try:
                app = self._app(tmp_path).run()
                app.radio(key="ing_mode").set_value("manual").run()
                self.assertFalse(app.exception)
                self.assertEqual(app.radio(key="ing_toc_source").value, "outline")
                number_keys = {widget.key for widget in app.number_input}
                self.assertIn("ing_offset", number_keys)
            finally:
                os.chdir(previous_cwd)
                if previous_config is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous_config


_HAS_FITZ = importlib.util.find_spec("fitz") is not None


@unittest.skipUnless(_HAS_FITZ, "PyMuPDF (optional [ocr] extra) not installed")
class CoordinateTocTests(unittest.TestCase):
    """Reconstructing a TOC from a printed contents page by word geometry."""

    def _toc_pdf(self, tmp: Path, *, margins=(0,)) -> Path:
        import fitz

        # Two logical levels: chapter at x=72, subsection at x=108, with the
        # page number as the trailing token on the same line (no dot leaders).
        # ``margins`` shifts a page's left edge to emulate recto/verso drift.
        doc = fitz.open()
        for shift in margins:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72 + shift, 40), "Contents", fontsize=14)

            def row(y, x, text):
                page.insert_text((x + shift, y), text, fontsize=11)

            row(80, 72, "1 First Chapter 1")
            row(100, 108, "1.1 Alpha 1")
            row(120, 108, "1.2 Beta 3")
            # A wrapped subsection title: the page number lands on line two.
            row(140, 108, "1.3 A Very Long Subsection Title That")
            row(158, 132, "Wraps To A Second Line 7")
            row(180, 108, "About the Author 9")
            row(210, 72, "2 Second Chapter 11")
            row(230, 108, "2.1 Gamma 11")
        path = tmp / f"toc_{len(margins)}.pdf"
        doc.save(str(path))
        doc.close()
        return path

    def test_levels_titles_and_pages(self) -> None:
        from dokey.tocpage import read_page_toc

        with tempfile.TemporaryDirectory() as tmp:
            entries = read_page_toc(self._toc_pdf(Path(tmp)))
        by_title = {e.title: e for e in entries}

        # Chapters (parents) are dropped; their subsections survive as leaves.
        self.assertIn("1.1 Alpha", by_title)
        self.assertNotIn("1 First Chapter", by_title)
        self.assertEqual(by_title["1.1 Alpha"].page, 1)
        self.assertEqual(by_title["1.1 Alpha"].parent, "1 First Chapter")
        self.assertEqual(by_title["2.1 Gamma"].parent, "2 Second Chapter")
        # Subsections sit one level below their chapter.
        self.assertEqual(by_title["1.1 Alpha"].level, by_title["1.2 Beta"].level)
        self.assertGreater(by_title["1.1 Alpha"].level, 0)

    def test_wrapped_title_is_merged(self) -> None:
        from dokey.tocpage import read_page_toc

        with tempfile.TemporaryDirectory() as tmp:
            entries = read_page_toc(self._toc_pdf(Path(tmp)))
        merged = [e for e in entries if e.title.startswith("1.3")]
        self.assertEqual(len(merged), 1)
        self.assertEqual(
            merged[0].title, "1.3 A Very Long Subsection Title That Wraps To A Second Line"
        )
        self.assertEqual(merged[0].page, 7)
        # The stray continuation fragment must not become its own entry.
        self.assertNotIn("Wraps To A Second Line", {e.title for e in entries})

    def _report_toc_pdf(self, tmp: Path) -> Path:
        """A Korean report's contents: the three shapes measured on one.

        Front matter folioed in Roman numerals; division headers that carry no
        page number of their own; and, on the page after, the list of tables,
        set in the same two columns under the same running head.
        """
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        # The built-in Korean font: the base-14 fonts have no Hangul, and text
        # that does not render does not extract either.
        page.insert_text((72, 40), "차 례", fontsize=14, fontname="korea")

        def row(y, x, text):
            page.insert_text((x, y), text, fontsize=11, fontname="korea")

        row(80, 96, "요 약····················ⅴ")
        row(110, 96, "제1장 서론")
        row(130, 132, "1. 연구의 배경····3")
        row(150, 132, "2. 주요 개념····5")
        # 0.4pt to the left of the first chapter: the jitter of setting the
        # same tier twice, which is not a level.
        row(180, 95.6, "제2장 분석")
        row(200, 132, "1. 일반 현황····17")
        row(220, 132, "2. 배출량 분석····28")

        tables = doc.new_page(width=612, height=792)
        tables.insert_text((72, 40), "차 례", fontsize=14, fontname="korea")
        for index in range(6):
            tables.insert_text(
                (96, 80 + index * 20),
                f"<표 2-{index + 1}> 배출량 현황····{18 + index}",
                fontsize=11,
                fontname="korea",
            )
        path = tmp / "report_toc.pdf"
        doc.save(str(path))
        doc.close()
        return path

    def test_a_division_header_is_an_entry_not_a_wrapped_title(self) -> None:
        from dokey.tocpage import read_page_toc

        with tempfile.TemporaryDirectory() as tmp:
            entries = read_page_toc(self._report_toc_pdf(Path(tmp)))
        by_title = {e.title: e for e in entries}

        # The chapter's opening clause keeps its own row and its own page,
        # rather than being absorbed into the header above it and then dropped
        # with it as a parent.
        self.assertIn("1. 연구의 배경", by_title)
        self.assertEqual(by_title["1. 연구의 배경"].page, 3)
        self.assertEqual(by_title["1. 연구의 배경"].parent, "제1장 서론")
        self.assertEqual(by_title["1. 일반 현황"].parent, "제2장 분석")
        # Sub-point drift in the header's own indentation is not a level.
        self.assertEqual(
            by_title["1. 연구의 배경"].level, by_title["1. 일반 현황"].level
        )

    def test_roman_front_matter_does_not_stick_to_the_next_entry(self) -> None:
        from dokey.tocpage import read_page_toc

        with tempfile.TemporaryDirectory() as tmp:
            entries = read_page_toc(self._report_toc_pdf(Path(tmp)))

        # The summary is folioed ⅴ, on a series the body's page numbers do not
        # share, so it is left out -- but recognized, so it does not ride along
        # on the title below it.
        self.assertNotIn("요 약", {e.title for e in entries})
        for entry in entries:
            self.assertNotIn("약", entry.title)

    def test_a_list_of_tables_is_not_a_table_of_contents(self) -> None:
        from dokey.tocpage import find_toc_pages, read_page_toc

        with tempfile.TemporaryDirectory() as tmp:
            path = self._report_toc_pdf(Path(tmp))
            entries = read_page_toc(path)

            import fitz

            with fitz.open(str(path)) as doc:
                self.assertEqual(find_toc_pages(doc), [0])

        self.assertFalse([e for e in entries if e.title.startswith("<표")])

    def test_margin_drift_keeps_levels_consistent(self) -> None:
        from dokey.tocpage import read_page_toc

        # Facing pages whose left margins differ by 18pt must not split one
        # logical level into two.
        with tempfile.TemporaryDirectory() as tmp:
            entries = read_page_toc(self._toc_pdf(Path(tmp), margins=(0, 18)))
        subsection_levels = {
            e.level for e in entries if _SECTION_NO_TWO_PART(e.title)
        }
        self.assertEqual(len(subsection_levels), 1)


def _SECTION_NO_TWO_PART(title: str) -> bool:
    import re

    return re.match(r"^\d+\.\d+\b", title) is not None


class _FakeOcrClient:
    """Returns canned transcripts in call order, like a page-by-page scan."""

    def __init__(self, transcripts):
        self._transcripts = transcripts
        self.calls = 0
        self.endpoint = "fake://ocr"

    def health(self):
        return True

    def transcribe(self, _png_bytes):
        transcript = (
            self._transcripts[self.calls] if self.calls < len(self._transcripts) else ""
        )
        self.calls += 1
        return transcript


def _fake_render(_pdf_path, _page, _dpi):
    return b""


@unittest.skipUnless(_HAS_FITZ, "PyMuPDF (optional [ocr] extra) not installed")
class OcrFallbackTocTests(unittest.TestCase):
    """Recovering the TOC from a scanned PDF by OCR-ing only the front matter."""

    def _blank_pdf(self, path: Path, pages: int) -> None:
        import fitz

        doc = fitz.open()
        for _ in range(pages):
            doc.new_page(width=612, height=792)
        doc.save(str(path))
        doc.close()

    def test_ocr_entries_parsing(self) -> None:
        from dokey.tocpage import _ocr_entries

        transcript = "Contents\n1 First Chapter 1\n1.1 Alpha 1\n1.2 Beta 3\n"
        entries = _ocr_entries(transcript)
        titles = {e.title: e for e in entries}
        self.assertEqual(titles["1.1 Alpha"].page, 1)
        self.assertEqual(titles["1.2 Beta"].page, 3)
        self.assertEqual(titles["1 First Chapter"].level, 0)
        self.assertEqual(titles["1.1 Alpha"].level, 1)

    def test_fallback_stops_once_toc_run_ends(self) -> None:
        from dokey.tocpage import read_page_toc

        transcripts = [
            "",  # page 1: front matter, not a TOC
            "",  # page 2: front matter, not a TOC
            "Contents\n1 First Chapter 1\n1.1 Alpha 1\n1.2 Beta 3\n"
            "2 Second Chapter 5\n2.1 Gamma 5\n",  # page 3: the contents page
            "Body text begins here.",  # page 4: not a TOC -> stop
        ]
        client = _FakeOcrClient(transcripts)
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "scanned.pdf"
            self._blank_pdf(pdf, pages=6)
            entries = read_page_toc(
                pdf, ocr_client=client, min_entries=3, render=_fake_render
            )
        by_title = {e.title: e for e in entries}
        self.assertIn("1.1 Alpha", by_title)
        self.assertEqual(by_title["1.1 Alpha"].parent, "1 First Chapter")
        self.assertEqual(by_title["2.1 Gamma"].parent, "2 Second Chapter")
        # OCR stopped after page 4 (front matter 1-2, TOC 3, then non-TOC 4),
        # never touching pages 5-6.
        self.assertEqual(client.calls, 4)

    def test_no_client_raises_helpful_error(self) -> None:
        from dokey.tocpage import read_page_toc

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "scanned.pdf"
            self._blank_pdf(pdf, pages=3)
            with self.assertRaises(ValueError):
                read_page_toc(pdf)  # no text layer, no OCR client


class ConverterSeamTests(unittest.TestCase):
    """Bring-your-own document conversion: command building, discovery, config.

    The converter runs out of process, so these tests never need it installed:
    they check the command dokey would run and how it reads what came back.
    """

    def command(self, **kwargs) -> list[str]:
        converter = convertlib.Converter(("docling",))
        return convertlib.build_command(
            converter, Path("in.pdf"), Path("out"), **kwargs
        )

    def test_ocr_is_off_and_figures_stay_out_by_default(self) -> None:
        command = self.command()
        self.assertIn("--no-ocr", command)
        self.assertNotIn("--ocr", command)
        # Embedded base64 was 99.7% of a measured render's characters.
        self.assertEqual(
            command[command.index("--image-export-mode") + 1], "placeholder"
        )
        self.assertEqual(command[command.index("--to") + 1], "md")

    def test_ocr_options_are_passed_only_when_ocr_is_on(self) -> None:
        on = self.command(ocr=True, ocr_engine="easyocr", ocr_lang="ko,en")
        self.assertIn("--ocr", on)
        self.assertEqual(on[on.index("--ocr-engine") + 1], "easyocr")
        self.assertEqual(on[on.index("--ocr-lang") + 1], "ko,en")
        off = self.command(ocr=False, ocr_engine="easyocr")
        self.assertNotIn("--ocr-engine", off)

    def test_the_default_ocr_engine_is_flagged_not_silently_used(self) -> None:
        # Measured: the default engine writes Hanja onto Korean scans.
        self.assertIsNotNone(convertlib.ocr_engine_caution(True, None))
        self.assertIsNone(convertlib.ocr_engine_caution(True, "easyocr"))
        self.assertIsNone(convertlib.ocr_engine_caution(False, None))

    def test_conversion_returns_the_file_named_after_the_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "out"
            source = Path(tmp) / "book.pdf"
            source.write_bytes(b"%PDF-1.4\n")

            def runner(command, **kwargs):
                work.mkdir(parents=True, exist_ok=True)
                (work / "book.md").write_text("# Book\n", encoding="utf-8")
                (work / "other.md").write_text("# Other\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            produced = convertlib.convert(
                source,
                convertlib.Converter(("docling",)),
                work_dir=work,
                runner=runner,
            )
            self.assertEqual([path.name for path in produced], ["book.md"])

    def test_a_failing_converter_reports_its_own_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.pdf"
            source.write_bytes(b"%PDF-1.4\n")

            def runner(command, **kwargs):
                return subprocess.CompletedProcess(command, 2, "", "no backend for pdf")

            with self.assertRaises(SystemExit) as caught:
                convertlib.convert(
                    source,
                    convertlib.Converter(("docling",)),
                    work_dir=Path(tmp) / "out",
                    runner=runner,
                )
            self.assertIn("no backend for pdf", str(caught.exception))

    def test_a_non_ascii_filename_is_staged_before_conversion(self) -> None:
        # Docling's parser cannot open a path with non-ASCII characters on
        # Windows -- a Korean filename dies with "Failed to load document" on a
        # file that reads fine -- so the seam hands it an ASCII stand-in.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "규정-비상사태관리.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            seen: dict[str, str] = {}

            def runner(command, **kwargs):
                given = Path(command[command.index("convert") + 1])
                seen["path"] = str(given)
                out = Path(command[command.index("--output") + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{given.stem}.md").write_text("# ok\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            produced = convertlib.convert(
                source,
                convertlib.Converter(("docling",)),
                work_dir=Path(tmp) / "out",
                runner=runner,
            )
            self.assertTrue(seen["path"].isascii(), seen["path"])
            self.assertEqual(produced[0].read_text(encoding="utf-8"), "# ok\n")

    def test_a_converted_file_comes_back_under_the_document_s_own_name(self) -> None:
        # The converter is handed an ASCII stand-in and names its output after
        # it. In this corpus the filename carries the date, the equipment tag
        # and the revision, so the workaround must not be what loses them.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "20240315_부서명_T-101_사건_rev1.2.pdf"
            source.write_bytes(b"%PDF-1.4\n")

            def runner(command, **kwargs):
                given = Path(command[command.index("convert") + 1])
                out = Path(command[command.index("--output") + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{given.stem}.md").write_text("# ok\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            produced = convertlib.convert(
                source,
                convertlib.Converter(("docling",)),
                work_dir=Path(tmp) / "out",
                runner=runner,
            )
            self.assertEqual(
                produced[0].name, "20240315_부서명_T-101_사건_rev1.2.md"
            )
            self.assertEqual(produced[0].read_text(encoding="utf-8"), "# ok\n")

    def test_converter_log_in_another_encoding_does_not_fail_the_run(self) -> None:
        # The child writes its log in the console codepage; decoding it
        # strictly would fail a conversion that already succeeded.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            captured: dict[str, object] = {}

            def runner(command, **kwargs):
                captured.update(kwargs)
                out = Path(command[command.index("--output") + 1])
                out.mkdir(parents=True, exist_ok=True)
                (out / "book.md").write_text("# ok\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            convertlib.convert(
                source,
                convertlib.Converter(("docling",)),
                work_dir=Path(tmp) / "out",
                runner=runner,
            )
            self.assertEqual(captured.get("errors"), "replace")

    def test_saved_converter_wins_over_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = str(Path(tmp) / "config")
            try:
                converter = convertlib.converter_from_command("mytool --flag")
                convertlib.save_converter(converter)
                resolved, source = convertlib.resolve_converter()
                self.assertEqual(resolved.argv, ("mytool", "--flag"))
                self.assertEqual(source, "config")
                convertlib.save_converter(None)
                self.assertIsNone(convertlib.load_converter())
            finally:
                if previous is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous

    def test_saved_ocr_choice_survives_changing_the_command(self) -> None:
        # `dokey auto` converts scans without asking, so the engine choice has
        # to be remembered -- and re-pointing the command must not drop it.
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = str(Path(tmp) / "config")
            try:
                main(
                    [
                        "convert",
                        "--set",
                        "docling",
                        "--ocr-engine",
                        "easyocr",
                        "--ocr-lang",
                        "ko,en",
                    ]
                )
                main(["convert", "--set", "C:/tools/docling.exe"])
                options = convertlib.load_options()
                self.assertEqual(options.ocr_engine, "easyocr")
                self.assertEqual(options.ocr_lang, "ko,en")
                self.assertEqual(
                    convertlib.load_converter().argv, ("C:/tools/docling.exe",)
                )
            finally:
                if previous is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous

    def test_both_formats_come_out_of_one_conversion_by_default(self) -> None:
        command = self.command(to=convertlib.DEFAULT_TARGETS)
        self.assertEqual(
            [command[i + 1] for i, part in enumerate(command) if part == "--to"],
            ["md", "json"],
        )

    def _stub_converter(self, tmp: Path) -> Path:
        """A stand-in converter: writes the formats it was asked for, no models."""
        script = tmp / "stub_converter.py"
        script.write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "argv = sys.argv[1:]\n"
            "source = Path(argv[1])\n"
            "targets = [argv[i + 1] for i, a in enumerate(argv) if a == '--to']\n"
            "out = Path(argv[argv.index('--output') + 1])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "for target in targets:\n"
            "    if target == 'md':\n"
            "        (out / (source.stem + '.md')).write_text(\n"
            "            '# Book\\n\\n## 1. Alpha\\n\\nBody text.\\n', encoding='utf-8')\n"
            "    else:\n"
            "        (out / (source.stem + '.json')).write_text(\n"
            "            json.dumps({'texts': []}), encoding='utf-8')\n",
            encoding="utf-8",
        )
        return script

    def test_converting_writes_the_render_and_stops_there(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            previous = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = str(tmp_path / "config")
            try:
                script = self._stub_converter(tmp_path)
                convertlib.save_converter(
                    convertlib.Converter((sys.executable, str(script)))
                )
                source = tmp_path / "book.pdf"
                source.write_bytes(b"%PDF-1.4\n")
                out = tmp_path / "converted"
                lake = tmp_path / "lake"

                main(["convert", str(source), "--output", str(out)])
                # Conversion is the product: both formats, and no lake unless
                # one was asked for.
                self.assertTrue((out / "book.md").exists())
                self.assertTrue((out / "book.json").exists())
                self.assertFalse(lake.exists())

                main(
                    [
                        "convert", str(source), "--output", str(out),
                        "--ingest", "--output-dir", str(lake),
                    ]
                )
                self.assertTrue((lake / "silver" / "sections.jsonl").exists())
            finally:
                if previous is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous

    def test_the_help_for_convert_renders(self) -> None:
        # argparse expands % in help text, so a measured figure written "99.7%"
        # is read as a format specifier and every option's help dies with it.
        import contextlib
        import io as _io

        buffer = _io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(buffer):
            main(["convert", "--help"])
        self.assertIn("--ingest", buffer.getvalue())

    def test_missing_converter_explains_how_to_get_one(self) -> None:
        hint = convertlib.install_hint()
        self.assertIn("dokey[docling]", hint)
        self.assertIn("dokey convert --set", hint)


class BackendTests(unittest.TestCase):
    """Bring-your-own OCR serving: endpoint normalization, discovery, config."""

    def test_chat_endpoint_normalization(self) -> None:
        expected = "http://127.0.0.1:1234/v1/chat/completions"
        self.assertEqual(backendslib.chat_endpoint("127.0.0.1:1234"), expected)
        self.assertEqual(backendslib.chat_endpoint("http://127.0.0.1:1234/"), expected)
        self.assertEqual(backendslib.chat_endpoint("http://127.0.0.1:1234/v1"), expected)
        self.assertEqual(backendslib.chat_endpoint(expected), expected)

    def test_probe_parses_the_model_list(self) -> None:
        def fetch(url, timeout):
            self.assertTrue(url.endswith("/v1/models"))
            return {"data": [{"id": "unlimited-ocr"}, {"id": "qwen3-4b"}]}

        backend = backendslib.probe("127.0.0.1:9999", fetch=fetch)
        self.assertEqual(backend.models, ("unlimited-ocr", "qwen3-4b"))
        self.assertTrue(backend.endpoint.endswith("/v1/chat/completions"))

    def test_probe_unreachable_returns_none(self) -> None:
        def fetch(url, timeout):
            raise OSError("connection refused")

        self.assertIsNone(backendslib.probe("127.0.0.1:9", fetch=fetch))

    def test_probe_tolerates_null_model_list(self) -> None:
        # Observed live: a server answering {"data": null} must not crash.
        backend = backendslib.probe(
            "127.0.0.1:9998", fetch=lambda url, timeout: {"data": None}
        )
        self.assertEqual(backend.models, ())

    def test_discover_collects_responding_ports(self) -> None:
        def prober(url, timeout):
            if url.endswith(":1234"):
                return backendslib.Backend(backendslib.chat_endpoint(url), ("vlm",))
            return None

        found = backendslib.discover(prober=prober)
        self.assertEqual(
            [item.endpoint for item in found],
            ["http://127.0.0.1:1234/v1/chat/completions"],
        )

    def test_config_roundtrip_and_resolution_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = tmp
            try:
                self.assertEqual(
                    backendslib.resolve_endpoint(None),
                    (ocrlib.DEFAULT_ENDPOINT, "default"),
                )
                backendslib.set_saved_endpoint("127.0.0.1:1234")
                self.assertEqual(
                    backendslib.resolve_endpoint(None),
                    ("http://127.0.0.1:1234/v1/chat/completions", "config"),
                )
                # An explicit flag wins over the saved config.
                self.assertEqual(
                    backendslib.resolve_endpoint("localhost:8089"),
                    ("http://localhost:8089/v1/chat/completions", "flag"),
                )
                backendslib.set_saved_endpoint(None)
                self.assertEqual(backendslib.resolve_endpoint(None)[1], "default")
            finally:
                if previous is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous

    def test_cli_backend_set_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = tmp
            try:
                main(["backend", "--set", "127.0.0.1:1234", "--no-discover"])
                self.assertEqual(
                    backendslib.saved_endpoint(),
                    "http://127.0.0.1:1234/v1/chat/completions",
                )
                main(["backend", "--clear", "--no-discover"])
                self.assertIsNone(backendslib.saved_endpoint())
            finally:
                if previous is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous


class BareLaunchTests(unittest.TestCase):
    """Double-clicking dokey.exe (no arguments) must launch, not usage-error."""

    def test_no_arguments_dispatches_to_the_default_launcher(self) -> None:
        from dokey import cli as cli_module

        calls = []
        original = cli_module.launch_default
        cli_module.launch_default = lambda: calls.append(True)
        try:
            main([])
        finally:
            cli_module.launch_default = original
        self.assertEqual(calls, [True])

    def test_workspace_dir_honors_config_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("DOKEY_CONFIG_DIR")
            os.environ["DOKEY_CONFIG_DIR"] = tmp
            try:
                self.assertEqual(
                    backendslib.workspace_dir(), Path.home() / "dokey"
                )
                backendslib.save_config({"workspace": str(Path(tmp) / "ws")})
                self.assertEqual(backendslib.workspace_dir(), Path(tmp) / "ws")
            finally:
                if previous is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous

    def test_bare_launch_moves_into_the_workspace(self) -> None:
        from dokey.cli import _ensure_workspace_cwd

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            previous_env = os.environ.get("DOKEY_CONFIG_DIR")
            previous_cwd = Path.cwd()
            os.environ["DOKEY_CONFIG_DIR"] = str(tmp_path / "cfg")
            try:
                backendslib.save_config({"workspace": str(tmp_path / "ws")})

                # Launched from a lake-less directory (the Scripts folder case).
                bare = tmp_path / "scripts"
                bare.mkdir()
                os.chdir(bare)
                self.assertEqual(_ensure_workspace_cwd(), tmp_path / "ws")
                self.assertEqual(Path.cwd(), tmp_path / "ws")

                # Launched from a real project directory: stay put.
                project = tmp_path / "project"
                write_search_lake(project / "dokey_out" / "book")
                os.chdir(project)
                self.assertEqual(_ensure_workspace_cwd(), project)
                self.assertEqual(Path.cwd(), project)
            finally:
                os.chdir(previous_cwd)
                if previous_env is None:
                    os.environ.pop("DOKEY_CONFIG_DIR", None)
                else:
                    os.environ["DOKEY_CONFIG_DIR"] = previous_env


class AppCommandTests(unittest.TestCase):
    @unittest.skipIf(
        importlib.util.find_spec("webview") is not None,
        "pywebview installed; running would open a desktop window",
    )
    @unittest.skipUnless(
        importlib.util.find_spec("streamlit") is not None, "streamlit not installed"
    )
    def test_missing_pywebview_reports_the_app_extra(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["app"])
        self.assertIn("pywebview", str(ctx.exception))


@unittest.skipUnless(_HAS_FITZ, "PyMuPDF (optional [ocr] extra) not installed")
class IngestTocFromPageOcrTests(unittest.TestCase):
    """The `ingest --toc-from-page` CLI wiring reaches the OCR fallback."""

    def _blank_pdf(self, path: Path, pages: int) -> None:
        import fitz

        doc = fitz.open()
        for _ in range(pages):
            doc.new_page(width=612, height=792)
        doc.save(str(path))
        doc.close()

    _TRANSCRIPTS = [
        "",  # page 1: front matter
        "",  # page 2: front matter
        "Contents\n1 First Chapter 1\n1.1 Alpha 1\n1.2 Beta 3\n"
        "2 Second Chapter 5\n2.1 Gamma 5\n",  # page 3: the contents page
        "Body text begins here.",  # page 4: stops the run
    ]

    def test_scanned_pdf_falls_back_to_ocr_through_cli(self) -> None:
        import dokey.ocr as ocrmod

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf = tmp_path / "scanned.pdf"
            self._blank_pdf(pdf, pages=6)
            output_dir = tmp_path / "lake"

            original = ocrmod.OcrClient
            ocrmod.OcrClient = lambda *a, **k: _FakeOcrClient(self._TRANSCRIPTS)
            try:
                main([
                    "ingest", "--input", str(pdf), "--toc-from-page",
                    "--output-dir", str(output_dir),
                    "--no-page-text", "--no-pdf-artifacts", "--section-overlap", "0",
                ])
            finally:
                ocrmod.OcrClient = original

            with (output_dir / "silver" / "sections.csv").open(encoding="utf-8-sig") as fh:
                titles = {row["title"] for row in csv.DictReader(fh)}
        self.assertIn("1.1 Alpha", titles)
        self.assertIn("2.1 Gamma", titles)

    def test_no_ocr_fallback_flag_disables_the_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf = tmp_path / "scanned.pdf"
            self._blank_pdf(pdf, pages=3)
            output_dir = tmp_path / "lake"
            with self.assertRaises(ValueError):
                main([
                    "ingest", "--input", str(pdf), "--toc-from-page",
                    "--no-ocr-fallback", "--output-dir", str(output_dir),
                ])


@unittest.skipUnless(_HAS_FITZ, "PyMuPDF (optional [ocr] extra) not installed")
class ProbeTests(unittest.TestCase):
    """Routing a PDF to the text or the OCR path."""

    def _pdf(self, path: Path, *, text_pages: int, image_pages: int) -> None:
        import fitz

        doc = fitz.open()
        for _ in range(text_pages):
            page = doc.new_page(width=612, height=792)
            for i in range(12):
                page.insert_text(
                    (72, 72 + i * 16),
                    "This page carries a real text layer with many "
                    "extractable words for the probe to measure.",
                    fontsize=11,
                )
        for _ in range(image_pages):
            page = doc.new_page(width=612, height=792)
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8))
            pix.clear_with(200)
            page.insert_image(fitz.Rect(0, 0, 600, 700), pixmap=pix)
        doc.save(str(path))
        doc.close()

    def test_text_document_routes_to_text(self) -> None:
        from dokey.detect import probe_pdf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "text.pdf"
            self._pdf(path, text_pages=3, image_pages=0)
            probe = probe_pdf(path)
        self.assertEqual(probe.method, "text")
        self.assertEqual(probe.scanned_pages, ())

    def test_scanned_document_routes_to_ocr(self) -> None:
        from dokey.detect import probe_pdf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scanned.pdf"
            self._pdf(path, text_pages=0, image_pages=3)
            probe = probe_pdf(path)
        self.assertEqual(probe.method, "ocr")
        self.assertEqual(probe.scanned_pages, (1, 2, 3))

    def test_mixed_document_flags_scanned_pages(self) -> None:
        from dokey.detect import probe_pdf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.pdf"
            self._pdf(path, text_pages=3, image_pages=1)
            probe = probe_pdf(path)
        # One scanned page in four keeps the document on the text route while
        # still surfacing the image page for section-level attention.
        self.assertEqual(probe.method, "text")
        self.assertIn(4, probe.scanned_pages)


class KoreanTypographyTocTests(unittest.TestCase):
    """Korean/CJK contents-page conventions: fused dot leaders and 제N장/절."""

    def test_fused_leader_and_page_number_token_splits(self) -> None:
        from dokey.tocpage import _entry_from_tokens

        entry = _entry_from_tokens(["제1절", "사업의", "개념·······················3"], 117.1)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.title, "제1절 사업의 개념")
        self.assertEqual(entry.page, 3)

    def test_fused_leader_requires_a_leader_run(self) -> None:
        from dokey.tocpage import _entry_from_tokens

        # A decimal inside a title ("물류4.0") must not split into a page number.
        self.assertIsNone(_entry_from_tokens(["스마트", "물류4.0"], 80.0))
        # A lone leader-and-number row has no title and is not an entry.
        self.assertIsNone(_entry_from_tokens(["·····12"], 80.0))

    def test_separate_trailing_number_still_parses(self) -> None:
        from dokey.tocpage import _entry_from_tokens

        entry = _entry_from_tokens(["1.1", "Alpha", "12"], 108.0)
        self.assertEqual(entry.title, "1.1 Alpha")
        self.assertEqual(entry.page, 12)

    def test_korean_structural_prefix_depth(self) -> None:
        from dokey.tocpage import _num_depth

        self.assertEqual(_num_depth("제1장 사업 개요"), 0)
        self.assertEqual(_num_depth("제 2 편 총론"), 0)
        self.assertEqual(_num_depth("제2절 추진배경 및 필요성"), 1)
        self.assertEqual(_num_depth("10.2 Something"), 1)
        # No trailing boundary: not a structural prefix.
        self.assertIsNone(_num_depth("제1장기계획"))
        self.assertIsNone(_num_depth("About the Author"))

    def test_slugify_keeps_korean_titles_readable(self) -> None:
        from dokey.names import slugify

        self.assertEqual(slugify("제1절 사업의 개념"), "제1절_사업의_개념")
        self.assertEqual(slugify("종합분석(PEST)"), "종합분석_PEST")
        self.assertEqual(slugify("Editor's Corner"), "Editor_s_Corner")


@unittest.skipUnless(_HAS_FITZ, "PyMuPDF (optional [ocr] extra) not installed")
class OffsetEstimateTests(unittest.TestCase):
    """Model-free --page-offset estimation from running folios."""

    def _folio_pdf(self, path: Path, *, pages: int, offset: int) -> None:
        import fitz

        # Front matter carries no folio; body pages print printed-page numbers
        # (pdf page - offset) in the footer, like a book's running folio.
        doc = fitz.open()
        for number in range(1, pages + 1):
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 72), "Body text without stray integers.", fontsize=11)
            folio = number - offset
            if folio >= 1:
                page.insert_text((300, 770), str(folio), fontsize=9)
        doc.save(str(path))
        doc.close()

    def test_constant_offset_is_recovered_with_confidence(self) -> None:
        from dokey.offset import estimate_page_offset

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "folio.pdf"
            self._folio_pdf(path, pages=12, offset=3)
            estimate = estimate_page_offset(path)
        self.assertEqual(estimate.offset, 3)
        self.assertTrue(estimate.confident)
        self.assertEqual(estimate.votes, estimate.sampled)

    def test_pdf_without_folios_yields_none(self) -> None:
        from dokey.offset import estimate_page_offset

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.pdf"
            self._folio_pdf(path, pages=6, offset=99)  # no folio ever printed
            estimate = estimate_page_offset(path)
        self.assertIsNone(estimate.offset)
        self.assertFalse(estimate.confident)


@unittest.skipUnless(_HAS_FITZ, "PyMuPDF (optional [ocr] extra) not installed")
class AutoCommandTests(unittest.TestCase):
    """`dokey auto`: one-shot TOC detection, offset estimation, ingest, index."""

    def _book_pdf(self, path: Path) -> None:
        import fitz

        # A 10-page book with no outline: cover, a printed contents page whose
        # rows fuse the dot leader into the title token (CJK-style), and body
        # pages that carry running folios (printed page = pdf page - 2) plus
        # the section heading on each section's first page.
        doc = fitz.open()
        cover = doc.new_page(width=612, height=792)
        cover.insert_text((72, 100), "A Test Book Without An Outline", fontsize=16)

        toc = doc.new_page(width=612, height=792)
        toc.insert_text((72, 40), "Contents", fontsize=14)
        rows = [
            (80, 72, "1 First Chapter·····1"),
            (100, 108, "1.1 Alpha·····1"),
            (120, 108, "1.2 Beta·····3"),
            (140, 72, "2 Second Chapter·····5"),
            (160, 108, "2.1 Gamma·····5"),
        ]
        for y, x, text in rows:
            toc.insert_text((x, y), text, fontsize=11)

        headings = {3: "1.1 Alpha", 5: "1.2 Beta", 7: "2.1 Gamma"}
        for number in range(3, 11):
            page = doc.new_page(width=612, height=792)
            heading = headings.get(number)
            if heading:
                page.insert_text((72, 72), heading, fontsize=13)
            page.insert_text((72, 100), "Section body text.", fontsize=11)
            page.insert_text((300, 770), str(number - 2), fontsize=9)
        doc.save(str(path))
        doc.close()

    def test_auto_ingests_and_indexes_without_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf = tmp_path / "book.pdf"
            self._book_pdf(pdf)
            lake = tmp_path / "lake"

            main(["auto", str(pdf), "--output-dir", str(lake)])

            rows = [
                json.loads(line)
                for line in (lake / "silver" / "sections.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertTrue((lake / "gold" / "search.db").exists())
        by_title = {row["title"]: row for row in rows}
        self.assertIn("1.1 Alpha", by_title)
        self.assertIn("2.1 Gamma", by_title)
        # Printed page 1 + estimated offset 2 = pdf page 3.
        self.assertEqual(by_title["1.1 Alpha"]["content_start_page"], 1)
        self.assertEqual(by_title["1.1 Alpha"]["pdf_start_page"], 3)
        self.assertEqual(by_title["2.1 Gamma"]["pdf_start_page"], 7)
        # Every section heading opens a fresh page here, so auto detects a
        # clean break and picks overlap 0: Alpha (pdf 3) ends the page before
        # Beta (pdf 5), with no shared boundary page.
        self.assertEqual(by_title["1.1 Alpha"]["pdf_end_page"], 4)

    def _book_pdf_with_stray_bookmark(self, path: Path) -> None:
        """The same book, plus the one bookmark an authoring tool left behind."""
        import fitz

        self._book_pdf(path)
        with fitz.open(str(path)) as doc:
            doc.set_toc([[1, "빈 페이지", 2]])
            doc.saveIncr()

    def test_a_stray_bookmark_does_not_pass_for_a_table_of_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf = tmp_path / "book.pdf"
            self._book_pdf_with_stray_bookmark(pdf)
            lake = tmp_path / "lake"

            main(["auto", str(pdf), "--output-dir", str(lake)])

            rows = [
                json.loads(line)
                for line in (lake / "silver" / "sections.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
        by_title = {row["title"]: row for row in rows}
        # One bookmark on page 2 of a ten-page book leaves the whole book in
        # one section, so the printed contents page is read instead -- and it
        # places the sections exactly where it does without the bookmark.
        self.assertNotIn("빈 페이지", by_title)
        self.assertIn("1.1 Alpha", by_title)
        self.assertEqual(by_title["1.1 Alpha"]["pdf_start_page"], 3)
        self.assertEqual(by_title["2.1 Gamma"]["pdf_start_page"], 7)

    def test_auto_page_offset_flag_overrides_the_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf = tmp_path / "book.pdf"
            self._book_pdf(pdf)
            lake = tmp_path / "lake"

            main(["auto", str(pdf), "--output-dir", str(lake), "--page-offset", "2"])

            rows = [
                json.loads(line)
                for line in (lake / "silver" / "sections.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
        by_title = {row["title"]: row for row in rows}
        self.assertEqual(by_title["1.1 Alpha"]["pdf_start_page"], 3)


class DocumentNameTests(unittest.TestCase):
    """The filename is metadata in this corpus; only its recognizable parts."""

    def test_it_reads_the_date_tag_and_revision_a_name_states(self) -> None:
        from dokey import docname

        read = docname.read(
            "20240315_부서명_T-101_설비명_사건_문서종류_rev1.2.xlsx"
        )
        self.assertEqual([item.value for item in read.dates], ["2024-03-15"])
        self.assertEqual([item.text for item in read.tags], ["T-101"])
        self.assertEqual(read.revision.value, "1.2")
        # Everything else is kept as written, in order, and not interpreted:
        # "부서명" is a department to a reader who knows the organization.
        self.assertEqual(read.tokens[1], "부서명")
        self.assertIn("사건", read.tokens)

    def test_a_date_glued_to_a_word_is_still_a_date(self) -> None:
        from dokey import docname

        read = docname.read("샘플문서 요약20240315.xlsx")
        self.assertEqual([item.value for item in read.dates], ["2024-03-15"])
        self.assertEqual(read.tokens, ["샘플문서", "요약20240315"])

    def test_eight_digits_that_are_not_a_date_are_not_claimed(self) -> None:
        from dokey import docname

        self.assertEqual(docname.read("도면 10120304 표지.pdf").dates, [])

    def test_a_document_number_is_not_an_equipment_tag(self) -> None:
        from dokey import docname

        # C-79-2015 is a KOSHA guide number wearing the shape of a tag; the
        # third dashed part is what separates them.
        self.assertEqual(docname.read("KOSHA GUIDE C-79-2015.pdf").tags, [])
        self.assertEqual(
            [item.text for item in docname.read("설비_HX-3001A_점검.xlsx").tags],
            ["HX-3001A"],
        )


class OutlineCoverageTests(unittest.TestCase):
    """An outline is asked to show that it divides the document."""

    @staticmethod
    def _entries(pages: list[int]) -> list:
        from dokey.models import TocEntry

        return [TocEntry(level=0, title=f"Entry {p}", page=p) for p in pages]

    def test_a_single_stray_bookmark_does_not_divide_the_document(self) -> None:
        from dokey.outline import divides_document, largest_share

        entries = self._entries([2])
        self.assertFalse(divides_document(entries, 210))
        self.assertGreater(largest_share(entries, 210), 0.9)

    def test_an_outline_that_covers_the_book_is_kept(self) -> None:
        from dokey.outline import divides_document

        self.assertTrue(divides_document(self._entries([1, 20, 40, 60, 80]), 100))

    def test_front_matter_before_the_first_entry_is_not_held_against_it(self) -> None:
        from dokey.outline import divides_document

        # An outline that starts at chapter 1, forty pages in, is doing its job.
        self.assertTrue(divides_document(self._entries([40, 60, 80]), 100))

    def test_folio_entries_are_measured_without_the_tail(self) -> None:
        from dokey.outline import divides_document

        # Printed folios say where sections start, not where the book ends, so
        # the distance from the last one to the last page is the page offset
        # rather than the size of that section.
        entries = self._entries([1, 3, 5])
        self.assertFalse(divides_document(entries, 10))
        self.assertTrue(divides_document(entries, 10, count_tail=False))


class MentionTests(unittest.TestCase):
    """Where a tag-shaped identifier occurs, addressed like a clause."""

    @staticmethod
    def _sections(*bodies: str) -> list:
        from dokey.mdunit import Section

        return [
            Section(order=index, level=1, title=f"{index}. 설비", parent="설비", body=body)
            for index, body in enumerate(bodies, start=1)
        ]

    def test_it_addresses_each_occurrence(self) -> None:
        from dokey import mentions

        found, report = mentions.find(
            self._sections("| T-101 | 저장탱크 | FRP |\n| P-201 | 이송펌프 |")
        )
        self.assertEqual([item.tag for item in found], ["T-101", "P-201"])
        self.assertEqual(found[0].section_index, 1)
        self.assertEqual(found[0].page, 1)
        self.assertIn("저장탱크", found[0].context)
        self.assertEqual(report.mentions, 2)

    def test_a_document_number_is_not_a_mention(self) -> None:
        from dokey import mentions

        # A standard prints its own number in the running header of every
        # page; counting those was the loudest false positive measured.
        found, _ = mentions.find(
            self._sections("KOSHA GUIDE M-181 - 2014 목재가공용 루터기"), "M-181-2014"
        )
        self.assertEqual(found, [])

    def test_the_filename_corroborates_what_the_text_says(self) -> None:
        from dokey import mentions

        found, _ = mentions.find(
            self._sections("T-101 파손 확인, P-201 정상"),
            "20240315_부서명_T-101_사건",
            ("T-101",),
        )
        by_tag = {item.tag: item for item in found}
        # The document is named for T-101 and mentions it: the name is not
        # what makes it a tank, but a consumer sorting equipment from alloy
        # grades will want the corroborated ones first.
        self.assertTrue(by_tag["T-101"].named)
        self.assertFalse(by_tag["P-201"].named)


class FigureCaptionTests(unittest.TestCase):
    """A caption names something other than itself: above it, or below it."""

    def _blocks(self, tmp: Path, document: dict) -> Path:
        path = tmp / "blocks.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _bbox(top: float, bottom: float) -> dict:
        # PDF space: the origin is bottom-left, so a larger t is higher up.
        return {"l": 100.0, "t": top, "r": 300.0, "b": bottom,
                "coord_origin": "BOTTOMLEFT"}

    def _document(self) -> dict:
        """Two figures, one captioned by the converter and one not.

        Both captions sit below their picture, which is what this document
        does; the second binding has to be induced from the first.
        """
        return {
            "pictures": [
                {
                    "self_ref": "#/pictures/0",
                    "prov": [{"page_no": 1, "bbox": self._bbox(700, 500)}],
                    "captions": [{"$ref": "#/texts/0"}],
                },
                {
                    "self_ref": "#/pictures/1",
                    "prov": [{"page_no": 2, "bbox": self._bbox(700, 500)}],
                },
            ],
            "tables": [],
            "texts": [
                {
                    "self_ref": "#/texts/0",
                    "label": "caption",
                    "text": "<그림 1> 파이프서포트",
                    "prov": [{"page_no": 1, "bbox": self._bbox(490, 480)}],
                },
                {
                    "self_ref": "#/texts/1",
                    "label": "caption",
                    "text": "<그림 2> 틀형 동바리",
                    "prov": [{"page_no": 2, "bbox": self._bbox(490, 480)}],
                },
            ],
        }

    def test_an_orphan_caption_is_bound_the_way_the_document_binds_the_rest(self) -> None:
        from dokey import figures

        with tempfile.TemporaryDirectory() as tmp:
            rows, report = figures.read_figures(
                self._blocks(Path(tmp), self._document())
            )
        by_ref = {row.ref: row for row in rows}
        self.assertEqual(by_ref["#/pictures/0"].basis, "converter")
        self.assertEqual(by_ref["#/pictures/1"].caption, "<그림 2> 틀형 동바리")
        self.assertEqual(by_ref["#/pictures/1"].basis, "induced")
        self.assertEqual(by_ref["#/pictures/1"].side, "below")
        self.assertEqual(report.conventions["picture"], "below")
        self.assertEqual(report.unbound_captions, 0)

    def test_a_caption_on_the_wrong_side_is_left_alone(self) -> None:
        from dokey import figures

        document = self._document()
        # Move the orphan caption above its picture: this document puts them
        # below, so it is not that picture's caption.
        document["texts"][1]["prov"][0]["bbox"] = self._bbox(760, 750)
        with tempfile.TemporaryDirectory() as tmp:
            rows, report = figures.read_figures(self._blocks(Path(tmp), document))
        by_ref = {row.ref: row for row in rows}
        self.assertIsNone(by_ref["#/pictures/1"].caption)
        self.assertEqual(report.unbound_objects, 1)
        self.assertEqual(report.unbound_captions, 1)

    def test_a_document_with_no_evidence_falls_back_to_the_corpus_prior(self) -> None:
        from dokey import figures

        document = self._document()
        document["pictures"][0].pop("captions")  # nothing bound to learn from
        with tempfile.TemporaryDirectory() as tmp:
            rows, report = figures.read_figures(self._blocks(Path(tmp), document))
        bases = {row.basis for row in rows if row.caption}
        self.assertEqual(bases, {"prior"})
        self.assertEqual(report.from_prior, 2)
        # A table's prior is the other way up, and is not a figure's.
        self.assertEqual(figures.CORPUS_PRIOR["table"], "above")

    def test_a_spreadsheet_grid_counts_the_other_way_up(self) -> None:
        from dokey import figures

        # A sheet's rows increase downwards, so "above" is the smaller t.
        upper = {"t": 1.0, "b": 2.0, "coord_origin": "TOPLEFT"}
        lower = {"t": 5.0, "b": 6.0, "coord_origin": "TOPLEFT"}
        side, _ = figures._side_and_gap(upper, lower)
        self.assertEqual(side, "above")

    def test_the_figure_is_placed_in_the_section_that_holds_it(self) -> None:
        from dokey import figures
        from dokey.mdunit import Section

        sections = [
            Section(order=1, level=1, title="1. 개요", parent="1. 개요", body=""),
            Section(order=2, level=1, title="2. 부재", parent="2. 부재", body=""),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = figures.read_figures(
                self._blocks(Path(tmp), self._document()),
                sections,
                [(1, 1), (2, 3)],
            )
        self.assertEqual(rows[0].section, "1. 개요")
        self.assertEqual(rows[1].section, "2. 부재")
        self.assertEqual(rows[1].section_index, 2)


class SpreadsheetTests(unittest.TestCase):
    """A workbook's unit is the sheet, and its title is the sheet's name."""

    def _blocks(self, tmp: Path) -> Path:
        """A block stream shaped like the converter's: tables tagged by sheet."""
        document = {
            "tables": [
                {
                    "prov": [{"page_no": 1}],
                    "data": {"grid": [
                        [{"text": "품목"}, {"text": "수량"}],
                        [{"text": "전기안전시험"}, {"text": "1"}],
                    ]},
                },
                {
                    "prov": [{"page_no": 2}],
                    "data": {"grid": [
                        [{"text": "태그"}, {"text": "설비명"}],
                        [{"text": "T-101"}, {"text": "저장탱크"}],
                    ]},
                },
            ],
            "texts": [],
        }
        path = tmp / "book.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def test_one_sheet_is_one_section_named_by_the_workbook(self) -> None:
        from dokey import sheets

        with tempfile.TemporaryDirectory() as tmp:
            sections, report = sheets.unitize(
                self._blocks(Path(tmp)), ["견적서", "설비목록"]
            )
        self.assertEqual([s.title for s in sections], ["견적서", "설비목록"])
        # The sheet's number is its page: sheet 2 really is page 2.
        self.assertEqual([s.order for s in sections], [1, 2])
        self.assertIn("T-101", sections[1].body)
        self.assertEqual(report.sheets, 2)
        self.assertEqual(report.named, 2)
        self.assertEqual(report.rows, 4)

    def test_sheets_are_numbered_when_the_workbook_will_not_say(self) -> None:
        from dokey import sheets

        with tempfile.TemporaryDirectory() as tmp:
            sections, report = sheets.unitize(self._blocks(Path(tmp)), [])
        self.assertEqual([s.title for s in sections], ["Sheet 1", "Sheet 2"])
        self.assertEqual(report.named, 0)
        self.assertTrue(report.notes)

    def test_a_cell_cannot_break_the_row_it_sits_in(self) -> None:
        from dokey import sheets

        table = sheets.markdown_table(
            [[{"text": "a|b"}, {"text": "two\nlines"}], [{"text": "1"}]]
        )
        rows = table.splitlines()
        self.assertEqual(rows[0], r"| a\|b | two lines |")
        # A short row is padded, not left ragged.
        self.assertEqual(rows[-1], "| 1 |  |")

    def test_the_workbook_names_its_own_sheets(self) -> None:
        from dokey import sheets

        try:
            from openpyxl import Workbook
        except ImportError:  # pragma: no cover - environment dependent
            self.skipTest("openpyxl not installed")
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / "book.xlsx"
            workbook = Workbook()
            workbook.active.title = "견적서"
            workbook.create_sheet("설비목록")
            workbook.save(book)
            self.assertEqual(sheets.sheet_names(book), ["견적서", "설비목록"])
            self.assertTrue(sheets.is_spreadsheet(book))

    def test_a_file_that_is_not_a_workbook_yields_no_names(self) -> None:
        from dokey import sheets

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not.xlsx"
            path.write_bytes(b"not a zip")
            self.assertEqual(sheets.sheet_names(path), [])


@unittest.skipUnless(_HAS_FITZ, "PyMuPDF (optional [ocr] extra) not installed")
class DriftSmokeTestTests(unittest.TestCase):
    """The per-section smoke test pins sections a drifting offset misplaces."""

    def _drifting_pdf(self, path: Path) -> None:
        import fitz

        # Cover, contents, printed pages 1-3 at pdf 3-5 (offset 2), then two
        # unnumbered plate pages, then printed 4-8 at pdf 8-12 (offset 4).
        # The folio vote is split 3 vs 5, so the constant-offset prior (4)
        # misplaces every section before the plates.
        doc = fitz.open()
        cover = doc.new_page(width=612, height=792)
        cover.insert_text((72, 100), "A Book Whose Offset Drifts", fontsize=16)

        toc = doc.new_page(width=612, height=792)
        toc.insert_text((72, 40), "Contents", fontsize=14)
        for y, x, text in [
            (80, 72, "1 First Chapter·····1"),
            (100, 108, "1.1 Alpha·····1"),
            (120, 108, "1.2 Beta·····3"),
            (140, 72, "2 Second Chapter·····5"),
            (160, 108, "2.1 Gamma·····5"),
        ]:
            toc.insert_text((x, y), text, fontsize=11)

        headings = {1: "1.1 Alpha", 3: "1.2 Beta", 5: "2.1 Gamma"}

        def body_page(printed: int) -> None:
            page = doc.new_page(width=612, height=792)
            heading = headings.get(printed)
            if heading:
                page.insert_text((72, 72), heading, fontsize=13)
            page.insert_text((72, 100), "Section body text.", fontsize=11)
            page.insert_text((300, 770), str(printed), fontsize=9)

        for printed in (1, 2, 3):
            body_page(printed)
        for _ in range(2):  # unnumbered plates: no folio, no headings
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 100), "Plate.", fontsize=11)
        for printed in (4, 5, 6, 7, 8):
            body_page(printed)
        doc.save(str(path))
        doc.close()

    def test_sections_across_the_drift_are_pinned_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf = tmp_path / "drift.pdf"
            self._drifting_pdf(pdf)
            lake = tmp_path / "lake"

            # Pin the overlap so this test isolates drift pinning from the
            # clean-break detection exercised elsewhere.
            main([
                "auto", str(pdf), "--output-dir", str(lake), "--section-overlap", "1",
            ])

            rows = [
                json.loads(line)
                for line in (lake / "silver" / "sections.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
        by_title = {row["title"]: row for row in rows}
        # Printed 1 and 3 sit before the plates (offset 2); printed 5 after
        # them (offset 4). No constant offset satisfies all three.
        self.assertEqual(by_title["1.1 Alpha"]["pdf_start_page"], 3)
        self.assertEqual(by_title["1.2 Beta"]["pdf_start_page"], 5)
        self.assertEqual(by_title["2.1 Gamma"]["pdf_start_page"], 9)
        # Ranges stay contiguous in the pdf domain across the drift.
        self.assertEqual(by_title["1.2 Beta"]["pdf_end_page"], 9)
        self.assertEqual(by_title["2.1 Gamma"]["pdf_end_page"], 12)

    def test_pin_section_starts_reports_statuses(self) -> None:
        from dokey.offset import pin_section_starts
        from dokey.tocpage import read_page_toc

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "drift.pdf"
            self._drifting_pdf(pdf)
            entries = read_page_toc(pdf)
            # A deliberately wrong constant prior: the smoke test must locate
            # and pin every section regardless.
            pinned, report = pin_section_starts(pdf, entries, 4)
        self.assertEqual(
            [entry.pdf_page for entry in pinned], [3, 5, 9]
        )
        self.assertEqual(report.verified + report.corrected, 3)
        self.assertEqual(report.unresolved, 0)

    def test_a_repeated_title_does_not_make_a_page_look_like_a_divider(self) -> None:
        from dokey.offset import _looks_like_divider

        # Three chapters opening on "1. 개요" put that title in the roster three
        # times. The page below holds one heading, not three, and counting the
        # roster's repeats pushed the section two pages past its own heading.
        self.assertFalse(
            _looks_like_divider(lambda page: "1.개요본문이이어진다", 5, ["1.개요"] * 3)
        )
        roster = ["1.개요", "2.컨트롤타워지정", "3.전문연구단운영"]
        self.assertTrue(_looks_like_divider(lambda page: "".join(roster), 5, roster))


@unittest.skipUnless(_HAS_FITZ, "PyMuPDF (optional [ocr] extra) not installed")
class CleanBreakDetectionTests(unittest.TestCase):
    """Reading section-break style off the page to choose section overlap."""

    def _live_page(self, lines):
        # A live in-memory page (no save/reopen — that locks the file on
        # Windows). ASCII text only: PyMuPDF's built-in font cannot draw CJK,
        # so a Korean string would extract empty. The geometry logic under
        # test is language-agnostic; the Korean path is validated end-to-end
        # on the real document.
        import fitz

        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        for y, text in lines:
            page.insert_text((72, y), text, fontsize=11)
        return doc, page

    def test_heading_at_top_is_a_clean_start(self) -> None:
        from dokey.offset import _is_clean_start

        # Running header (top band), a short chapter-title block, then the
        # heading — nothing body-length precedes it.
        doc, page = self._live_page([
            (30, "118 | Series Running Header"),
            (80, "1 First Chapter"),
            (110, "1.1 Alpha Section"),
            (150, "This is the section body which runs quite long indeed here."),
        ])
        try:
            self.assertTrue(_is_clean_start(page, "1.1 Alpha Section"))
        finally:
            doc.close()

    def test_heading_after_body_is_a_midpage_start(self) -> None:
        from dokey.offset import _is_clean_start

        # The previous section's body fills the top of the page; the next
        # heading begins partway down.
        body = "The previous section continues with a full line of prose here."
        doc, page = self._live_page([
            (30, "118 | Series Running Header"),
            (80, body),
            (110, body),
            (140, body),
            (170, "2.1 Gamma Section"),
            (200, body),
        ])
        try:
            self.assertFalse(_is_clean_start(page, "2.1 Gamma Section"))
        finally:
            doc.close()

    def test_recommended_overlap_from_report(self) -> None:
        from dokey.offset import SectionCheck, SmokeReport

        def report(flags):
            checks = tuple(
                SectionCheck(
                    title=f"s{i}",
                    printed_page=i,
                    predicted_pdf_page=i,
                    found_pdf_page=i,
                    clean_start=flag,
                )
                for i, flag in enumerate(flags)
            )
            return SmokeReport(checks=checks)

        # Mostly clean -> overlap 0.
        self.assertEqual(report([True, True, True, True]).recommended_overlap(), 0)
        # Mostly mid-page -> the safe overlap 1.
        self.assertEqual(report([False, False, False, True]).recommended_overlap(), 1)
        # One clean start below the 70% floor stays on overlap 1.
        self.assertEqual(report([True, True, False]).recommended_overlap(), 1)
        # Too few readable starts to tell -> None, overlap falls back to 1.
        self.assertIsNone(report([True, None]).clean_breaking)
        self.assertEqual(report([True, None]).recommended_overlap(), 1)


class PinnedRangeTests(unittest.TestCase):
    """build_ranges honors per-entry pdf pins over the constant offset."""

    def test_pinned_entries_define_pdf_ranges(self) -> None:
        entries = [
            TocEntry(level=1, title="Alpha", page=1, parent="One", pdf_page=3),
            TocEntry(level=1, title="Beta", page=3, parent="One", pdf_page=5),
            TocEntry(level=1, title="Gamma", page=5, parent="Two", pdf_page=9),
        ]
        ranges = build_ranges(
            entries=entries,
            output_dir=Path("out"),
            total_pdf_pages=12,
            pdf_page_offset=4,
            max_content_page=None,
            section_overlap=0,
        )
        self.assertEqual(
            [(r.pdf_start_page, r.pdf_end_page) for r in ranges],
            [(3, 4), (5, 8), (9, 12)],
        )
        # Printed content pages remain the TOC's own numbers.
        self.assertEqual(
            [(r.content_start_page, r.content_end_page) for r in ranges],
            [(1, 2), (3, 4), (5, 8)],
        )

    def test_unpinned_entries_keep_constant_offset_arithmetic(self) -> None:
        entries = [
            TocEntry(level=1, title="Alpha", page=1, parent="One"),
            TocEntry(level=1, title="Beta", page=3, parent="One"),
        ]
        ranges = build_ranges(
            entries=entries,
            output_dir=Path("out"),
            total_pdf_pages=10,
            pdf_page_offset=2,
            max_content_page=None,
            section_overlap=1,
        )
        self.assertEqual(
            [(r.pdf_start_page, r.pdf_end_page) for r in ranges],
            [(3, 5), (5, 10)],
        )


# A render in the shape the corpus actually has: every heading ``##``, the
# hierarchy carried by numbering, a running header between pages, the document
# title repeated as that header, and a sentence tail promoted to a heading.
KOSHA_MD = """\
KOSHA GUIDE

M - 165 - 2013

## 트랙터 안전 운전에 관한 기술지침

2013. 11.

## 안전보건기술지침의 개요

o 작성자 : 한국안전학회

KOSHA GUIDE

M - 165 - 2013

## 1. 목 적

이 지침은 트랙터의 안전 운전에 관한 사항을 기술한다.

## 2. 적용범위

이 지침은 농업에 적용한다.

KOSHA GUIDE

M - 165 - 2013

## 트랙터 안전 운전에 관한 기술지침

## 5. 운전석에 타고 내릴 때의 안전 확인사항

일반 사항.

## 5.1 타고 내릴 때

세 곳의 접촉을 유지한다.

## 5.2 안전정지

주차 브레이크를 채운다.

KOSHA GUIDE

M - 165 - 2013

## 11. 전복 방지

트랙터는 옆으로 넘어질 수 있다.

## 음을 유념한다.

경사면에서는 속도를 줄인다.
"""


class MarkdownUnitizeTests(unittest.TestCase):
    """Unitizing a laid-out document's Markdown render.

    The cases are the failures measured on 866 Docling renders of KOSHA
    technical standards: uniform heading levels, running headers surviving as
    body text, and prose fragments promoted to headings.
    """

    def sections(self, markdown: str, **kwargs):
        return mdunit.unitize(markdown, fallback_title="Doc", **kwargs)

    def test_uniform_levels_take_hierarchy_from_numbering(self) -> None:
        result = self.sections(KOSHA_MD)
        titles = [section.title for section in result.sections]
        self.assertEqual(
            titles,
            [
                "트랙터 안전 운전에 관한 기술지침",
                "안전보건기술지침의 개요",
                "1. 목 적",
                "2. 적용범위",
                "5. 운전석에 타고 내릴 때의 안전 확인사항",
                "11. 전복 방지",
            ],
        )
        self.assertTrue(result.report.derived_levels)
        self.assertEqual(result.report.max_level, 1)
        # 5.1 and 5.2 are inside clause 5, not sections of their own.
        clause_five = result.sections[4]
        self.assertIn("5.1 타고 내릴 때", clause_five.body)
        self.assertIn("세 곳의 접촉을 유지한다.", clause_five.body)
        self.assertEqual(result.report.subheadings_folded, 2)

    def test_running_header_is_dropped_from_every_section(self) -> None:
        result = self.sections(KOSHA_MD)
        for section in result.sections:
            self.assertNotIn("KOSHA GUIDE", section.body)
            self.assertNotIn("M - 165 - 2013", section.body)
        self.assertEqual(result.report.running_marks["KOSHA GUIDE"], 4)
        self.assertEqual(result.report.running_marks["M - 165 - 2013"], 4)

    def test_repeated_running_title_does_not_start_a_second_section(self) -> None:
        result = self.sections(KOSHA_MD)
        titles = [section.title for section in result.sections]
        self.assertEqual(titles.count("트랙터 안전 운전에 관한 기술지침"), 1)
        self.assertEqual(result.report.repeat_titles_demoted, 1)

    def test_prose_fragment_is_demoted_but_its_text_kept(self) -> None:
        result = self.sections(KOSHA_MD)
        last = result.sections[-1]
        self.assertEqual(last.title, "11. 전복 방지")
        self.assertIn("음을 유념한다.", last.body)
        self.assertEqual(result.report.fragments_demoted, 1)

    def test_heading_interrupting_a_numbered_run_is_demoted(self) -> None:
        # A document that writes 11.1 then 11.2 numbers everything at that
        # rung, so the unnumbered thing between them took no number -- and the
        # text before it stops mid-sentence, where the page break cut it.
        markdown = (
            "## 11. 전복 방지\n\n서문.\n\n"
            "## 11.1 주요 유의사항\n\n"
            "(2) 경사로를 무사히 올라갈 수 있었다 해도 보장은 없\n\n"
            "## 어렵다는 것에 주의\n\n"
            "(3) 브레이크를 사용하여야 한다.\n\n"
            "## 11.2 안전한 작업 시스템\n\n작업계획을 세운다.\n"
        )
        result = self.sections(markdown, max_level=3)
        titles = [section.title for section in result.sections]
        self.assertEqual(titles, ["11. 전복 방지", "11.1 주요 유의사항", "11.2 안전한 작업 시스템"])
        self.assertEqual(result.report.fragments_demoted, 1)
        self.assertIn("어렵다는 것에 주의", result.sections[1].body)

    def test_a_gap_in_the_numbering_blocks_the_demotion(self) -> None:
        # 11.1 -> 11.3 means a clause is missing; dokey must not reason as if
        # the run were complete, so the heading between them keeps its status.
        markdown = (
            "## 11. 전복 방지\n\n서문.\n\n"
            "## 11.1 주요 유의사항\n\n본문이 끊긴 채\n\n"
            "## 어렵다는 것에 주의\n\n설명.\n\n"
            "## 11.3 안전한 작업\n\n작업계획.\n"
        )
        result = self.sections(markdown, max_level=3)
        self.assertEqual(result.report.fragments_demoted, 0)
        self.assertIn("어렵다는 것에 주의", [s.title for s in result.sections])

    def test_a_finished_sentence_before_the_heading_blocks_the_demotion(self) -> None:
        # Interrupting the run is not enough on its own: a real unnumbered
        # heading follows text that ended properly.
        markdown = (
            "## 3. 분석\n\n서문.\n\n"
            "## 3.1 시료\n\n앞 문장은 여기서 끝난다.\n\n"
            "## 농도계산\n\n계산식.\n\n"
            "## 3.2 표준용액\n\n조제법.\n"
        )
        result = self.sections(markdown, max_level=3)
        self.assertEqual(result.report.fragments_demoted, 0)
        self.assertIn("농도계산", [s.title for s in result.sections])

    def test_caption_heading_is_never_read_as_prose(self) -> None:
        markdown = (
            "## 5. 표\n\n서문이 끊긴 채\n\n"
            "## 5.1 첫 표\n\n앞 문장이 끊긴\n\n"
            "## <표 3> 액체의 정전기 특성\n\n| a | b |\n\n"
            "## 5.2 다음 표\n\n본문.\n"
        )
        result = self.sections(markdown, max_level=3)
        self.assertEqual(result.report.fragments_demoted, 0)
        self.assertIn("<표 3> 액체의 정전기 특성", [s.title for s in result.sections])

    def test_split_title_is_rejoined_into_one_heading(self) -> None:
        # "## 1. 목" / "## 적" is one heading the page break cut in two.
        markdown = "## 1. 목\n\n## 적\n\n이 지침은 …을 목적으로 한다.\n\n## 2. 적용범위\n\n본문.\n"
        result = self.sections(markdown)
        self.assertEqual(
            [section.title for section in result.sections], ["1. 목적", "2. 적용범위"]
        )
        self.assertEqual(result.report.titles_rejoined, 1)
        self.assertNotIn("적\n", result.sections[0].body)

    def test_a_bare_annex_label_takes_the_caption_that_follows_it(self) -> None:
        # Measured shape: the label is one heading, the caption the next, then
        # the table. Without rejoining, the citable label is lost and only the
        # caption survives.
        markdown = (
            "## 1. 목 적\n\n본문.\n\n"
            "## <별표 1>\n\n"
            "## 소화기구의 소화약제별 적응성\n\n| 소화약제 | 가스 |\n|---|---|\n"
        )
        result = self.sections(markdown)
        self.assertIn(
            "<별표 1> 소화기구의 소화약제별 적응성",
            [section.title for section in result.sections],
        )
        self.assertEqual(result.report.titles_rejoined, 1)

    def test_an_annex_is_a_division_not_a_subheading(self) -> None:
        # 부록 / 별표 divide the document, so they sit at the clause's own rung
        # whether the document brackets them or not.
        markdown = (
            "## 1. 목 적\n\n본문.\n\n## <부록 1>\n\n## 1. 계산 목적\n\n계산 본문.\n"
        )
        result = self.sections(markdown)
        titles = [section.title for section in result.sections]
        self.assertIn("<부록 1>", titles)
        self.assertEqual([s.level for s in result.sections], [1, 1, 1])

    def test_a_table_caption_is_not_an_annex(self) -> None:
        korean = profileslib.resolve("ko")
        self.assertEqual(korean.numbering("<별표 1>").depth, 1)
        self.assertEqual(korean.numbering("[부록 2] 점검표").depth, 1)
        self.assertEqual(korean.numbering("<별표 3>").label, "<별표 3>")
        # A table or figure labels an object inside a division; it is not one.
        self.assertIsNone(korean.numbering("<표 2> 액체의 정전기 특성"))
        self.assertIsNone(korean.numbering("<그림 4>"))

    def test_rejoin_keeps_the_space_after_a_conjunction(self) -> None:
        markdown = "## 5.2.2 작업 관리 및\n\n## 감독\n\n본문.\n\n## 5.2.3 기록\n\n본문.\n"
        result = self.sections(markdown, max_level=3)
        self.assertEqual(result.sections[0].title, "5.2.2 작업 관리 및 감독")

    def test_letter_spaced_title_is_not_treated_as_truncated(self) -> None:
        # "한 국 산 업" ends on one syllable by typography, not truncation.
        markdown = "## 한 국 산 업 안 전\n\n발행 기관.\n\n## 개요\n\n본문.\n\n## 1. 목 적\n\n본문.\n"
        result = self.sections(markdown)
        self.assertIn("한 국 산 업 안 전", [s.title for s in result.sections])
        self.assertEqual(result.report.titles_rejoined, 0)

    def test_content_between_two_headings_blocks_the_rejoin(self) -> None:
        markdown = "## 3. 에반스 매듭\n\n매듭을 짓는다.\n\n## 매듭방법\n\n설명.\n"
        result = self.sections(markdown, max_level=2)
        self.assertEqual(result.sections[0].title, "3. 에반스 매듭")
        self.assertEqual(result.report.titles_rejoined, 0)

    def test_a_retypeset_document_title_is_demoted(self) -> None:
        # Measured in A-101-2018: the running title carries a typo the cover
        # title does not (기술지침 / 기술지칩), so exact repetition cannot see it.
        markdown = (
            "## 테트라하이드로퓨란에 대한 작업환경측정 분석 기술지침\n\n2018. 12.\n\n"
            "## 테트라하이드로퓨란에 대한 작업환경측정 분석 기술지칩\n\n"
            "## 1. 목 적\n\n본문.\n\n## 2. 적용범위\n\n본문.\n"
        )
        result = self.sections(markdown)
        titles = [section.title for section in result.sections]
        self.assertEqual(
            titles,
            [
                "테트라하이드로퓨란에 대한 작업환경측정 분석 기술지침",
                "1. 목 적",
                "2. 적용범위",
            ],
        )
        self.assertEqual(result.report.title_echoes_demoted, 1)

    def test_titles_that_differ_by_ordinal_are_not_echoes(self) -> None:
        # <부록 4> and <부록 1> are 90% alike and are two different appendices.
        markdown = (
            "## 지중경사계 설치방법 및 측정 예시 지침\n\n서문.\n\n"
            "## <부록 1> 지중경사계 설치방법, 측정방법 및 측정 예시\n\n내용.\n\n"
            "## <부록 4> 하중계 설치방법, 측정방법 및 측정 예시\n\n내용.\n"
        )
        result = self.sections(markdown)
        self.assertEqual(len(result.sections), 3)
        self.assertEqual(result.report.title_echoes_demoted, 0)

    def test_a_heading_with_nothing_under_it_is_not_a_section(self) -> None:
        markdown = (
            "## 터널공사 안전보건작업 지침\n\n2012. 5.\n\n"
            "## 한국산업안전보건공단\n\n"  # cover imprint: a title with no passage
            "## 1. 목 적\n\n본문.\n"
        )
        result = self.sections(markdown)
        self.assertNotIn("한국산업안전보건공단", [s.title for s in result.sections])
        self.assertEqual(result.report.empty_headings_demoted, 1)

    def test_a_numbered_divider_may_stand_without_a_body(self) -> None:
        # 제2장 heads a chapter whose first clause follows immediately; the
        # document's own numbering says it is a division, so it stays.
        markdown = "## 제2장 가우시안 모델\n\n## 1. 적용범위\n\n본문.\n"
        result = self.sections(markdown)
        self.assertIn("제2장 가우시안 모델", [s.title for s in result.sections])
        self.assertEqual(result.report.empty_headings_demoted, 0)

    def test_the_outline_mirrors_the_sections(self) -> None:
        result = self.sections(KOSHA_MD)
        self.assertEqual(
            [(entry.title, entry.page) for entry in result.outline],
            [(section.title, section.order) for section in result.sections],
        )
        self.assertEqual([entry.level for entry in result.outline][:2], [1, 1])

    def test_overpromoted_numbered_list_item_is_demoted(self) -> None:
        # A numbered heading that is a sentence is a list item the renderer
        # promoted; numbering protects it from the repeat rules, not from this.
        markdown = (
            "## 1. 목 적\n\n서문.\n\n"
            "## 2. 안전보건방침을 수행하기 위한 자원을 제공하여야 한다.\n\n본문.\n"
        )
        result = self.sections(markdown)
        self.assertEqual([s.title for s in result.sections], ["1. 목 적"])
        self.assertIn("자원을 제공하여야 한다.", result.sections[0].body)

    def test_document_own_levels_are_honored_when_they_vary(self) -> None:
        markdown = "# Guide\n\nIntro.\n\n## Setup\n\nSteps.\n\n### Detail\n\nMore.\n"
        result = self.sections(markdown)
        self.assertEqual(
            [(s.level, s.title) for s in result.sections],
            [(1, "Guide"), (2, "Setup"), (3, "Detail")],
        )
        self.assertFalse(result.report.derived_levels)
        self.assertIsNone(result.report.max_level)

    def test_clause_depth_means_the_same_unit_in_documents_that_differ(self) -> None:
        # One document heads its clauses at rung 1; the other puts an annex
        # above them, so its clauses sit a rung lower. "clause" picks the
        # clauses in both, where a fixed number would pick different things.
        plain = "## 1. 목 적\n\n본문.\n\n## 1.1 세부\n\n본문.\n\n## 2. 적용범위\n\n본문.\n"
        with_annex = (
            "## <부록 1>\n\n부록 서문.\n\n## 1. 목 적\n\n본문.\n\n"
            "## 1.1 세부\n\n본문.\n\n## 2. 적용범위\n\n본문.\n"
        )
        for markdown in (plain, with_annex):
            result = self.sections(markdown, max_level="clause")
            titles = [section.title for section in result.sections]
            self.assertIn("1. 목 적", titles)
            self.assertIn("2. 적용범위", titles)
            self.assertNotIn("1.1 세부", titles)

    def test_subclause_depth_goes_one_rung_further(self) -> None:
        markdown = "## 1. 목 적\n\n본문.\n\n## 1.1 세부\n\n본문.\n\n## 2. 적용범위\n\n본문.\n"
        result = self.sections(markdown, max_level="subclause")
        self.assertIn("1.1 세부", [section.title for section in result.sections])

    def test_an_unknown_depth_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.sections("## 1. 목 적\n\n본문.\n", max_level="deepest")

    def test_explicit_max_level_caps_the_document_own_levels(self) -> None:
        markdown = "# Guide\n\nIntro.\n\n## Setup\n\nSteps.\n\n### Detail\n\nMore.\n"
        result = self.sections(markdown, max_level=1)
        self.assertEqual([s.title for s in result.sections], ["Guide"])
        self.assertIn("## Setup", result.sections[0].body)

    def test_repeated_content_stays_when_it_does_not_span_the_document(self) -> None:
        # A checklist restates the same answer under every item. It repeats as
        # often as a running header but never leaves its one passage.
        markdown = (
            "## 1. 점검표\n\n"
            + "\n\n".join(
                f"({index}) 항목 {index}\n\n해당사항 없음" for index in range(1, 6)
            )
            + "\n\n"
            + "\n\n".join(f"본문 문단 {index}입니다." for index in range(40))
            + "\n\n## 2. 결론\n\n끝.\n"
        )
        result = self.sections(markdown)
        self.assertEqual(result.sections[0].body.count("해당사항 없음"), 5)

    def test_repeated_heading_keeps_its_text_when_it_is_never_body_text(self) -> None:
        # 농도계산 heads a passage under every analyte: a real repeated title,
        # not furniture. It stops being a section, but its text is not deleted.
        markdown = "".join(
            f"## {index}. 분석 대상 {index}\n\n서문.\n\n## 농도계산\n\n계산식 {index}.\n\n"
            for index in range(1, 5)
        )
        result = self.sections(markdown)
        self.assertEqual(len(result.sections), 4)
        self.assertEqual(result.report.running_mark_lines, 0)
        self.assertEqual(sum("농도계산" in s.body for s in result.sections), 4)

    def test_document_code_split_across_lines_is_absorbed(self) -> None:
        # A stretched running header arrives in pieces; the pieces are dropped
        # only because they keep the company of a mark, three times over.
        page_break = "D\n\n-\n\nC\n\n-\n\n10 - 2026\n\n"
        markdown = "## 1. 목 적\n\n" + "".join(
            f"{page_break}본문 {index}행입니다.\n\n" for index in range(4)
        )
        result = self.sections(markdown)
        body = result.sections[0].body
        for piece in ("D", "-", "C", "10 - 2026"):
            self.assertNotIn(f"\n{piece}\n", f"\n{body}\n")
        self.assertIn("본문 3행입니다.", body)

    def test_one_syllable_line_beside_prose_survives(self) -> None:
        # "것" is the tail of "~할 것" broken across a column, not furniture.
        markdown = "## 1. 기준\n\n" + "".join(
            f"③ 항목 {index}을 관리할\n\n것\n\n" for index in range(6)
        )
        result = self.sections(markdown)
        self.assertEqual(result.sections[0].body.count("것"), 6)

    def test_hash_inside_a_code_fence_is_not_a_heading(self) -> None:
        markdown = "# Title\n\n```sh\n# not a heading\necho hi\n```\n\nAfter.\n"
        result = self.sections(markdown)
        self.assertEqual([s.title for s in result.sections], ["Title"])
        self.assertIn("# not a heading", result.sections[0].body)

    def test_table_rows_are_not_headings(self) -> None:
        markdown = "## 1. 표\n\n| # | 항목 |\n|---|---|\n| 1 | 값 |\n"
        result = self.sections(markdown)
        self.assertEqual([s.title for s in result.sections], ["1. 표"])
        self.assertIn("| # | 항목 |", result.sections[0].body)

    def test_furniture_table_is_dropped(self) -> None:
        # Docling renders a columnar running header as a one-row table.
        page = "| KOSHA GUIDE   | KOSHA GUIDE   |\n|---------------|---------------|\n"
        markdown = "## 1. 목 적\n\n" + "".join(
            f"{page}\nKOSHA GUIDE\n\n본문 {index}.\n\n" for index in range(4)
        )
        result = self.sections(markdown)
        self.assertNotIn("KOSHA GUIDE", result.sections[0].body)
        self.assertEqual(result.report.furniture_tables_dropped, 4)

    def test_closing_hashes_are_not_part_of_the_title(self) -> None:
        result = self.sections("## Title ##\n\nBody.\n")
        self.assertEqual(result.sections[0].title, "Title")

    def test_indented_hash_is_a_code_block_not_a_heading(self) -> None:
        result = self.sections("# Title\n\n    # indented\n\nBody.\n")
        self.assertEqual([s.title for s in result.sections], ["Title"])

    def test_image_placeholder_stays_in_the_body_but_not_in_search_text(self) -> None:
        result = self.sections("## 1. 그림\n\n<!-- image -->\n\n설명.\n")
        section = result.sections[0]
        self.assertIn("<!-- image -->", section.body)
        self.assertNotIn("<!-- image -->", mdunit.section_page_text(section))
        self.assertIn("설명.", mdunit.section_page_text(section))

    def test_document_without_headings_becomes_one_section(self) -> None:
        result = self.sections("문단 하나.\n\n문단 둘.\n")
        self.assertEqual(len(result.sections), 1)
        self.assertEqual(result.sections[0].title, "Doc")
        self.assertTrue(result.report.notes)

    def test_empty_document_yields_nothing_and_says_so(self) -> None:
        result = self.sections("\n\n")
        self.assertEqual(result.sections, [])
        self.assertTrue(result.report.notes)

    def test_preamble_before_the_first_heading_is_kept(self) -> None:
        result = self.sections("서문 문단.\n\n## 1. 목 적\n\n본문.\n")
        self.assertEqual(result.sections[0].title, "Doc")
        self.assertIn("서문 문단.", result.sections[0].body)

    def test_profile_none_disables_the_korean_rules(self) -> None:
        markdown = "## 1. 목 적\n\n서문.\n\n## 음을 유념한다.\n\n본문.\n"
        korean = self.sections(markdown)
        self.assertEqual(korean.report.fragments_demoted, 1)
        # Without the Korean profile there is no sentence test, so the fragment
        # keeps heading status (depth still decides whether it opens a section).
        neutral = self.sections(markdown, profile="none")
        self.assertEqual(neutral.report.fragments_demoted, 0)
        self.assertIn(
            "음을 유념한다.", [s.title for s in self.sections(markdown, profile="none", max_level=2).sections]
        )


class ProfileTests(unittest.TestCase):
    """The numbering ladder and the prose test, per language profile."""

    def test_neutral_profile_reads_arabic_numbering(self) -> None:
        neutral = profileslib.resolve("none")
        self.assertEqual(neutral.numbering("5. Scope").depth, 1)
        self.assertEqual(neutral.numbering("5.1 Detail").depth, 2)
        self.assertEqual(neutral.numbering("5.1.2 Deeper").depth, 3)
        self.assertIsNone(neutral.numbering("Scope"))

    def test_measured_values_are_not_read_as_section_numbers(self) -> None:
        neutral = profileslib.resolve("none")
        # A section number counts from 1 and its rungs are short; a measurement
        # does neither, which is what keeps "0. 5 mm" off the ladder.
        self.assertIsNone(neutral.numbering("0. 5 mm"))
        self.assertIsNone(neutral.numbering("16.0.26 m3"))
        self.assertIsNone(neutral.numbering("2.135 kg"))

    def test_korean_ladder_depths(self) -> None:
        korean = profileslib.resolve("ko")
        self.assertEqual(korean.numbering("제1장 총칙").depth, 1)
        self.assertEqual(korean.numbering("4. 적용범위").depth, 1)
        self.assertEqual(korean.numbering("4.1 세부").depth, 2)
        self.assertEqual(korean.numbering("(1) 항목").depth, 3)
        self.assertEqual(korean.numbering("(가) 목").depth, 4)
        self.assertEqual(korean.numbering("① 세목").depth, 5)
        self.assertEqual(korean.numbering("부록 1").depth, 1)

    def test_dash_numbering_is_read_but_a_document_code_is_not(self) -> None:
        neutral = profileslib.resolve("none")
        self.assertEqual(neutral.numbering("5-4-2 거푸집 해체").depth, 3)
        # A document code and a range wear the same shape; both stay off the
        # ladder, the first because its rungs are four digits wide.
        self.assertIsNone(neutral.numbering("10 - 2026"))
        self.assertIsNone(neutral.numbering("1-2일 미만"))

    def test_cited_statute_sits_below_the_clause_citing_it(self) -> None:
        korean = profileslib.resolve("ko")
        self.assertEqual(korean.numbering("제1장 총칙").depth, 1)
        self.assertEqual(korean.numbering("제 2 절 안전").depth, 2)
        self.assertEqual(korean.numbering("제338조(굴착작업 사전조사 등)").depth, 3)

    def test_consecutive_numbering_is_recognized(self) -> None:
        korean = profileslib.resolve("ko")
        successor = profileslib.is_successor
        self.assertTrue(successor(korean.numbering("11.1 가"), korean.numbering("11.2 나")))
        self.assertFalse(successor(korean.numbering("11.1 가"), korean.numbering("11.3 다")))
        self.assertFalse(successor(korean.numbering("11.1 가"), korean.numbering("12. 라")))
        self.assertTrue(successor(korean.numbering("① 가"), korean.numbering("② 나")))
        self.assertFalse(successor(korean.numbering("5. 가"), None))

    def test_off_ladder_series_is_kept_and_marked(self) -> None:
        korean = profileslib.resolve("ko")
        self.assertTrue(korean.numbering("1) 항목").irregular)
        self.assertTrue(korean.numbering("가. 목").irregular)
        # A sentence that opens with a syllable outside 가나다 is not numbering.
        self.assertIsNone(korean.numbering("것. 이것은 문장"))

    def test_korean_prose_test(self) -> None:
        korean = profileslib.resolve("ko")
        self.assertTrue(korean.is_sentence_tail("음을 유념한다."))
        self.assertTrue(korean.is_sentence_tail("구성된다"))
        self.assertTrue(korean.is_sentence_tail(": 이러한 증상이 있으면"))
        self.assertFalse(korean.is_sentence_tail("안전보건기술지침의 개요"))
        self.assertFalse(korean.is_sentence_tail("트레일러를 수직으로 올릴 때"))
        self.assertFalse(korean.is_sentence_tail("용어의 정의"))

    def test_korean_key_ignores_justification_spacing(self) -> None:
        korean = profileslib.resolve("ko")
        self.assertEqual(korean.key("목  적"), korean.key("목적"))

    def test_profile_is_detected_from_the_text(self) -> None:
        self.assertEqual(profileslib.detect("이 지침은 트랙터에 적용한다."), "ko")
        self.assertEqual(profileslib.detect("This guide applies to tractors."), "none")


CLAUSE_BODY = """\
이 지침에서 사용되는 용어의 정의는 다음과 같다.

## 3.1 공통 용어

(1) 이 지침에서 사용되는 용어는 다음과 같다.

(가) '동력인출장치'라 함은 엔진에서 발생된 동력을 이용하는 장치를 말한다.

① 변속기 옆면에 설치된다.

(나) '견인봉'이라 함은 동력차와 동력을 갖지 않은 차를 연결하는 봉을 말한다.

(2) 그 밖의 용어는 관계 법령에 따른다.
"""


class SourceBlockPageTests(unittest.TestCase):
    """Pages taken from the stream a render came from, not invented."""

    def blocks(self, rows) -> list:
        return [
            blockslib.Block(page=page, label=label, layer=layer, text=text)
            for page, label, layer, text in rows
        ]

    SOURCE = [
        (1, "section_header", "body", "트랙터 안전 운전에 관한 기술지침"),
        (1, "text", "body", "2013. 11."),
        (2, "page_header", "furniture", "KOSHA GUIDE"),
        (2, "section_header", "body", "1. 목 적"),
        (2, "text", "body", "이 지침은 트랙터의 안전 운전을 다룬다."),
        (3, "text", "body", "KOSHA GUIDE"),  # the same mark, labelled body here
        (3, "text", "body", "계속되는 본문."),
        (4, "text", "body", "KOSHA GUIDE"),
        (4, "section_header", "body", "2. 적용범위"),
        (5, "text", "body", "이 지침은 농업에 적용한다."),
    ]

    def sections(self, titles):
        return [
            mdunit.Section(index, 1, title, title, "")
            for index, title in enumerate(titles, start=1)
        ]

    def test_a_section_takes_the_pages_it_occupies(self) -> None:
        report = blockslib.PageReport()
        ranges = blockslib.locate_sections(
            self.sections(["트랙터 안전 운전에 관한 기술지침", "1. 목 적", "2. 적용범위"]),
            self.blocks(self.SOURCE),
            report,
        )
        self.assertEqual(ranges, [(1, 1), (2, 3), (4, 5)])
        self.assertEqual(report.located, 3)
        self.assertEqual(report.interpolated, 0)
        self.assertEqual(report.pages, 5)

    def test_a_repaired_title_still_finds_its_block(self) -> None:
        # dokey rejoins "1. 목" + "적" into "1. 목적"; the block still says
        # "1. 목 적", and a title matching the start of a block is the block.
        ranges = blockslib.locate_sections(
            self.sections(["1. 목적"]), self.blocks(self.SOURCE)
        )
        self.assertEqual(ranges[0][0], 2)

    def test_a_section_that_cannot_be_found_is_counted_not_hidden(self) -> None:
        report = blockslib.PageReport()
        blockslib.locate_sections(
            self.sections(["1. 목 적", "없는 절 제목입니다"]),
            self.blocks(self.SOURCE),
            report,
        )
        self.assertEqual(report.interpolated, 1)
        self.assertTrue(report.notes)

    def test_page_text_drops_the_running_mark_whatever_its_label(self) -> None:
        # The converter called it furniture on page 2 and body on pages 3-4;
        # recurrence across the document settles it.
        rows = blockslib.page_texts(self.blocks(self.SOURCE))
        self.assertEqual([row["page"] for row in rows], [1, 2, 3, 4, 5])
        self.assertFalse(any("KOSHA GUIDE" in row["text"] for row in rows))
        self.assertIn("이 지침은 트랙터의", rows[1]["text"])

    def test_a_block_stream_beside_the_render_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            render = Path(tmp) / "doc.md"
            render.write_text("# x\n", encoding="utf-8")
            self.assertIsNone(blockslib.find_source_blocks(render))
            (Path(tmp) / "doc.json").write_text('{"texts": []}', encoding="utf-8")
            self.assertEqual(blockslib.find_source_blocks(render).name, "doc.json")


class TocSourceTests(unittest.TestCase):
    """One cascade, asked by the ingest and by the app's preview alike."""

    class _Reader:
        def __init__(self, pages, outline=()):
            self.pages = [_FakePage(text) for text in pages]
            self.outline = list(outline)

    BODY = [
        "목차\n1. 적용범위\n2. 목적\n3. 용어의 정의\n",
        "1. 적용범위\n\n이 규칙은 조직에 적용한다.\n",
        "2. 목적\n\n환경 측면을 파악함을 목적으로 한다.\n",
        "3. 용어의 정의\n\n용어는 다음과 같다.\n",
    ]

    def test_the_body_answers_when_no_contents_page_can_be_read(self) -> None:
        found = tocsource.resolve(
            self._Reader(self.BODY), Path("x.pdf"), allow_printed=False
        )
        self.assertEqual(found.source, "derived")
        self.assertTrue(found.physical_pages)
        self.assertEqual([entry.page for entry in found.entries], [2, 3, 4])

    def test_a_document_with_nothing_to_read_says_so(self) -> None:
        found = tocsource.resolve(
            self._Reader(["표지", "본문 문단입니다.", ""]),
            Path("x.pdf"),
            allow_printed=False,
        )
        self.assertFalse(found.found)
        self.assertEqual(found.source, "none")

    def test_ocr_is_never_reached_without_a_client(self) -> None:
        # A preview passes no client, and must come back rather than spend
        # minutes rendering the front matter through a model.
        called = []

        def refuse(*args, **kwargs):
            called.append(kwargs.get("ocr_client"))
            raise ValueError("no contents page")

        with unittest.mock.patch.object(tocsource, "read_page_toc", refuse):
            found = tocsource.resolve(
                self._Reader(["표지", "본문."]), Path("x.pdf"), ocr_client=None
            )
        self.assertEqual(found.source, "none")
        self.assertNotIn("client", [type(c).__name__ for c in called])

    def test_the_depth_asked_for_reaches_the_body_reader(self) -> None:
        pages = ["1. 적용범위\n\n본문.\n\n1.1 세부\n\n본문.\n\n2. 목적\n\n본문.\n"]
        shallow = tocsource.resolve(
            self._Reader(pages), Path("x.pdf"), max_level=1, allow_printed=False
        )
        deep = tocsource.resolve(
            self._Reader(pages), Path("x.pdf"), max_level=2, allow_printed=False
        )
        self.assertNotIn("1.1 세부", [entry.title for entry in shallow.entries])
        self.assertIn("1.1 세부", [entry.title for entry in deep.entries])


class BodyDerivedTocTests(unittest.TestCase):
    """Reading a table of contents off the body when the front matter has none.

    The shape that forces this is a contents page listing titles with no page
    numbers -- a reader manages, the title-and-page reader does not -- and it
    is common in corporate rules.
    """

    CONTENTS = "목차\n1. 적용범위\n2. 목적\n3. 용어의 정의\n4. 일반 사항\n"
    BODY_ONE = (
        "1. 적용범위\n\n이 규칙은 조직의 활동에 적용한다.\n\n"
        "2. 목적\n\n환경 측면을 파악하고 평가함을 목적으로 한다.\n\n"
        "3. 용어의 정의\n\n3.1 환경영향\n\n환경 변화를 말한다.\n"
    )
    BODY_TWO = "4. 일반 사항\n\n부서장은 다음을 따른다.\n\n1) 첫째 항목\n2) 둘째 항목\n"

    def test_headings_are_found_with_the_page_they_start_on(self) -> None:
        entries = bodytoc.derive_toc([self.CONTENTS, self.BODY_ONE, self.BODY_TWO])
        self.assertEqual(
            [(entry.title, entry.page) for entry in entries],
            [
                ("1. 적용범위", 2),
                ("2. 목적", 2),
                ("3. 용어의 정의", 2),
                ("4. 일반 사항", 3),
            ],
        )

    def test_the_contents_listing_itself_is_not_the_body(self) -> None:
        # Its entries come first and would put every section on page 1; a
        # listing is recognizable by its entries sitting on consecutive lines.
        entries = bodytoc.derive_toc([self.CONTENTS, self.BODY_ONE, self.BODY_TWO])
        self.assertNotIn(1, [entry.page for entry in entries])

    def test_an_unresolved_auto_list_does_not_swallow_the_clauses_around_it(
        self,
    ) -> None:
        # Measured: a list rendered as "0. 0. 0." sat between clauses 4 and 6,
        # and the run of it plus its neighbours read as a contents listing --
        # taking clauses 5 and 6 with it.
        body = (
            "4. 일반 사항\n\n다음을 고려한다.\n\n"
            "0. 첫째 고려사항\n0. 둘째 고려사항\n0. 셋째 고려사항\n\n"
            "5. 평가 기준 수립\n\n기준을 세운다.\n\n"
            "0. 심각도\n0. 가능성\n0. 발생빈도\n\n"
            "6. 평가 실시\n\n평가한다.\n"
        )
        contents = "목차\n1. 적용범위\n2. 목적\n3. 용어의 정의\n4. 일반 사항\n"
        entries = bodytoc.derive_toc([contents, body])
        self.assertEqual(
            [entry.title for entry in entries],
            ["4. 일반 사항", "5. 평가 기준 수립", "6. 평가 실시"],
        )
        self.assertTrue(all(entry.page == 2 for entry in entries))

    def test_a_numbered_sentence_is_not_a_heading(self) -> None:
        body = (
            "1. 적용범위\n\n본문.\n\n"
            "2. 이 규칙은 전과정의 관점에서 조직이 관리할 수 있는 환경측면에 적용한다.\n\n"
            "2. 목적\n\n본문.\n"
        )
        entries = bodytoc.derive_toc([body])
        self.assertEqual([entry.title for entry in entries], ["1. 적용범위", "2. 목적"])

    def test_depth_is_the_document_own_ladder(self) -> None:
        pages = [self.CONTENTS, self.BODY_ONE, self.BODY_TWO]
        deep = bodytoc.derive_toc(pages, max_level=2)
        self.assertIn("3.1 환경영향", [entry.title for entry in deep])
        self.assertEqual(
            [entry.level for entry in deep if entry.title == "3.1 환경영향"], [2]
        )
        shallow = bodytoc.derive_toc(pages, max_level=1)
        self.assertNotIn("3.1 환경영향", [entry.title for entry in shallow])

    def test_a_document_with_no_numbering_yields_nothing(self) -> None:
        self.assertEqual(bodytoc.derive_toc(["서문입니다.\n\n본문이 이어집니다.\n"]), [])


class LadderInductionTests(unittest.TestCase):
    """The nesting order is read from the document, not assumed.

    Measured across the 866-document corpus the fixed order was fitted to,
    1,241 of 9,159 observed series pairs contradict it -- 138 documents put
    circled numerals above (가), and about 200 nest an appendix inside the
    clauses. So the order has to come from the document.
    """

    def induce(self, text: str, profile: str = "ko"):
        return ladderlib.induce_from_lines(
            text.splitlines(), profileslib.resolve(profile)
        )

    def test_the_order_is_read_from_containment(self) -> None:
        # (1) brackets (가) here, and (가) brackets ①, often enough to believe.
        body = "\n".join(
            f"(1) 항목 {n}\n(가) 목 하나\n(나) 목 둘\n① 세목\n(2) 다음 항목 {n}\n"
            for n in range(1, 5)
        )
        ladder = self.induce(body)
        self.assertLess(ladder.rank["paren_num"], ladder.rank["paren_hangul"])
        self.assertLess(ladder.rank["paren_hangul"], ladder.rank["circled_num"])
        self.assertEqual(ladder.source["paren_hangul"], "observed")

    def test_a_document_that_inverts_the_convention_is_followed(self) -> None:
        # ① above (가) -- the inverse of the conventional ladder, and measured
        # in 138 documents of the corpus.
        body = "\n".join(
            f"① 세목 {n}\n(가) 목 하나\n(나) 목 둘\n② 다음 세목 {n}\n" for n in range(1, 5)
        )
        ladder = self.induce(body)
        self.assertLess(ladder.rank["circled_num"], ladder.rank["paren_hangul"])
        self.assertEqual(ladder.source["circled_num"], "observed")

    def test_the_prior_only_fills_what_the_document_does_not_say(self) -> None:
        ladder = self.induce("(1) 하나뿐인 항목입니다.\n(가) 목 하나뿐입니다.\n")
        self.assertLess(ladder.rank["paren_num"], ladder.rank["paren_hangul"])
        self.assertEqual(ladder.source["paren_num"], "prior")

    def test_a_decimal_keeps_its_own_arity(self) -> None:
        ladder = self.induce("1. 절\n1.1 소절\n1.1.2 더 깊은 소절\n")
        korean = profileslib.resolve("ko")
        self.assertEqual(ladder.depth(korean.numbering("1. 절")), 1)
        self.assertEqual(ladder.depth(korean.numbering("1.1 소절")), 2)
        self.assertEqual(ladder.depth(korean.numbering("1.1.2 더 깊은")), 3)

    def test_a_list_whose_numbers_never_advance_is_not_a_clause_series(self) -> None:
        # Auto-numbering that failed to resolve renders every item as "0."
        # (measured in a corporate regulation). The items are real; the
        # ordinals are not.
        body = (
            "1. 적용범위\n본문.\n2. 목적\n본문.\n3. 용어의 정의\n"
            "3.1 비상사태\n0. 중대재해\n0. 중대산업사고\n0. 화재/폭발\n0. 운송사고\n"
        )
        ladder = self.induce(body)
        korean = profileslib.resolve("ko")
        zero = korean.numbering("0. 중대재해")
        clause = korean.numbering("1. 적용범위")
        self.assertEqual(ladder.kind_of(zero), ladderlib.UNORDERED)
        self.assertEqual(ladder.kind_of(clause), "integer")
        self.assertGreater(ladder.depth(zero), ladder.depth(clause))

    def test_a_lone_clause_zero_is_left_alone(self) -> None:
        # A standard that opens with clause 0 (an introduction) means it.
        ladder = self.induce("0. 서론\n본문.\n1. 적용범위\n본문.\n2. 목적\n본문.\n")
        korean = profileslib.resolve("ko")
        self.assertEqual(ladder.kind_of(korean.numbering("0. 서론")), "integer")

    def test_a_contradictory_document_still_ranks_the_same_way_twice(self) -> None:
        # A series reused at two depths puts a cycle in the evidence, and the
        # cycle is broken at its weakest edge -- by count, then by name, so the
        # same document never ranks two different ways.
        body = "\n".join(
            f"(1) 항목 {n}\n(가) 목\n(2) 항목 {n}b\n(가) 목\n(나) 상위 목 {n}\n(1) 안쪽 항목\n(2) 안쪽 항목\n(다) 상위 목 {n}b\n"
            for n in range(1, 4)
        )
        first = self.induce(body)
        second = self.induce(body)
        self.assertEqual(first.order, second.order)
        self.assertEqual(first.rank, second.rank)

    def test_an_unordered_item_is_addressed_by_position(self) -> None:
        body = "0. 첫째 항목\n0. 둘째 항목\n0. 셋째 항목\n"
        korean = profileslib.resolve("ko")
        ladder = ladderlib.induce_from_lines(body.splitlines(), korean)
        items = pathslib.segment(body, root="3.1", profile=korean, ladder=ladder)
        self.assertEqual(
            [item.address for item in items],
            # an integer label is its digits, as everywhere else in an address
            ["3.1 0(1)", "3.1 0(2)", "3.1 0(3)"],
        )
        self.assertTrue(all(not item.ordered for item in items))


class AddressLadderTests(unittest.TestCase):
    """Cutting a section along the numbering ladder it addresses itself by."""

    def segment(self, body: str, root: str | None = "3.", profile: str = "ko"):
        report = pathslib.SegmentReport()
        items = pathslib.segment(
            body,
            root=root,
            profile=profileslib.resolve(profile),
            report=report,
        )
        return items, report

    def test_every_item_carries_its_full_address(self) -> None:
        items, _ = self.segment(CLAUSE_BODY)
        self.assertEqual(
            [item.address for item in items],
            [
                "3. 3.1",
                "3. 3.1 (1)",
                "3. 3.1 (1) (가)",
                "3. 3.1 (1) (가) ①",
                "3. 3.1 (1) (나)",
                "3. 3.1 (2)",
            ],
        )

    def test_a_rung_is_a_range_and_its_text_is_its_own(self) -> None:
        items, _ = self.segment(CLAUSE_BODY)
        by_address = {item.address: item for item in items}
        parent = by_address["3. 3.1 (1) (가)"]
        child = by_address["3. 3.1 (1) (가) ①"]
        # The child sits inside the parent's range...
        self.assertLess(parent.char_start, child.char_start)
        self.assertGreaterEqual(parent.char_end, child.char_end)
        # ...but the parent's own text stops where the child begins.
        self.assertNotIn("변속기 옆면", parent.text)
        self.assertIn("동력인출장치", parent.text)

    def test_offsets_hold_the_text_they_claim(self) -> None:
        # The verification gate: a span must be where the record says it is.
        items, _ = self.segment(CLAUSE_BODY)
        for item in items:
            self.assertEqual(
                CLAUSE_BODY[item.char_start : item.char_own_end].strip(), item.text
            )

    def test_a_skipped_rung_is_counted_not_invented(self) -> None:
        # This document does use (1), so jumping from 4.2 straight to (가)
        # skips a rung of its own ladder.
        body = (
            "## 4.1 개요\n\n(1) 첫 항목.\n\n(가) 세부 사항.\n\n(2) 둘째 항목.\n\n"
            "## 4.2 다음\n\n(가) 바로 목으로 간다.\n"
        )
        items, report = self.segment(body, root="4.")
        jumped = [item for item in items if item.address == "4. 4.2 (가)"]
        self.assertEqual(len(jumped), 1)
        self.assertEqual(jumped[0].skipped, 1)
        self.assertEqual(report.skipped_rungs, 1)

    def test_a_rung_the_document_never_uses_is_not_a_gap(self) -> None:
        # No (1) anywhere: (가) simply is the rung below 4.1 in this document,
        # and counting a gap against a ladder it does not follow would be
        # counting against a convention, not against the document.
        body = "## 4.1 개요\n\n(가) 첫 항목입니다.\n\n(나) 둘째 항목입니다.\n"
        items, report = self.segment(body, root="4.")
        self.assertEqual(
            [item.address for item in items],
            ["4. 4.1", "4. 4.1 (가)", "4. 4.1 (나)"],
        )
        self.assertEqual(report.skipped_rungs, 0)

    def test_an_off_ladder_series_is_kept_and_marked(self) -> None:
        body = "1) 첫째 항목.\n\n2) 둘째 항목.\n"
        items, report = self.segment(body, root="5.")
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item.irregular for item in items))
        self.assertEqual(report.irregular, 2)

    def test_text_before_the_first_enumerator_belongs_to_the_section(self) -> None:
        items, report = self.segment(CLAUSE_BODY)
        self.assertNotIn("용어의 정의는 다음과 같다", "".join(i.text for i in items))
        self.assertGreater(report.unaddressed_chars, 0)

    def test_a_measurement_is_not_an_address(self) -> None:
        body = "0. 5 mm 이하로 한다.\n\n2013. 11. 발행.\n\n16.0.26 m3 이다.\n"
        items, _ = self.segment(body, root="7.")
        self.assertEqual(items, [])

    def test_table_rows_are_not_enumerators(self) -> None:
        body = "| (1) | 값 |\n|---|---|\n| (2) | 값 |\n\n(1) 진짜 항목.\n"
        items, _ = self.segment(body, root="8.")
        self.assertEqual([item.address for item in items], ["8. (1)"])

    def test_a_folded_subheading_is_a_rung(self) -> None:
        # At depth 1 the decimal subheadings live in the body, marker and all;
        # they are still part of the address ladder.
        items, _ = self.segment(CLAUSE_BODY)
        self.assertEqual(items[0].address, "3. 3.1")
        self.assertEqual(items[0].depth, 2)


class ArtifactNamingTests(unittest.TestCase):
    """An artifact is named for its section and nothing else."""

    def paths(self, sections) -> list[str]:
        ranges = mdunit.build_section_ranges(sections, Path("lake"))
        return [
            str(Path(row.output_file).relative_to(Path("lake") / "artifacts" / "by_section"))
            for row in ranges
        ]

    def test_top_level_sections_get_no_folder_and_no_ordinal(self) -> None:
        sections = [
            mdunit.Section(1, 1, "1. 목적", "1. 목적", "본문"),
            mdunit.Section(2, 1, "2. 적용범위", "2. 적용범위", "본문"),
        ]
        self.assertEqual(self.paths(sections), ["1_목적.md", "2_적용범위.md"])

    def test_a_child_sits_in_a_folder_named_for_its_parent(self) -> None:
        sections = [
            mdunit.Section(1, 1, "11. 전복 방지", "11. 전복 방지", ""),
            mdunit.Section(2, 2, "11.1 주요 유의사항", "11. 전복 방지", ""),
        ]
        self.assertEqual(
            self.paths(sections),
            ["11_전복_방지.md", str(Path("11_전복_방지") / "11_1_주요_유의사항.md")],
        )

    def test_a_repeated_title_does_not_overwrite_the_first(self) -> None:
        # A compound document restarts its numbering, so "1. 목적" recurs.
        sections = [
            mdunit.Section(index, 1, "1. 목적", "1. 목적", "") for index in range(1, 4)
        ]
        self.assertEqual(
            self.paths(sections), ["1_목적.md", "1_목적_2.md", "1_목적_3.md"]
        )

    def test_split_pdf_names_follow_the_same_rule(self) -> None:
        entries = [
            TocEntry(level=1, title="Front Matter", page=1),
            TocEntry(level=2, title="Editor's Corner", page=1, parent="Front Matter"),
        ]
        ranges = build_ranges(
            entries=entries,
            output_dir=Path("lake"),
            total_pdf_pages=10,
            pdf_page_offset=0,
            max_content_page=None,
        )
        names = [
            str(Path(row.output_file).relative_to(Path("lake") / "artifacts" / "by_section"))
            for row in ranges
        ]
        self.assertEqual(
            names,
            ["Front_Matter.pdf", str(Path("Front_Matter") / "Editor_s_Corner.pdf")],
        )


class MarkdownIngestCliTests(unittest.TestCase):
    """`dokey auto <render>.md` end to end."""

    def test_ingest_writes_sections_and_a_unitize_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "M-165-2013.md"
            source.write_text(KOSHA_MD, encoding="utf-8")
            lake = Path(tmp) / "lake"
            main(["auto", str(source), "--output-dir", str(lake)])

            rows = [
                json.loads(line)
                for line in (lake / "silver" / "sections.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[2]["title"], "1. 목 적")

            outline = [
                json.loads(line)
                for line in (lake / "silver" / "toc.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(outline), len(rows))
            self.assertEqual(outline[2]["title"], "1. 목 적")
            self.assertEqual(outline[2]["page"], 3)

            report = json.loads(
                (lake / "bronze" / "md_ingest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["profile"], "ko")
            self.assertTrue(report["derived_levels"])
            self.assertEqual(report["max_level"], 1)
            self.assertTrue(
                any(mark["text"] == "KOSHA GUIDE" for mark in report["running_marks"])
            )

            pages = [
                json.loads(line)
                for line in (lake / "bronze" / "pages.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual([page["page"] for page in pages], [1, 2, 3, 4, 5, 6])
            self.assertNotIn("KOSHA GUIDE", pages[2]["text"])

    def test_max_level_flag_keeps_the_decimal_clauses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "M-165-2013.md"
            source.write_text(KOSHA_MD, encoding="utf-8")
            lake = Path(tmp) / "lake"
            main(
                [
                    "auto",
                    str(source),
                    "--output-dir",
                    str(lake),
                    "--outline-max-level",
                    "2",
                ]
            )
            titles = [
                json.loads(line)["title"]
                for line in (lake / "silver" / "sections.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertIn("5.1 타고 내릴 때", titles)
            self.assertIn("5. 운전석에 타고 내릴 때의 안전 확인사항", titles)

    def test_empty_render_reports_the_likely_cause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "scan.md"
            source.write_text("\n\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as caught:
                main(["auto", str(source), "--output-dir", str(Path(tmp) / "lake")])
            self.assertIn("no readable text", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
