from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .. import backends as backendslib
from .. import folios as folioslib
from .. import ocr as ocrlib
from .. import search as searchlib
from ..manifest import write_manifest_rows
from .common import resolve_lake


def _find_raw_pdf(lake: Path) -> Path:
    # The source copy sits at the lake root; the split PDFs live under
    # by_section/, so a root glob finds only the document itself.
    pdfs = sorted(lake.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(
            f"No source PDF in {lake}. Pass --pdf, or re-ingest without --no-raw-copy."
        )
    return pdfs[0]


def _model_summary(model: ocrlib.OffsetModel) -> str:
    return "; ".join(
        f"pdf {segment.start_page}->offset {segment.offset}"
        for segment in model.segments
    )


def _results_from_model(
    model: ocrlib.OffsetModel, total_pages: int
) -> dict[int, "ocrlib.FolioResult"]:
    results = {}
    for page in range(1, total_pages + 1):
        folio = model.folio_at(page)
        source = "model" if folio is not None else "front-matter"
        results[page] = ocrlib.FolioResult(page, folio, source)
    return results


def _load_model(model_path: Path) -> ocrlib.OffsetModel:
    data = json.loads(model_path.read_text(encoding="utf-8"))
    segments = tuple(
        ocrlib.OffsetSegment(s["start_page"], s["offset"]) for s in data["segments"]
    )
    return ocrlib.OffsetModel(segments, data["first_page"], data["last_page"])


def _folios_calibrated(
    client, pdf_path, total_pages, args, folio_cache_path, model_path
):
    if model_path.exists() and not args.rebuild:
        model = _load_model(model_path)
        print(f"Reusing offset model {model_path}: {_model_summary(model)}")
    else:
        print(f"Calibrating offset model from {pdf_path.name} via {args.endpoint} ...")
        body = ocrlib.detect_body_start(
            client, pdf_path, total_pages, total_pages, args.dpi
        )
        if body is None:
            raise SystemExit(
                "Could not detect the first body page with an arabic folio. "
                "Pass --all-pages for exhaustive OCR instead."
            )
        body_start, body_folio = body
        print(
            f"  body starts at pdf {body_start} (printed {body_folio}, "
            f"offset {body_start - body_folio})"
        )

        def log(page, used, folio, offset):
            note = "" if used == page else f" (used pdf {used})"
            print(f"  probe pdf {page}{note} -> folio {folio}, offset {offset}")

        model = ocrlib.calibrate_offsets(
            client, pdf_path, body_start, total_pages,
            max_folio=total_pages, dpi=args.dpi, log=log,
        )
        print(f"  offset segments: {_model_summary(model)}")

        if args.verify > 0:
            span = model.last_page - model.first_page
            step = max(1, span // (args.verify + 1))
            samples = list(
                range(model.first_page + step, model.last_page, step)
            )[: args.verify]
            mismatches = ocrlib.verify_model(
                client, pdf_path, model, samples, max_folio=total_pages, dpi=args.dpi
            )
            if mismatches:
                print(f"  WARNING: {len(mismatches)} verification mismatch(es):")
                for page, predicted, actual in mismatches:
                    print(f"    pdf {page}: model {predicted} vs OCR {actual}")
            else:
                print(f"  verified {len(samples)} sample page(s); all consistent")

        model_path.write_text(
            json.dumps(model.to_dict(), indent=2), encoding="utf-8"
        )
        print(f"  wrote offset model: {model_path}")

    results = _results_from_model(model, total_pages)
    ocrlib.save_folio_map(folio_cache_path, results)
    return results


def _folios_exhaustive(client, pdf_path, rows, total_pages, args, folio_cache_path):
    needed = set()
    for row in rows:
        needed.add(int(row["pdf_start_page"]))
        needed.add(int(row["pdf_end_page"]))
    pages = sorted(needed)
    print(f"Exhaustive OCR: {len(pages)} boundary page(s) via {args.endpoint}")
    cache = {} if args.rebuild else ocrlib.load_cache(folio_cache_path)
    if cache:
        print(f"Reusing {len(cache)} cached folio(s)")
    done = 0

    def progress(page, folio, source):
        nonlocal done
        done += 1
        if done % 25 == 0 or done == len(pages):
            print(f"  {done}/{len(pages)} pages (last pdf {page} -> folio {folio})")

    results = ocrlib.build_folio_map(
        client, pdf_path, pages, max_folio=total_pages,
        dpi=args.dpi, cache=cache, progress=progress,
    )
    ocrlib.save_folio_map(folio_cache_path, results)
    return results


def _folios_via_ocr(lake, pdf_path, rows, args) -> None:
    endpoint, endpoint_source = backendslib.resolve_endpoint(args.endpoint)
    args.endpoint = endpoint  # downstream progress lines print the resolved URL
    print(f"OCR endpoint: {endpoint} ({endpoint_source})")
    client = ocrlib.OcrClient(endpoint)
    if not client.health():
        raise SystemExit(
            f"OCR endpoint not reachable at {endpoint} ({endpoint_source}).\n"
            "Start your local serving first (LM Studio, llama.cpp llama-server "
            "with an OCR GGUF and --mmproj, Ollama), or point dokey at a "
            "running one:\n"
            "  dokey backend            # discover local servers\n"
            "  dokey backend --set URL  # remember one"
        )
    total_pdf_pages = max(int(row["pdf_end_page"]) for row in rows)
    folio_cache_path = searchlib.index_path(lake).with_name("folios.jsonl")
    model_path = searchlib.index_path(lake).with_name("offset_model.json")
    started = time.monotonic()
    if args.all_pages:
        results = _folios_exhaustive(
            client, pdf_path, rows, total_pdf_pages, args, folio_cache_path
        )
    else:
        results = _folios_calibrated(
            client, pdf_path, total_pdf_pages, args, folio_cache_path, model_path
        )
    elapsed = time.monotonic() - started
    modeled = sum(1 for r in results.values() if r.source == "model")
    ocr_read = sum(1 for r in results.values() if r.source == "ocr")
    interpolated = sum(1 for r in results.values() if r.source == "interpolated")
    unresolved = sum(1 for r in results.values() if r.folio is None)
    print(
        f"Folio map: {ocr_read} OCR, {modeled} modeled, {interpolated} interpolated, "
        f"{unresolved} unresolved | {client.calls} OCR calls in {elapsed:.0f}s"
    )
    for row in rows:
        for key in ("printed_start_page", "printed_end_page", "folio_source"):
            row.pop(key, None)
        start = results.get(int(row["pdf_start_page"]))
        end = results.get(int(row["pdf_end_page"]))
        row["printed_start_page"] = start.folio if start else None
        row["printed_end_page"] = end.folio if end else None
        row["folio_source"] = start.source if start else "unresolved"


def run_folios(args: argparse.Namespace) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    lake = resolve_lake(args.lake)
    pdf_path = args.pdf or _find_raw_pdf(lake)

    rows = searchlib._read_jsonl(lake / "sections.jsonl")
    if not rows:
        raise SystemExit("Empty section manifest; run `dokey ingest` first.")

    used = None
    if args.source in ("auto", "toc"):
        toc_map, toc_pages = folioslib.build_toc_map(pdf_path)
        if toc_map and (len(toc_map) >= 10 or args.source == "toc"):
            print(
                f"TOC folios: {len(toc_map)} numbered entries from "
                f"pdf pages {toc_pages or '?'} of {pdf_path.name}"
            )
            stats = folioslib.apply_folios(rows, toc_map)
            print(
                f"  matched {stats.matched}, derived {stats.derived}, "
                f"front-matter {stats.front_matter} of {stats.total} sections "
                f"| offset range {stats.offset_min}..{stats.offset_max}"
            )
            used = "toc"
        elif args.source == "toc":
            raise SystemExit(
                "No usable text Table of Contents found. For a scanned PDF, "
                "use --source ocr."
            )
        else:
            print(
                f"TOC parse yielded {len(toc_map)} entries; falling back to OCR."
            )

    if used is None:
        _folios_via_ocr(lake, pdf_path, rows, args)

    backup = lake / "sections.prefolio.jsonl"
    if not backup.exists():
        backup.write_text(
            (lake / "sections.jsonl").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        print(f"Backed up original manifest: {backup}")

    write_manifest_rows(lake, rows)
    print("Updated manifest with printed_start_page / printed_end_page.")

    stats = searchlib.build_index(lake)
    print(
        f"Rebuilt index: {stats.db_path} "
        f"({stats.sections} sections, {stats.pages} pages)"
    )
