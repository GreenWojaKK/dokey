# dokey

`dokey` is to document parsing what LM Studio is to local models. It ships its own reading engine and hosts the ones you bring — Docling, MarkItDown, `hwp2md`, any OpenAI-compatible OCR server — discovered on the machine rather than configured, run at arm's length as separate processes. Whatever engine reads the document, dokey parses the result into the sections a reader cites, with page ranges and addresses, and **records the evidence for every decision it makes**. Everything runs locally; no LLM is involved in the reading.

## Quick start

```powershell
python -m pip install -e .
dokey auto "C:\docs\report.pdf"
```

```text
report.pdf: 613 PDF pages
Route: TEXT (mean 923 chars/page, 6 scanned-looking pages)
TOC: 28 entries from the printed contents page(s)
Page offset prior: 9 (folio votes 83/83)
Smoke test: 28/28 section starts verified, 0 corrected, 0 interpolated
Section overlap: 0 (28/28 clean starts)
Ingested 28 sections from 28 TOC entries.
Search index: dokey_out\report\search.db (28 sections, 613 pages)
```

```powershell
dokey search "controller tuning" --lake dokey_out\report
dokey ui                            # browser UI; `dokey app` for a desktop window
```

## Engines

The built-in engine reads text-layer PDFs (pypdf) and workbooks (the file itself — an `.xlsx` is a zip of XML, and a legacy `.xls` goes through xlrd). Everything else is an engine you plug in:

| Input | Engine | Pages |
| --- | --- | --- |
| PDF with a text layer | built in (pypdf) | real |
| xlsx · xls | built in (native read, no converter) | one sheet = one page |
| Markdown / render | built in, unitized by heading | real, when the block JSON sits beside it |
| Scanned PDF | plug in: Docling | real, from the block stream |
| HWP / HWPX | plug in: `hwp2md` | synthetic, one per section |
| docx · pptx · html · epub | plug in: lightest found | synthetic — flow formats state no pages |

```powershell
pip install docling                  # found on PATH or in the interpreter — that's the whole setup
dokey convert --set "docling"        # …or pin one explicitly; any command works
dokey hwp --set "hwp2md"             # HWP engine
dokey backend --set 127.0.0.1:1234   # OCR endpoint (LM Studio, llama.cpp, Ollama, vLLM…)
```

Resolution is always **flag > saved setting > discovery**. Engines are registered by the evidence they keep, not by name: a paged source prefers an engine that keeps pages, a flow source loses nothing to a markdown-only one. Which engine opens which file is settled by running it — a refusal shows the tool's own last line, then the next one is tried. Each engine's output keeps its own name (`report-docling.md`, `dokey_out/report-docling`), so two readings of one document never overwrite each other. dokey imports none of them, and `pip install dokey` stays a pypdf-sized install.

## What a parse writes

A flat lake, every file at the root:

```text
lake/
  report.pdf                      # the source, under its own name
  pages.jsonl                     # page text
  toc.jsonl                       # the outline the parse worked from
  sections.csv / .json / .jsonl   # the section manifest — the contract
  items.jsonl                     # every numbered item, with its address
  ingest.json                     # what was read, dropped, and decided
  search.db                       # full-text index (SQLite FTS5, derived)
  by_section/                     # one PDF (and/or .md) per section
  media/                          # figures and pictures, where a source has them
```

Depending on the source, the lake also carries `figures.jsonl` (which caption names which figure), `mentions.jsonl` (where tag-shaped identifiers like `T-101` occur), `document.json` (what the filename states), `cells.jsonl` (every spreadsheet cell under its own reference), `objects.jsonl` and `sheet_figures.jsonl` (what a workbook hangs on its grid, and the drawings assembled from it — each redrawn as SVG from the file's own geometry).

## The evidence

Every judgement travels with its row — `basis` says whether the file stated a fact or dokey induced it, `header_basis` says what proved a table header, `converted_by` says which engine produced a render — and removals are counted in `ingest.json` rather than done silently. The decisions themselves are read from the document, printed as they are made, and overridable by flag:

- **TOC cascade** — the embedded outline if it actually divides the document, else the printed contents page read by word geometry, else the document's own numbered headings, else OCR. The source used is recorded in `toc.jsonl`.
- **Page offset** — most body pages carry their printed folio; each votes `offset = PDF page − folio` and the mode wins. Never asked of the user.
- **Per-section verification** — every predicted start page is read and searched for its own title; drift is pinned section by section, unmatched sections are interpolated and reported.
- **Per-boundary overlap** — a section that opens a fresh page shares nothing; a mid-page break keeps the shared page in both sections.
- **Numbering ladder** — which series encloses which (`1.` → `4.1` → `(1)` → `(가)` → `①`) is induced per document from containment; the convention is only a tie-breaker. Language profiles live in `dokey/profiles/`.

## Manual control

`dokey auto` is the front door; everything it decides can be pinned:

```text
dokey probe --input report.pdf        classify text vs scanned before parsing
dokey ingest --toc toc.csv ...        supply the TOC yourself (CSV, text, outline, printed page)
  --page-offset N                     override the folio vote
  --section-overlap N                 override the per-boundary decision
  --section-depth clause|subclause|N  how deep to split
dokey folios --lake ...               recover printed page numbers into the manifest
dokey index / dokey search            build and query the FTS5 index
```

## Development

```powershell
python -m unittest discover -s tests -v
```

MIT. The measurements behind each decision — corpus counts, failure cases, and the reasons a rule exists — live in the code's docstrings and the commit history.
