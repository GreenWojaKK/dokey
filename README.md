# Dokey

`dokey` is a small CLI for turning long book or booklet PDFs into document-lake friendly units.

The first target workflow is RAG ingestion: avoid sending a full 700-page PDF to an LLM, split it into section or subtopic units, and keep a manifest that preserves source page ranges and hierarchy.

## Outputs

An ingest run creates this shape:

```text
lake/
  raw/
    original.pdf
  bronze/
    pages.jsonl
  silver/
    toc.jsonl
    items.jsonl
    sections.csv
    sections.json
    sections.jsonl
  gold/
    search.db
  artifacts/
    by_section/
      Front_Matter.pdf            # a top-level section
      Front_Matter/               # …and its children, if it has any
        Editors_Corner.pdf
```

`silver/sections.*` is the main contract. PDF files under `artifacts/` are derived artifacts. `gold/search.db` is the derived full-text search index, created by `dokey index` or on first `dokey search`.

`silver/toc.jsonl` is the outline the ingest worked from — the embedded PDF
outline, the printed contents page, the document's own numbered headings, or
(for a render) those headings after the sweep described below. Every ingest
establishes it first and splits from it, so it is the record of how the
sections were decided.

The first source is checked before it is believed. A bookmark is metadata, and
metadata is not always about the document: one 210-page report's entire outline
is a single entry reading `빈 페이지` — "blank page" — pointing at page 2, left
behind by whoever made the file. Taken at its word it yields one section holding
the whole book. So an outline is asked to show that it divides the document, and
if its widest entry governs more than half the pages, dokey reads the printed
contents page too and prefers that when it has more entries and does divide. The
test is coverage, not vocabulary: no list of titles can be checked against the
words a document happens to use, but any table of contents can be checked
against the thing it claims to describe.

The third source exists because the second one fails on a shape that is
perfectly readable to a person: **a contents page that lists titles with no
page numbers.** A reader looking for title-and-page pairs finds no pairs and
reports no contents page, while the document is not short of structure at all —
its clauses are numbered, and each appears in the body where it starts. So
before giving up, dokey reads the headings off the body and takes the page each
was found on. Those pages are physical, like an outline's, so no page offset
applies and no smoke test is needed to place them.

Reading a contents page means reading a page laid out for a person, and three
things on it are not title-and-page pairs. A **division header** (`제1장 서론`)
carries no page number of its own, which made it read as the first line of a
wrapped title: it absorbed the clause below it, and the two were then dropped
together as a parent, so every chapter's opening clause went missing. It is now
an entry in its own right, taking the page of the first entry beneath it.
**Front matter** is folioed in Roman numerals (`요 약 ····· ⅴ`), a series that
shares no scale with the body's Arabic pages, so such a row is recognized in
order to be left out rather than glued onto the title below it. And the **list
of tables** after the contents is set in the same two columns under the same
running head; what separates the two is what the rows name — an object inside
the text (`<표 2-1>`) rather than a division of it.

`silver/items.jsonl` goes one level finer, for Markdown inputs. A section is
the unit a reader cites, but it is not the unit a document *addresses*: a
technical standard addresses a passage by a ladder of numbering series
(`4.1` → `(1)` → `(가)` → `①`), and anything reading the text for its content —
a definition harvest, a norm extractor, a knowledge graph — anchors on that
address, because the enumerator is the one boundary in the text that does not
depend on how the sentence is worded. Each row carries the full address, the
item's own words, and character offsets into the section body, so a consumer
can verify the words really sit where the address says. The rungs are *ranges*:
text under `(가)` is also inside the `(1)` above it, so `char_start`/`char_end`
bound the whole range and `char_own_end` bounds the item's own words. A rung
the document skips is counted, never invented. Turn the file off with
`--no-items`.

An artifact is named for its section and nothing else: order, page ranges, and
the section's ordinal are manifest fields, so repeating them in the path only
made it longer. Two sections that really do share a title — a compound document
restarts its numbering, so ``1. 목적`` can occur many times — are kept apart by
a ``_2``, ``_3`` suffix on the later ones.

## Install Locally

```powershell
python -m pip install -e .
```

## Quick Start: `dokey auto`

Not sure which flags a PDF needs? Point `auto` at it:

```powershell
dokey auto "C:\docs\스마트 철도물류4.0혁신기술개발.pdf"
```

`auto` recognizes the document's shape and runs the whole pipeline — probe,
TOC, offset, smoke test, overlap, ingest, index — printing each decision as
it goes:

```text
스마트 철도물류4.0혁신기술개발.pdf: 613 PDF pages
Route: TEXT (mean 923 chars/page, 6 scanned-looking pages)
TOC: 28 entries from the printed contents page(s)
Page offset prior: 9 (folio votes 83/83)
Smoke test: 28/28 section starts verified, 0 corrected, 0 interpolated
Section overlap: 0 (28/28 clean starts — sections start on fresh pages)
...
Ingested 28 sections from 28 TOC entries.
Search index: dokey_out\...\gold\search.db (28 sections, 613 pages)
```

The recognition is deliberately lexical — no LLM:

- **TOC source cascade**: the embedded PDF outline when one exists, else the
  book's own printed contents page(s) read by word geometry (with the OCR
  fallback for scanned books).
- **Page offset prior**: derived from the document itself, never required
  from the user. Most body pages carry their printed folio in the running
  header or footer; each one votes `offset = PDF page − folio` and the modal
  offset wins. A document with no text folios falls back to locating the
  first TOC titles in the body.
- **Per-section smoke test**: the prior is not trusted as-is. Every section's
  predicted start page is read and searched for the section's own title
  (exact, then a prefix, then the bare `제N절`/`10.2` marker at the page
  head, with typographic variants folded); a hit pins the section to its
  true physical page and updates the running offset, so an offset that
  drifts mid-book — plates, part dividers, unnumbered leaves — is corrected
  section by section. Chapter-divider pages listing several section titles
  are recognized and never count as a start. Unmatched sections are
  interpolated from their neighbors and reported, never silently misplaced.

- **Section overlap**: also read from the document, not defaulted blindly.
  The smoke test already visited every section's start page, so `auto` knows
  whether sections begin on a fresh page or mid-page. When they start on
  fresh pages (a clean break, as this report does), it picks overlap `0` — a
  shared boundary page would just duplicate the next section's first page.
  When breaks fall mid-page it keeps overlap `1` so neither section is
  truncated. A mixed or unclear document stays on the safe `1`.

`--page-offset N` overrides the prior (the smoke test still verifies it),
`--section-overlap N` overrides the detected overlap, and `--toc-page N`
pins the contents page. The lake lands in `dokey_out\<pdf name>` unless
`--output-dir` says otherwise. Everything below remains available for
manual control.

## Basic Usage

CSV TOC:

```powershell
dokey ingest `
  --input book.pdf `
  --toc examples\toc_csv_example.csv `
  --output-dir dokey_out\book `
  --page-offset 13
```

Text TOC:

```powershell
dokey ingest `
  --input book.pdf `
  --toc examples\toc_text_example.txt `
  --toc-format text `
  --output-dir dokey_out\book `
  --page-offset 0
```

PDF outline/bookmarks:

```powershell
dokey ingest `
  --input book.pdf `
  --toc-from-outline `
  --output-dir dokey_out\book `
  --page-offset 0
```

The book's own printed contents page (no outline, no TOC file needed):

```powershell
dokey ingest `
  --input book.pdf `
  --toc-from-page `
  --output-dir dokey_out\book `
  --page-offset 13
```

`--toc-from-page` reconstructs the TOC from the contents page(s) printed in the
PDF itself, by reading word geometry rather than the flat text stream: the title
on the left, the page number as the trailing token, and indentation depth giving
the level. It handles contents pages that use plain spacing instead of dot
leaders, a left margin that shifts between facing pages, and titles that wrap to
a second line. The contents page is found automatically; pin it with
`--toc-page N` (repeatable) if detection misses. Needs the optional `[ocr]`
extra (PyMuPDF): `python -m pip install -e .[ocr]`.

For a **scanned** PDF with no text layer, `--toc-from-page` falls back to OCR:
it transcribes the front matter page by page only until the contents page is
recognized, parses it, and stops — the body is never OCR'd and the transcripts
are discarded, since sectioning itself needs only the TOC page ranges. This
needs a local OCR endpoint (`--ocr-endpoint`, default the same as `folios`);
disable it with `--no-ocr-fallback`, or just supply `--toc` / `--toc-from-outline`
instead. The recovered sections are the scanned pages themselves, split by page
range; their text stays unavailable unless you OCR it separately.

Useful flags:

```text
--page-offset N        PDF page = TOC/content page + N
--max-content-page N   Stop at a content page boundary
--section-overlap N    Extend each section N pages into the next; default 1
                       (keeps a section complete when it shares a boundary
                       page with the next; use 0 for strict non-overlap)
--no-page-text         Skip bronze/pages.jsonl
--no-pdf-artifacts     Write only manifests, no split PDFs
--no-raw-copy          Do not copy source PDF under raw/
--toc-from-outline     Use PDF bookmarks/outline instead of a TOC file
--outline-max-level N  Deepest outline level to use; default is 1
```

## Bring-Your-Own OCR Serving

dokey ships no models. Every scanned-PDF feature (the `--toc-from-page` OCR
fallback, `folios --source ocr`) talks to an OpenAI-compatible chat endpoint
that **you** already run — LM Studio, a llama.cpp `llama-server` with a
vision-capable OCR GGUF and `--mmproj`, Ollama, vLLM. The model runtime stays
your reusable local infrastructure; dokey is a thin layer over it.

```powershell
dokey backend                      # show the effective endpoint + scan local ports
dokey backend --set 127.0.0.1:1234 # remember your LM Studio (host:port is enough)
dokey backend --clear              # back to the built-in default
```

The effective endpoint is resolved in a fixed order: an explicit
`--ocr-endpoint` / `--endpoint` flag, then the saved backend
(`~/.dokey/config.json`), then the built-in default
(`http://127.0.0.1:8731/v1/chat/completions`). The web UI has the same controls
under **🔌 OCR backend**, including one-click discovery of local servers.

## Markdown input

dokey accepts Markdown directly — hand it a `.md`/`.markdown` file and it skips
all PDF parsing, reading the text as-is and unitizing it by ATX heading:

```powershell
dokey auto "report.md"        # unitize by heading, index; no conversion
```

This is the fast lane when you already have text. If a PDF needs OCR or real
layout reconstruction (tables, multi-column, formulas), run a dedicated tool
**upstream** — Docling, Marker, Unstructured — and feed dokey the Markdown it
produced. dokey stays the section-unitizer and search layer; it does not try to
re-implement PDF layout reconstruction. Each heading becomes one section with a
synthetic page, so the manifest, index, and search behave exactly as elsewhere.

### Reading a render rather than a hand-written file

A render is a lossy view of a laid-out document, and reading one section per
`#` line produces sections that are not in the document. dokey handles the
three losses that show up in every renderer's output — measured on 866 Docling
renders of Korean technical standards:

- **Flattened hierarchy.** 852 of the 866 files use one heading level for
  everything, from the title down to clause `11.14`; the nesting lives in the
  *numbering*. When a file's heading levels are uniform, dokey derives levels
  from the numbering — but *which* numbering series encloses which is the
  document's own convention, not something dokey can assume. It is read off
  each document by counting containment: two consecutive `(1)`/`(2)` items
  bracket one item, so whatever appears between them is nested inside it. The
  conventional order (`1.` → `4.1` → `(1)` → `(가)` → `①`) is only a prior, used
  where a document says nothing; 85% of rungs across the corpus are decided by
  the document itself, and 37 of 850 documents order their series differently
  from the convention. `--outline-max-level` fixes the split depth; left unset,
  dokey descends the ladder until the sections are of citable size.
- **Page furniture as body text.** A running header is text on the page like
  any other, and it lands wherever the page broke — mid-paragraph, or split
  across five lines. dokey drops a short line that recurs three times or more
  *and reaches across the document*, which is what separates a page mark from
  repeated content (a checklist's "none applicable" repeats too, but stays in
  its one passage). No vocabulary is involved, so this is not tied to a
  publisher or a language.
- **Prose promoted to headings.** A sentence split by a page break can arrive
  as its own block labelled a header. Such a fragment is demoted back to prose:
  its text is kept, only its status as a section is refused. Two signals find
  them — a heading that *is* a sentence, and a heading that interrupts a run of
  consecutive numbers (`11.1`, then this, then `11.2`) while the text before it
  stops mid-sentence. Numbering only ever protects a heading; a heading is
  never refused merely for lacking a number, which was measured to cost more
  real sections than it removes false ones.
- **The same title, re-set.** A running title is typeset afresh on every page,
  so it comes back not identical but nearly so — different spacing, a different
  middle dot, or (measured in one document) the typo `기술지칩` against the cover's
  `기술지침`. Exact-match repetition is blind to all of these, so dokey sweeps the
  whole document first and compares every unnumbered title against the
  document's own title; the echoes stop being sections. Titles whose ordinals
  differ are never echoes, however alike the words: `<부록 4>` and `<부록 1>` are
  two appendices.
- **Headings with nothing under them.** A section is a passage; a title with no
  passage is a cover imprint, a running title standing at the top of a page, or
  the residue of a heading the layout broke. Unnumbered ones are demoted — a
  numbered divider (`제2장`) may legitimately stand alone before its first clause.
- **Titles cut in two.** The break can fall inside the heading itself:
  `## 1. 목` on one page, `## 적` on the next. When nothing stands between them,
  the first ends on a single syllable, and the second is short and unnumbered,
  the two are rejoined into `1. 목적` (150 such pairs in the measured corpus)
  rather than left as a truncated title and a stray line.

Every removal is recorded in `bronze/md_ingest.json` — counts, the marks
themselves, and the ingest's known defects — because dropping lines without a
record is indistinguishable from losing them.

```powershell
dokey auto "render.md"                          # detect the profile, split at depth 1
dokey auto "render.md" --outline-max-level 2    # keep 5.1, 5.2 … as sections
dokey auto "render.md" --profile none           # no language profile
```

The language-dependent parts — which numbering series exist, what a sentence
ending looks like — live in `dokey/profiles/`, one module per language, and are
selected from the text (`--profile auto`, the default). Korean (`ko`) ships
with the address ladder `절 → 4.1 → (1) → (가) → ① → ㉮`.

A render carries no page numbers of its own, and reconstructing them from the
running marks that survive in it was measured to work for 1 document in 866. So
dokey takes them from the stream the render came from: Docling writes
`prov[].page_no` on every block and leaves the JSON beside the Markdown, and
when it is there each section gets the pages it actually occupies. Measured on
the same corpus: 9,443 of 9,830 sections located in the block stream, 387
interpolated from their neighbour and reported as such, no section running past
the end of its document, and 48% of sections spanning more than one page —
where the synthetic numbering had claimed one page each, always. Without a
block stream the synthetic page remains, as a fallback rather than a claim.
Point at one explicitly with `--blocks`.

## Scanned and layout-heavy PDFs: `dokey convert`

dokey reads a PDF's text layer with pypdf and nothing else. A scan has no text
layer, so there is nothing to read — and a multi-column or table-heavy page has
text that comes out in the wrong order. Both need a layout converter, and dokey
runs one **you** install, at arm's length, exactly as it runs an HWP converter
or an OCR server:

```powershell
pip install dokey[docling]     # or: pip install docling — either is found
dokey convert                  # show the resolved converter
dokey convert "scan.pdf"       # convert; writes scan.md + scan.json here
dokey convert "scan.pdf" --ingest   # …and unitize it into a lake
dokey auto "scan.pdf"          # ingest, converting on the way if it has to
```

The `docling` extra is a convenience, not a coupling: dokey never imports
Docling, it invokes it as a separate process, and a `docling` already on PATH
works identically. `pip install dokey` stays a pypdf-sized install. **Nothing
has to be configured for it to be used**: dokey looks for the converter on
PATH and then in the interpreter running dokey, so a `pip install docling` is
the whole setup — from the CLI, from `dokey auto`, and from the web UI, which
says which converter it found before you add a book. `dokey convert --set` is
there for a converter that is somewhere else entirely, not as a step.

**Converting a document and taking it apart are separate acts**, so `dokey
convert` does the first and stops. It writes the render and the block stream
and prints the command that would unitize them. Conversion is the slow half —
minutes on a scanned book — and pinning it to a lake build means repeating it
whenever the unitizing is what you want to redo. `--ingest` asks for both in
one go.

Both formats come out by default because they come out of one parse: `--to md`
is the readable render, `--to json` the block stream that keeps page numbers
and bounding boxes, and dokey's own page recovery looks for the JSON beside
the Markdown it reads. Ask for one with `--to md` alone if the other is not
wanted.

Two defaults are set from measurement rather than left to the converter:

- **OCR is off unless asked.** A PDF with a text layer needs none, and Docling's
  default OCR engine is a Chinese PP-OCR model that writes Hanja onto Korean
  scans (54 such characters in one measured document). `dokey auto` turns OCR on
  only when the pages really are images, and says so; for Korean pass
  `--ocr-engine easyocr --ocr-lang ko,en`.
- **Figures are placeholders, not base64.** Docling embeds every figure by
  default: on three measured book pages that was 1,397,804 of 1,402,431
  characters. dokey asks for `placeholder`, so the figure's position is marked
  and its pixels stay out of the lake. `--images embedded` restores them.

`dokey auto` hands a PDF over only on the strong signal — at least half its
pages are images carrying no text. Sparse-but-real text stays on the pypdf path;
`--convert always` / `--convert never` override the judgement either way, and
the web UI offers the same three choices under **Advanced overrides**. When
`dokey auto` does convert, it takes both formats too, so the sections of a
scanned book get the pages the converter recorded rather than synthetic ones.

## Korean HWP / HWPX

dokey ingests Hancom word-processor files — `.hwp` (the binary v5 format) and
`.hwpx` (OWPML) — through the same **bring-your-own** seam as OCR: dokey ships
no HWP parser. It shells out to a converter **you** install, the reference one
being [`hwp2md`](https://github.com/hephaex/hwp2md), a Rust CLI that emits
Markdown with an ATX heading hierarchy.

```powershell
cargo install hwp2md          # native; dokey finds it on PATH
dokey hwp                     # show / auto-discover the converter
dokey auto "report.hwpx"      # ingest: convert, unitize by heading, index
```

dokey runs the converter only at **arm's length** — a separate process, talking
through files and CLI arguments — and bundles none of its code or binary. So
dokey stays MIT even though `hwp2md` is GPL-3.0; the converter is your own
separately-installed tool. Point dokey at any HWP→Markdown converter you prefer
(e.g. one built on the MIT `jw-hwp-core` or Apache-2.0 `hwp-rs`):

```powershell
dokey hwp --set "hwp2md"                                  # a command on PATH
dokey hwp --set "wsl.exe -e /home/you/.cargo/bin/hwp2md"  # an hwp2md inside WSL
```

If Rust lives only in your WSL install, dokey auto-discovers an `hwp2md` there
and translates Windows paths to `/mnt/...` for it — no native build needed.

Unlike a PDF, an HWP document has no intrinsic pages, so the unit is the
**heading**, not the page: each heading (`#`..`######`) becomes one section, its
parent is the nearest shallower heading, and its body is the text up to the next
heading. Each section is given one synthetic page so the manifest, index, and
full-text search all work exactly as for a PDF. Per-section artifacts are
Markdown files under `artifacts/by_section/` rather than split PDFs.

## Text vs Scanned PDFs

Not every PDF has a usable text layer. A publisher or digital-first PDF does;
a scanned booklet is a stack of page images that `page.extract_text()` returns
empty for, so ingesting it the normal way yields empty sections. `dokey probe`
classifies a PDF before you ingest it:

```powershell
dokey probe --input book.pdf
```

It measures the extractable text per page and whether each page is essentially
a full-page image, then reports the route:

- **TEXT** — extract the text layer; ingest normally.
- **OCR** — the document is scanned; recover printed pages with
  `dokey folios --source ocr` (a local OCR endpoint) and treat page text as
  unavailable until then.

A mostly-text document with a few scanned pages stays on the TEXT route but the
image pages are listed, so you know which sections may extract empty. Thresholds
are adjustable (`--min-mean-chars`, `--min-page-chars`, `--scan-ratio`). Needs
the optional `[ocr]` extra (PyMuPDF).

## Search

Build the index (SQLite FTS5, standard library only, stored at `gold/search.db`):

```powershell
dokey index --lake dokey_out\book
```

Search page text and section titles:

```powershell
dokey search "controller tuning" --lake dokey_out\book
dokey search "valve OR actuator" --lake dokey_out\book --limit 5
```

Notes:

- `--lake` may be omitted when exactly one lake exists under the current directory.
- The index is rebuilt automatically when `silver/sections.jsonl` or `bronze/pages.jsonl` changed; force with `--rebuild`.
- FTS5 query syntax (`AND`, `OR`, `NOT`, `"phrase"`, `term*`) is passed through; queries that fail to parse fall back to plain quoted terms.
- Section title matches are boosted above page-text matches.
- Lakes ingested with `--no-page-text` are searchable by section title only.

## Web UI

A local Streamlit UI over the same index (optional dependency):

```powershell
python -m pip install -e .[ui]
dokey ui --lake dokey_out\book
```

An empty query shows the section manifest for browsing (with recovered book
pages); results link to the split PDF artifacts. The sidebar's **➕ Add a
book** panel runs the whole pipeline from the browser, and defaults to
**Auto** — the same smart path as `dokey auto`: upload a PDF and add it, and
the TOC source, the page offset, and the section overlap are all read from
the document (no page offset to enter by hand). A wrong guess is correctable
under **Advanced overrides** without leaving Auto. Switch to **Manual** for
full control — pick the TOC source (PDF outline, an uploaded CSV/text TOC, or
the printed contents page, which falls back to the OCR backend for scanned
books) and set the offset and overlap yourself. Either way it ingests,
optionally recovers printed page numbers, builds the index, and opens the new
lake — no CLI needed. The **🔌 OCR backend** panel shows whether your local
serving is up and lets you pick a discovered server.

The sidebar language selector switches the full app UI between Korean and
English. The choice is saved in `~/.dokey/config.json` for the next launch.

## Desktop App

The same UI as a local desktop window (no browser tab, still 100% on-machine):

```powershell
python -m pip install -e .[app]
dokey app --lake dokey_out\book
```

`dokey app` starts the UI server headless on an unused port and opens it in a
native window (pywebview, WebView2 on Windows); closing the window stops the
server.

Deploy-friendly launch surfaces (no arguments needed):

- **`dokey.exe` double-clicked** (or `dokey` with no arguments) launches the
  app directly — the desktop window when pywebview is installed, otherwise the
  browser UI. No more usage error flashing in a closing console.
- **`dokey-app.exe`** is the windowed variant (a `gui-scripts` entry): no
  console window at all; startup failures surface as a message box.
- A bare launch has no meaningful working directory, so it works inside the
  user workspace `~/dokey` (override with the `workspace` key in
  `~/.dokey/config.json`); lakes you ingest from the app land there. Running
  `dokey` inside a project directory that already holds lakes keeps using
  that directory.

## Printed Page Numbers

A PDF page is rarely the book's printed page: front matter, part dividers, and
dropped blank leaves put the two out of step, and the offset drifts across the
book. When a lake is built from a PDF outline, `pdf_start_page` is the physical
page but the printed folio differs. `dokey folios` recovers the true printed
pages and adds `printed_start_page` / `printed_end_page` to the manifest, then
rebuilds the index so search shows real book pages.

```powershell
dokey folios --lake dokey_out\book
```

Two sources, selected with `--source` (default `auto`):

- `toc` — parse the PDF's own text Table of Contents (pypdf only, no GPU) and
  join it to the manifest by section number (`1.3`, `6.10`, `A.7`). Exact per
  section, so it handles a drifting offset automatically. Sections with no
  number (e.g. "About the Author", the index) are filled from the nearest
  matched section's offset; front matter is left unresolved.
- `ocr` — for scanned PDFs with no text TOC. Reads the printed folio off page
  images with a local vision OCR model served over an OpenAI-compatible endpoint
  (default `http://127.0.0.1:8731/v1/chat/completions`), e.g. a llama.cpp
  `llama-server` running an OCR GGUF with `--mmproj` on the GPU. By default it
  calibrates a piecewise offset model with a few dozen confirmed reads instead
  of OCR-ing every page; `--all-pages` OCRs every section boundary. Needs the
  optional extra: `python -m pip install -e .[ocr]`.

`auto` uses the text TOC when one is found and falls back to OCR otherwise. The
original manifest is backed up once to `silver/sections.prefolio.jsonl`.

## TOC Formats

CSV with explicit parent:

```csv
parent,title,page
Front Matter,Editor's Corner,1
Front Matter,Governance and Editorial Boards,2
```

CSV exported from a previous manifest also works if it has `content_start_page` instead of `page`.

Plain text TOC:

```text
* Part 1: Example Book 1
o Introduction 1
o Background 3
* Knowledge Area: Example Systems 6
o Example Systems 6
o Example System Concepts 9
Unbulleted Leaf Under Current Parent 12
```

Parent detection is intentionally conservative:

- `Part ...`
- `Knowledge Area: ...`
- headings ending in `Examples`, `Topics`, or `Research`

Everything else becomes a leaf under the current parent unless indentation or bullets indicate otherwise.

## Page Offset Examples

When content page 1 is PDF page 14, use `--page-offset 13`:

```powershell
dokey ingest `
  --input book.pdf `
  --toc toc.csv `
  --output-dir dokey_out\book `
  --page-offset 13
```

When TOC/content pages already match PDF pages, use `--page-offset 0`:

```powershell
dokey ingest `
  --input book-relative.pdf `
  --toc toc.csv `
  --output-dir dokey_out\book-relative `
  --page-offset 0
```

## Development

Run tests:

```powershell
python -m unittest discover -s tests -v
```

The current scope is a practical ingestion pipeline, not a full PDF layout model. Use Docling, Marker, Unstructured, or cloud document AI tools upstream when you need OCR or layout reconstruction, then feed their Markdown into `dokey auto <file.md>` (see [Markdown input](#markdown-input)).
