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
        "ingest_book": "➕ Add a book",
        "ocr_backend": "🔌 OCR backend (bring your own)",
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
        "document_file": "PDF · HWP/HWPX · Markdown",
        "md_input_caption": (
            "Markdown is ingested as-is and unitized by heading (each # section "
            "becomes a unit) — no conversion. Ideal for a Docling/Marker render."
        ),
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
        "ingest_book": "➕ 책 추가",
        "ocr_backend": "🔌 OCR 서버 연결",
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
        "document_file": "PDF · HWP/HWPX · 마크다운",
        "md_input_caption": (
            "마크다운은 변환 없이 그대로 수집되어 heading 단위로 쪼개집니다"
            "(각 # 절이 한 단위). Docling/Marker가 뽑은 마크다운에 적합합니다."
        ),
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
