from __future__ import annotations

from collections.abc import Mapping


DEFAULT_LANGUAGE = "ko"
SUPPORTED_LANGUAGES = ("ko", "en")
LANGUAGE_LABELS = {
    "ko": "한국어",
    "en": "English",
}


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "ingest_book": "Add a book",
        "close_import": "Close",
        "ocr_backend": "OCR backend (bring your own)",
        "ingesting": "Ingesting {name} ...",
        "recovering_pages": "Recovering printed page numbers ...",
        "skipped_page_recovery": (
            "Skipped printed-page recovery ({error}); built the index only."
        ),
        "building_index": "Building search index ...",
        "ingest_failed": "Ingest failed: {error}",
        "ingest_error": "Ingest error: {error}",
        "ingested": "Ingested → {path}",
        "ingest_log": "Ingest log",
        "no_output": "(no output)",
        "online": "Online · {models}",
        "no_model_loaded": "no model loaded",
        "offline_ocr": (
            "Offline — scanned-PDF OCR is unavailable until a local server is up."
        ),
        "endpoint": "Endpoint (host:port or full URL)",
        "endpoint_placeholder": "e.g. 127.0.0.1:1234 (LM Studio)",
        "save": "Save",
        "detect": "Detect",
        "recheck": "Recheck",
        "recheck_help": (
            "Ask the server again. Its answer is otherwise remembered for half "
            "a minute so that every click does not wait on it."
        ),
        "no_backend_found": (
            "No OpenAI-compatible server found on well-known local ports."
        ),
        "use": "Use",
        "backend_caption": (
            "dokey ships no models — point it at the serving you already run "
            "(LM Studio, llama.cpp llama-server, Ollama)."
        ),
        "backend_source_flag": "command option",
        "backend_source_config": "saved setting",
        "backend_source_default": "default",
        "pdf": "PDF",
        "document_file": "PDF · HWP/HWPX · Markdown · Excel (xlsx)",
        "sheet_input_caption": (
            "A workbook is unitized by sheet: each sheet becomes one section, "
            "titled by its own sheet name, tables intact. None of the heading "
            "questions apply, so there is nothing else to set."
        ),
        "sheet_sections_caption": "Its sections will be its {count} sheet(s): {names}",
        "sheet_names_unreadable": (
            "The sheet names could not be read from this file; the sheets will "
            "be numbered."
        ),
        "sheet_converter_offline": (
            "No layout converter found — reading a workbook's tables needs one. "
            "`pip install docling` and dokey runs it from here, no setup."
        ),
        "sheet_native_caption": (
            "Read directly from the file — cells, merges, charts and images "
            "with their cell anchors. No converter involved."
        ),
        "flow_input_caption": (
            "A flow document: converted to Markdown, then unitized by heading. "
            "The source states no pages — pagination is a rendering artifact — "
            "so a markdown-only converter loses nothing structural."
        ),
        "flow_converter_choice": "Converter",
        "flow_converter_choice_help": (
            "Which tool converts this document, for this run only. A "
            "markdown-only converter is fully adequate here — the source "
            "states no pages — and is usually much faster; one that also "
            "keeps a block stream can only know more."
        ),
        "flow_converter_offline": (
            "No converter found for this format. The light option is enough: "
            "`pip install markitdown[docx,pptx]` — or `pip install docling`, "
            "which also keeps page evidence for PDFs."
        ),
        "sheet_legacy_caption": (
            "A legacy .xls workbook: read directly, no converter involved."
        ),
        "sheet_xlrd_offline": (
            "Reading a legacy .xls needs xlrd (`pip install xlrd`) — or save "
            "the workbook as .xlsx and add that instead."
        ),
        "choose_document": "Choose a document",
        "change_document": "Choose another document",
        "clear_document": "Clear",
        "choose_document_title": "Choose a book to add",
        "selected_document": "Selected · {path}",
        "document_missing": "The selected file is no longer available.",
        "md_input_caption": (
            "Markdown is ingested as-is and unitized by heading (each # section "
            "becomes a unit) — no conversion. Ideal for a Docling/Marker render."
        ),
        "section_depth": "Split into sections at",
        "section_depth_auto": "Auto — until sections are of citable size",
        "section_depth_clause": "Clause (5.)",
        "section_depth_subclause": "Subclause (5.1)",
        "section_depth_help": (
            "Clause and subclause are read from each document's own numbering, "
            "so they pick the same kind of unit across documents whose ladders "
            "differ. Auto decides per document, which reads well on its own and "
            "does not compare between documents."
        ),
        "language_profile": "Numbering conventions",
        "language_profile_auto": "Detect from the text",
        "language_profile_none": "None (numerals only)",
        "language_profile_ko": "Korean",
        "language_profile_help": (
            "Which enumerator series dokey knows how to read -- (가), ①, 제2장 "
            "-- and what a finished sentence looks like, which is how a heading "
            "is told from a fragment. Detected from the text by default."
        ),
        "source_blocks": "Source blocks (optional)",
        "source_blocks_help": (
            "The converter's JSON for this render (DoclingDocument). With it, "
            "sections take the pages they occupy in the original document "
            "instead of one each. A .json sitting beside the file is found on "
            "its own; upload one here when it is somewhere else."
        ),
        "write_items": "Also address every item (4.1 (1) (가))",
        "write_items_help": (
            "Writes silver/items.jsonl: each numbered item of each section with "
            "its full address and offsets. Useful for extraction; skip it to "
            "keep the library small."
        ),
        "preview_toc": "Preview the table of contents",
        "preview_toc_help": (
            "Read the document and show the sections this setting would make, "
            "before anything is written. Nothing is extracted until you add it."
        ),
        "preview_reading": "Reading the document ...",
        "preview_source": "Table of contents from {source} — {count} entries, split at depth {depth}",
        "preview_ladder": "Numbering read as: {ladder}",
        "preview_empty": (
            "No table of contents found in this document. Extraction would have "
            "nothing to split on."
        ),
        "preview_failed": "Could not read this document: {error}",
        "preview_not_extracted": (
            "Nothing has been extracted yet — add the document to keep it."
        ),
        "preview_title": "Section",
        "preview_level": "Depth",
        "preview_pages": "Pages",
        "preview_chars": "Characters",
        "hwp_online": "HWP converter: {cmd}",
        "hwp_offline": (
            "No HWP converter found. Install hwp2md, or set one from a terminal: "
            "`dokey hwp --set \"...\"`."
        ),
        "converter_online": "Layout converter: {cmd} ({source})",
        "converter_offline": (
            "No layout converter found. Scanned pages have no text to read and "
            "would index empty; `pip install docling` and dokey runs it from "
            "here, no setup."
        ),
        "converter_source_config": "saved",
        "converter_source_discovered": "found on this machine",
        "converter_source_flag": "command option",
        "read_method": "Reading the PDF",
        "read_method_auto": "Automatic",
        "read_method_never": "Text layer only",
        "read_method_always": "Layout converter",
        "read_method_help": (
            "Automatic reads the PDF's own text layer and hands it to the "
            "layout converter only when the pages are images. Choose the "
            "converter to reconstruct a layout the text layer gets wrong "
            "(multi-column, tables) — slower, and it needs one installed."
        ),
        "ingest_mode": "Mode",
        "ingest_mode_auto": "Auto (recommended)",
        "ingest_mode_manual": "Manual",
        "ingest_mode_help": (
            "Auto reads the table of contents, the page offset, and the "
            "section overlap from the document itself — just upload and add. "
            "Manual exposes every control and accepts an external TOC file."
        ),
        "advanced_overrides": "Advanced overrides (optional)",
        "overlap_auto": "Auto",
        "page_offset_auto": "Page offset (blank = auto)",
        "page_offset_auto_help": (
            "PDF page = book page + N. Leave blank to estimate it from the "
            "book's own running page numbers; set a value only to correct a "
            "wrong guess."
        ),
        "section_overlap_auto_help": (
            "Leave on Auto to pick 0 or 1 from how the document breaks: 0 when "
            "sections start on a fresh page, 1 when a boundary falls mid-page."
        ),
        "toc_page_pin": "Contents page number (blank = auto)",
        "toc_page_pin_help": (
            "1-based PDF page(s) holding the printed table of contents, "
            "comma-separated. Leave blank to detect it automatically."
        ),
        "invalid_number": "Enter a whole number, or leave it blank for auto.",
        "toc_method": "Table of contents",
        "toc_help": (
            "Printed contents page reads the book's own contents page(s); "
            "for a scanned book it falls back to the OCR backend."
        ),
        "toc_outline": "PDF outline",
        "toc_file": "TOC file",
        "toc_printed": "Printed contents page",
        "toc_file_label": "TOC (CSV or text)",
        "toc_format": "TOC format",
        "format_auto": "Auto",
        "format_csv": "CSV",
        "format_text": "Text",
        "page_offset": "Page offset (PDF page = book page + N)",
        "section_overlap": "Section overlap (pages)",
        "section_overlap_help": (
            "Extend each section into the next by N pages so a section that "
            "shares a boundary page stays complete. 1 is the default; 0 is strict."
        ),
        "recover_printed": "Recover printed page numbers (TOC)",
        "library_name_optional": "Library name (optional)",
        "run_ingest": "Add to library",
        "no_library": (
            "No library yet. Use ‘Add a book’ above, or run `dokey ingest`."
        ),
        "projects": "Projects",
        "add_project": "Add project folder",
        "add_project_help": (
            "Choose a project root once. Its dokey libraries stay available "
            "here on future launches."
        ),
        "add_project_title": "Choose a project folder",
        "project_folder_path": "Project folder path",
        "project_folder_path_help": (
            "Folder-dialog support is unavailable, so enter the project root."
        ),
        "not_project_folder": "Project folder does not exist: {path}",
        "no_project": "No project folder is available.",
        "project_empty": "No dokey libraries in this project yet.",
        "project_empty_main": (
            "This project is ready. Add a document below to create its first "
            "library."
        ),
        "project_root": "Project root",
        "forget_project": "Remove project from list",
        "forget_project_help": (
            "Forget this shortcut only. Files inside the project are not deleted."
        ),
        "adding_to_project": "Destination project · {project}",
        "project_breadcrumb": "{project} / {path}",
        "appearance": "Language",
        "search_settings": "Search settings",
        "library": "Library",
        "browse_library": "📂 Open a library folder…",
        "browse_library_help": (
            "Point at a library anywhere on this machine. dokey lists the ones "
            "under the folder it was started in; this reaches the rest."
        ),
        "browse_library_title": "Choose a dokey library folder",
        "browse_cancelled": "No folder chosen.",
        "custom_library_path": "Custom library path",
        "custom_library_path_help": "Overrides the library selected above.",
        "not_library": (
            "Not a library directory (no silver/sections.jsonl): {path}"
        ),
        "index_error": "Could not prepare the search index: {error}",
        "building_search_index": "Building search index...",
        "rebuild_index": "Rebuild index",
        "rebuilding_search_index": "Rebuilding search index...",
        "index_stats": "{sections} sections / {pages} pages indexed",
        "index_built": "Index built {created}",
        "no_page_text": (
            "No bronze/pages.jsonl in this library; only section titles are "
            "searchable. Re-ingest without --no-page-text."
        ),
        "max_results": "Max results",
        "title_match": "title match",
        "book_pages": "book pp. {start}–{end}",
        "content_pages": "content pp. {start}–{end}",
        "pdf_pages": "PDF pp. {start}–{end}",
        "matched_pdf_pages": "matched PDF pages: {pages}",
        "download_pdf": "Download PDF",
        "open": "Open",
        "search": "Search",
        "search_placeholder": (
            'e.g. controller tuning · valve OR actuator · "alarm management"'
        ),
        "no_matches": "No matches.",
        "column_index": "index",
        "column_parent": "parent",
        "column_title": "title",
        "column_book_start": "book start",
        "column_book_end": "book end",
        "column_content_start": "content start",
        "column_content_end": "content end",
        "column_pdf_start": "PDF start",
        "column_pdf_end": "PDF end",
        "column_page_count": "page count",
        "column_folio_source": "page-number source",
    },
    "ko": {
        "ingest_book": "책 추가",
        "close_import": "닫기",
        "ocr_backend": "OCR 서버 연결",
        "ingesting": "{name}을(를) 가져오는 중...",
        "recovering_pages": "책에 인쇄된 페이지 번호를 복원하는 중...",
        "skipped_page_recovery": (
            "인쇄 페이지 번호 복원을 건너뛰고 검색 색인만 만들었습니다: {error}"
        ),
        "building_index": "검색 색인을 만드는 중...",
        "ingest_failed": "책을 가져오지 못했습니다: {error}",
        "ingest_error": "책을 가져오는 중 오류가 발생했습니다: {error}",
        "ingested": "책을 추가했습니다 → {path}",
        "ingest_log": "처리 기록",
        "no_output": "(기록 없음)",
        "online": "연결됨 · {models}",
        "no_model_loaded": "불러온 모델 없음",
        "offline_ocr": (
            "연결 안 됨 — 로컬 OCR 서버를 실행하기 전에는 스캔 PDF를 처리할 수 없습니다."
        ),
        "endpoint": "서버 주소 (host:port 또는 전체 URL)",
        "endpoint_placeholder": "예: 127.0.0.1:1234 (LM Studio)",
        "save": "저장",
        "detect": "자동 찾기",
        "recheck": "다시 확인",
        "recheck_help": (
            "서버 상태를 다시 물어봅니다. 그러지 않으면 30초 동안 기억한 답을 "
            "쓰며, 매 조작마다 응답을 기다리지 않습니다."
        ),
        "no_backend_found": (
            "일반적으로 사용하는 로컬 포트에서 OpenAI 호환 서버를 찾지 못했습니다."
        ),
        "use": "사용",
        "backend_caption": (
            "dokey에는 모델이 포함되지 않습니다. 현재 사용 중인 LM Studio, "
            "llama.cpp llama-server 또는 Ollama 서버를 연결하세요."
        ),
        "backend_source_flag": "명령 옵션",
        "backend_source_config": "저장된 설정",
        "backend_source_default": "기본값",
        "pdf": "PDF",
        "document_file": "PDF · HWP/HWPX · 마크다운 · 엑셀(xlsx)",
        "sheet_input_caption": (
            "워크북은 시트 단위로 쪼개집니다 — 시트 하나가 절 하나가 되고, "
            "제목은 시트명이며 표는 그대로 실립니다. 표제 관련 설정은 "
            "적용되지 않으므로 더 정할 것이 없습니다."
        ),
        "sheet_sections_caption": "절이 될 시트 {count}개: {names}",
        "sheet_names_unreadable": (
            "이 파일에서 시트명을 읽지 못했습니다. 시트는 번호로 매겨집니다."
        ),
        "sheet_converter_offline": (
            "레이아웃 변환기를 찾지 못했습니다 — 워크북의 표를 읽으려면 "
            "필요합니다. `pip install docling`만 하면 별도 설정 없이 dokey가 "
            "여기서 실행합니다."
        ),
        "sheet_native_caption": (
            "파일에서 직접 읽습니다 — 셀·병합·차트·그림을 셀 앵커와 함께. "
            "변환기는 관여하지 않습니다."
        ),
        "flow_input_caption": (
            "흐름 문서: 마크다운으로 변환한 뒤 heading 단위로 쪼갭니다. "
            "원본에 지면이 없으므로(쪽 매김은 조판 산물) 마크다운만 내는 "
            "변환기로도 구조가 유실되지 않습니다."
        ),
        "flow_converter_choice": "변환기",
        "flow_converter_choice_help": (
            "이 문서를 어떤 도구로 변환할지, 이번 실행에 한한 선택입니다. "
            "원본에 지면이 없으므로 마크다운만 내는 변환기로도 충분하고 대개 "
            "훨씬 빠릅니다. 블록 열까지 내는 쪽은 더 많이 알 뿐입니다."
        ),
        "flow_converter_offline": (
            "이 형식을 받을 변환기가 없습니다. 경량으로 충분합니다: "
            "`pip install markitdown[docx,pptx]` — 또는 PDF의 지면 증거까지 "
            "보존하는 `pip install docling`."
        ),
        "sheet_legacy_caption": (
            "구형 .xls 워크북: 변환기 없이 직접 읽습니다."
        ),
        "sheet_xlrd_offline": (
            "구형 .xls를 읽으려면 xlrd가 필요합니다(`pip install xlrd`) — "
            "또는 .xlsx로 저장해 추가하십시오."
        ),
        "choose_document": "문서 선택",
        "change_document": "다른 문서 선택",
        "clear_document": "해제",
        "choose_document_title": "추가할 책 선택",
        "selected_document": "선택됨 · {path}",
        "document_missing": "선택한 파일을 더 이상 찾을 수 없습니다.",
        "md_input_caption": (
            "마크다운은 변환 없이 그대로 수집되어 heading 단위로 쪼개집니다"
            "(각 # 절이 한 단위). Docling/Marker가 뽑은 마크다운에 적합합니다."
        ),
        "section_depth": "절을 나눌 깊이",
        "section_depth_auto": "자동 — 인용 가능한 크기가 될 때까지",
        "section_depth_clause": "절 단위 (5.)",
        "section_depth_subclause": "소절 단위 (5.1)",
        "section_depth_help": (
            "절·소절은 각 문서 자신의 번호 체계에서 읽으므로, 사다리가 다른 "
            "문서들에서도 같은 종류의 단위를 고릅니다. 자동은 문서마다 따로 "
            "정하므로 한 문서를 읽기에는 좋지만 문서 간 비교에는 맞지 않습니다."
        ),
        "language_profile": "번호 체계",
        "language_profile_auto": "글에서 자동 판정",
        "language_profile_none": "없음 (숫자만)",
        "language_profile_ko": "한국어",
        "language_profile_help": (
            "dokey가 읽을 줄 아는 열거 계열((가)·①·제2장)과 문장이 끝났는지 "
            "판단하는 기준입니다. 표제와 문장 조각을 가르는 데 쓰이며, 기본은 "
            "글에서 자동으로 정합니다."
        ),
        "source_blocks": "원문 블록 (선택)",
        "source_blocks_help": (
            "이 마크다운을 만든 변환기의 JSON(DoclingDocument)입니다. 있으면 "
            "절이 원문에서 실제로 차지한 지면을 갖습니다(없으면 한 절당 한 쪽). "
            "파일 옆에 같은 이름의 .json이 있으면 알아서 찾고, 다른 곳에 있으면 "
            "여기에 올리십시오."
        ),
        "write_items": "항목까지 주소 매기기 (4.1 (1) (가))",
        "write_items_help": (
            "silver/items.jsonl을 만듭니다 — 절 안의 번호 항목마다 전체 주소와 "
            "위치를 기록합니다. 추출 작업에 쓰이며, 라이브러리를 가볍게 두려면 "
            "끄십시오."
        ),
        "preview_toc": "목차 미리보기",
        "preview_toc_help": (
            "문서를 읽어 이 설정이 만들 절을 보여줍니다. 아무것도 쓰지 않으며, "
            "추가를 누르기 전까지 추출은 일어나지 않습니다."
        ),
        "preview_reading": "문서를 읽는 중 ...",
        "preview_source": "목차 출처: {source} — 항목 {count}개, 깊이 {depth}로 분할",
        "preview_ladder": "번호 체계 판독: {ladder}",
        "preview_empty": (
            "이 문서에서 목차를 찾지 못했습니다. 추출해도 나눌 기준이 없습니다."
        ),
        "preview_failed": "문서를 읽지 못했습니다: {error}",
        "preview_not_extracted": (
            "아직 아무것도 추출되지 않았습니다 — 추가를 눌러야 저장됩니다."
        ),
        "preview_title": "절",
        "preview_level": "깊이",
        "preview_pages": "지면",
        "preview_chars": "글자수",
        "hwp_online": "HWP 변환기: {cmd}",
        "hwp_offline": (
            "HWP 변환기를 찾지 못했습니다. hwp2md를 설치하거나, 터미널에서 "
            "`dokey hwp --set \"...\"`로 지정하세요."
        ),
        "converter_online": "레이아웃 변환기: {cmd} ({source})",
        "converter_offline": (
            "레이아웃 변환기를 찾지 못했습니다. 스캔 지면은 읽을 텍스트가 없어 "
            "빈 채로 색인됩니다. `pip install docling`을 하면 별도 설정 없이 "
            "dokey가 여기서 실행합니다."
        ),
        "converter_source_config": "저장된 설정",
        "converter_source_discovered": "이 컴퓨터에서 발견",
        "converter_source_flag": "명령 옵션",
        "read_method": "PDF 읽기 방식",
        "read_method_auto": "자동",
        "read_method_never": "텍스트 층만",
        "read_method_always": "레이아웃 변환기",
        "read_method_help": (
            "자동은 PDF의 텍스트 층을 읽고, 지면이 이미지일 때만 레이아웃 "
            "변환기에 넘깁니다. 텍스트 층이 순서를 그르치는 조판(다단·표)은 "
            "변환기를 직접 고르십시오 — 느리고, 변환기가 설치돼 있어야 합니다."
        ),
        "ingest_mode": "인식 방식",
        "ingest_mode_auto": "자동 (권장)",
        "ingest_mode_manual": "직접 설정",
        "ingest_mode_help": (
            "자동은 목차, 페이지 보정값, 섹션 겹침을 문서에서 스스로 읽어냅니다. "
            "업로드하고 추가만 하면 됩니다. 직접 설정은 모든 항목을 지정하며 "
            "외부 목차 파일도 사용할 수 있습니다."
        ),
        "advanced_overrides": "고급 설정 (선택 사항)",
        "overlap_auto": "자동",
        "page_offset_auto": "페이지 보정값 (비우면 자동)",
        "page_offset_auto_help": (
            "PDF 페이지 = 책 페이지 + N. 비워 두면 책에 인쇄된 페이지 번호에서 "
            "자동으로 추정합니다. 추정이 틀렸을 때만 값을 지정하세요."
        ),
        "section_overlap_auto_help": (
            "‘자동’으로 두면 문서의 단락 방식에 따라 0 또는 1을 고릅니다. "
            "섹션이 새 페이지에서 시작하면 0, 페이지 중간에서 나뉘면 1입니다."
        ),
        "toc_page_pin": "목차 페이지 번호 (비우면 자동)",
        "toc_page_pin_help": (
            "인쇄된 목차가 있는 PDF 페이지 번호(1부터). 쉼표로 구분합니다. "
            "비워 두면 자동으로 찾습니다."
        ),
        "invalid_number": "정수를 입력하거나, 자동으로 두려면 비워 두세요.",
        "toc_method": "목차 인식 방법",
        "toc_help": (
            "‘인쇄된 목차 페이지’는 책 안의 목차를 직접 읽습니다. "
            "스캔 PDF라면 연결된 OCR 서버를 사용합니다."
        ),
        "toc_outline": "PDF 책갈피",
        "toc_file": "목차 파일",
        "toc_printed": "인쇄된 목차 페이지",
        "toc_file_label": "목차 파일 (CSV 또는 텍스트)",
        "toc_format": "목차 파일 형식",
        "format_auto": "자동",
        "format_csv": "CSV",
        "format_text": "텍스트",
        "page_offset": "페이지 보정값 (PDF 페이지 = 책 페이지 + N)",
        "section_overlap": "섹션 겹침 (페이지)",
        "section_overlap_help": (
            "섹션 경계가 한 페이지 안에 있을 때 내용이 잘리지 않도록 다음 섹션의 "
            "페이지를 N장 포함합니다. 기본값은 1이며, 0이면 겹치지 않습니다."
        ),
        "recover_printed": "책에 인쇄된 페이지 번호 복원",
        "library_name_optional": "라이브러리 이름 (선택 사항)",
        "run_ingest": "라이브러리에 추가",
        "no_library": (
            "아직 라이브러리가 없습니다. 위의 ‘책 추가’를 사용하거나 "
            "`dokey ingest`를 실행하세요."
        ),
        "projects": "프로젝트",
        "add_project": "프로젝트 폴더 추가",
        "add_project_help": (
            "프로젝트 루트를 한 번 선택하면 그 안의 dokey 라이브러리를 "
            "다음 실행에서도 바로 열 수 있습니다."
        ),
        "add_project_title": "프로젝트 폴더 선택",
        "project_folder_path": "프로젝트 폴더 경로",
        "project_folder_path_help": (
            "폴더 선택 창을 사용할 수 없어 프로젝트 루트를 직접 입력합니다."
        ),
        "not_project_folder": "프로젝트 폴더가 존재하지 않습니다: {path}",
        "no_project": "사용할 수 있는 프로젝트 폴더가 없습니다.",
        "project_empty": "이 프로젝트에는 아직 dokey 라이브러리가 없습니다.",
        "project_empty_main": (
            "프로젝트가 준비되었습니다. 아래에서 문서를 추가해 첫 "
            "라이브러리를 만드세요."
        ),
        "project_root": "프로젝트 루트",
        "forget_project": "목록에서 프로젝트 제거",
        "forget_project_help": (
            "바로가기만 지웁니다. 프로젝트 안의 파일은 삭제하지 않습니다."
        ),
        "adding_to_project": "저장할 프로젝트 · {project}",
        "project_breadcrumb": "{project} / {path}",
        "appearance": "언어",
        "search_settings": "검색 설정",
        "library": "라이브러리",
        "browse_library": "📂 라이브러리 폴더 열기…",
        "browse_library_help": (
            "이 컴퓨터의 어느 위치든 지정할 수 있습니다. 목록에는 dokey를 실행한 "
            "폴더 아래의 라이브러리만 나오며, 그 밖의 것은 이 버튼으로 엽니다."
        ),
        "browse_library_title": "dokey 라이브러리 폴더 선택",
        "browse_cancelled": "폴더를 고르지 않았습니다.",
        "custom_library_path": "사용자 지정 라이브러리 경로",
        "custom_library_path_help": "위에서 선택한 라이브러리 대신 이 경로를 사용합니다.",
        "not_library": (
            "라이브러리 폴더가 아닙니다(silver/sections.jsonl 없음): {path}"
        ),
        "index_error": "검색 색인을 준비하지 못했습니다: {error}",
        "building_search_index": "검색 색인을 만드는 중...",
        "rebuild_index": "검색 색인 다시 만들기",
        "rebuilding_search_index": "검색 색인을 다시 만드는 중...",
        "index_stats": "섹션 {sections}개 / 페이지 {pages}개 색인됨",
        "index_built": "색인 생성 시각: {created}",
        "no_page_text": (
            "이 라이브러리에 bronze/pages.jsonl이 없어 섹션 제목만 검색할 수 있습니다. "
            "--no-page-text 없이 다시 가져오세요."
        ),
        "max_results": "최대 검색 결과",
        "title_match": "제목 일치",
        "book_pages": "책 {start}–{end}쪽",
        "content_pages": "내용 {start}–{end}쪽",
        "pdf_pages": "PDF {start}–{end}쪽",
        "matched_pdf_pages": "검색어가 나온 PDF 페이지: {pages}",
        "download_pdf": "PDF 저장",
        "open": "열기",
        "search": "검색",
        "search_placeholder": (
            '예: 제어기 튜닝 · valve OR actuator · "alarm management"'
        ),
        "no_matches": "검색 결과가 없습니다.",
        "column_index": "순번",
        "column_parent": "상위 섹션",
        "column_title": "제목",
        "column_book_start": "책 시작",
        "column_book_end": "책 끝",
        "column_content_start": "내용 시작",
        "column_content_end": "내용 끝",
        "column_pdf_start": "PDF 시작",
        "column_pdf_end": "PDF 끝",
        "column_page_count": "페이지 수",
        "column_folio_source": "페이지 번호 출처",
    },
}


def normalize_language(value: object) -> str:
    if isinstance(value, str) and value in SUPPORTED_LANGUAGES:
        return value
    return DEFAULT_LANGUAGE


def preferred_language(config: Mapping[str, object]) -> str:
    return normalize_language(config.get("language"))


def translate(language: str, key: str, **values: object) -> str:
    template = TRANSLATIONS[normalize_language(language)][key]
    return template.format(**values)
