# KOSHA_GUIDE — Docling 블록 산출 (이관 코퍼스)

KOSHA 기술지원규정 866건(공표 자료)을 Docling으로 변환한 결과를 두는 자리다.
dokey의 마크다운 경로를 구현·검증하는 입력이며, **산출물 자체는 저장소에 담지
않는다**(JSON 264 MB + md 33.5 MB) — `.gitignore`가 이 README만 남긴다.

## 구성

```
<분야>/<DOC_ID>.json   DoclingDocument 전체 — 계약 (866건, 264 MB)
<분야>/<DOC_ID>.md     읽기 판본 — 파생물 (866건, 33.5 MB)
manifest.jsonl         문서별 대장
run.json               변환 설정
```

분야 9종 · 18,394지면 · 1,018만 자.

| 분야 | 문서 |
|---|---:|
| 화학안전분야 | 234 |
| 기계안전분야 | 138 |
| 전기안전분야 | 95 |
| 산업안전일반분야 | 84 |
| 산업보건일반분야 | 83 |
| 산업의학분야 | 81 |
| 건설안전분야 | 76 |
| 산업독성분야 | 44 |
| 리스크관리분야 | 31 |

## 무엇을 소비할 것인가

**JSON이 계약이고 md는 파생물이다.** md에는 지면 번호·bbox·`content_layer`가
실리지 않으므로, md만 읽으면 복원할 수 없는 정보가 있다 — 특히 **지면**은
복원 불가다(살아남은 러닝 마크로 지면 수를 재구성하면 866건 중 1건만 실제와
일치). 그 몫은 JSON 경로가 진다.

**단, 절 단위화는 md에서도 성립한다(2026-07-26).** `dokey auto <문서>.md`는
번호 체계에서 층위를 유도하고 러닝 장식을 반복+도달범위 투표로 격리한다:
866건에서 절 9,983개·문서당 중앙 10개·중앙 493자, 문자 98.1% 보존. 경위와
남은 결함은 지시서 §⑥, 재현은 `python scripts/check_md_corpus.py
input/kosha_guide`.

md는 사람이 읽는 용도와 md 경로 자체를 시험할 표본으로 남겨 둔다.

## 대장 한 줄

```json
{ "doc_id": "C-05-2016", "field": "건설안전분야", "status": "ok",
  "pages": 13, "blocks_text": 246, "blocks_table": 1, "chars": 7419,
  "labels": {"list_item":178, "text":38, "section_header":18,
             "page_footer":11, "caption":1},
  "layers": {"ContentLayer.BODY":235, "ContentLayer.FURNITURE":11},
  "needs_vlm_ocr": false, "seconds": 8.9,
  "sha256": "dc9116…" }
```

`needs_vlm_ocr`가 `true`인 22건은 순수 스캔본이다. **Docling 내장 OCR을 끄고
변환했으므로 본문이 비어 있는 것이 정상**이며, 침묵 누락이 아니다(내장
RapidOCR이 중국어 모델이라 한국어 스캔에서 한자를 만든다 — 지시서 §되풀이
금지 6번). 해당 지면은 BYO-VLM 경로가 담당한다.

## 재생성

원본 PDF와 변환 스크립트는 상류 프로젝트에 있다.

```powershell
python src/document_ingestion/kosha_docling.py            # 전량(재개 지원)
python src/document_ingestion/kosha_docling.py --render   # md만 재생성
```
