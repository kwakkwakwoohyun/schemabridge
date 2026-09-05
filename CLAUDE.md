# CLAUDE.md

이 파일은 이 저장소에서 작업할 때 클로드 코드(claude.ai/code)에게 제공하는 안내 문서입니다.

## 이 저장소는 무엇인가

이 저장소는 일반적인 코드 저장소가 아닙니다. 회사 "AI Master" 8주 과정에서 한 사람이 매주 제출하는 산출물을 기록한 한글 마크다운 문서 모음이 중심이고, 4주차 설계를 바탕으로 실제 동작하는 PoC 코드(`schemabridge/`)가 하위 폴더로 함께 존재합니다. 매주 과제는 정해진 템플릿에 따라 AI Agent를 설계하고(이후 일부는 실제로 구현하는) 것입니다.

git 저장소입니다. 원격: `https://github.com/kwakkwakwoohyun/schemabridge.git` (저장소 이름은 `schemabridge`이지만, 실제로는 `AI_Master` 전체 — 주차별 문서 + `schemabridge/` 코드 — 를 이 저장소 하나에 푸시하고 있습니다). 최상위에는 문서들이 있고, `schemabridge/`만 유일한 하위 폴더입니다. `.venv/`, `__pycache__/` 등은 `.gitignore`로 제외됩니다.

## 파일 구조 및 네이밍 규칙

- `program_chapter.md` — 8주 프로그램 전체의 마스터 템플릿. 1번("역량 및 기술 스택 확인")부터 8번("최종 산출물 제출")까지, 매주 산출물이 다뤄야 할 항목을 정의합니다.
- `N주차.md` (예: `2주차.md`, `5주차.md`) — N주차의 **빈 템플릿**. `program_chapter.md`에서 해당 주차 분량만 떼어내 온 것으로, 플레이스홀더(`[...]`)가 아직 채워지지 않은 상태입니다.
- `N주차_완료.md` (예: `2주차_완료.md`) — N주차의 **작성 완료된 제출본**. `_완료.md` 파일이 있는 주차만 실제로 완료된 것이고, 빈 `N주차.md` 템플릿만 있는 주차는 아직 시작 전입니다.
- 현재 기준 2~4주차는 완료본(`_완료`)이 있고, 5~7주차는 빈 템플릿만 존재합니다. 1주차와 8주차 파일은 아직 없습니다.

"N주차 해줘" 또는 "N주차 채워줘" 같은 요청을 받으면, 섹션 템플릿은 `N주차.md`에서, 항목별 상세 지침·예시는 `program_chapter.md`에서 참고해 `N주차_완료.md`를 작성(또는 수정)합니다. 빈 템플릿인 `N주차.md` 자체는 덮어쓰지 않습니다 — 이건 채워 넣을 원본이므로 그대로 보존합니다.

## 문서 작성 규칙

- 모든 내용은 **한글**로 작성합니다. 문서를 수정·확장할 때도 이 언어를 유지합니다.
- 완료본(`_완료`) 문서는 상단에 인용구(`>`) 형태의 개정 이력을 누적합니다: `> 멘토 피드백 반영 (vN): ...`(실제 멘토 피드백 반영) 또는 `> AI 피드백 검토 후 반영 (vN): ...`(AI 피드백 중 선별 반영). 각 개정은 실제로 수정된 부분에 `**[vN 신규]**`(신규 추가) 또는 `**[vN 수정]**`(기존 수정) 같은 인라인 마커도 함께 답니다. 완료된 문서를 다시 수정할 때는 이 패턴을 그대로 따릅니다 — `> ... 반영 (vN+1): ...` 형태로 무엇을·왜 바꿨는지 요약하는 새 노트를 추가하고, 수정된 부분에 `[vN+1 신규/수정]` 태그를 달되 기존 내용을 조용히 덮어쓰지 않습니다.
- 일부 문서는 맨 끝에 짧은 "회의 대비 요약" 섹션이 있습니다 — 멘토 미팅 전에 이것만 읽어도 무엇이·왜 바뀌었는지 알 수 있게 만든 요약입니다. 이 섹션은 위쪽 개정 이력과 항상 내용이 맞아떨어지도록 유지합니다.

## 이 과정에서 설계 중인 프로젝트: SchemaBridge

이 과정(2~4주차에 걸쳐 정의됨)의 실제 주제는 **SchemaBridge**입니다. 증권사 "차세대"(핵심시스템 전면 교체) 프로젝트에서, 개발자가 기존 매핑정의서를 참고해 AS-IS(레거시) DB 컬럼을 TO-BE(신규) 컬럼에 매핑하는 작업을 돕는 LLM Agent입니다. 이 매핑이 항상 1:1이 아니고, 수작업 대조가 느리고 실수하기 쉬워서 만들게 됐습니다.

핵심 시나리오 (상세는 `3주차_완료.md` 참고):
- **SC-001 (메인):** TO-BE 컬럼이 주어지면 매핑정의서를 정확 조회(Exact Lookup)해 AS-IS 후보를 찾고, 결정적 로직을 우선 적용하는 근거 체인(데이터 타입 필터 → 코드 매핑 일치 → 설명이 있으면 유사도/없으면 자체 추론 → 제약조건 → 유사한 확정 매핑 패턴 → 컬럼명)으로 순위를 매겨, 근거와 함께 추천 결과를 반환합니다. 확신도가 낮으면 사람에게 넘기기 전에 먼저 구체적으로 되묻습니다(최대 2회 재시도). 낮은 확신도의 매핑을 스스로 확정하지 않습니다.
- **SC-002 (확장, SC-001에 의존):** SC-001에서 확정된 매핑과 사전 정의된 조인 규칙을 이용해, 특정 세금 신고서용으로 범위가 제한된(자유 형식 text-to-SQL이 아닌) TO-BE 쿼리를 생성하고, Read-only 검증 후 실행하며, 실패 시 자기수정합니다(최대 3회 재시도).

계획된 아키텍처 (`4주차_완료.md` 기준):
- **오케스트레이션:** LangGraph(`>=1.2`). 노드: `classify_intent → lookup_mapping_candidates → filter_by_type → check_code_match → infer_secondary_evidence → judge_and_rank → request_clarification(루프) → generate_rationale → format_response`, SC-002 확장 체인: `search_schema → generate_sql → validate_readonly → execute_sql → self_correct`
- **LLM/임베딩:** Azure OpenAI(GPT-5.4 계열 + `text-embedding-3-small` 계열 임베딩) — 회사가 제공하는 표준 자원이라 선택함
- **검색 전략:** 정형화된 매핑정의서는 RAG가 아닌 정확 조회. 설명·샘플값 같은 비정형 근거에만 임베딩 유사도/LLM 추론 사용. 후보가 3~5개 수준으로 적어 Vector DB는 사용하지 않고 즉석 비교함
- **출력 파싱:** Structured Output/JSON Schema. 내부 추론과 사용자 노출용 근거(rationale)를 분리
- **모니터링:** Langfuse를 LangGraph 콜백으로 연결

## `schemabridge/` 구현 현황 (중요 — 항상 최신 상태로 유지할 것)

- **이미 구현·검증 완료 (그대로 재사용, 다시 만들지 말 것):**
    - `src/data_loader.py`, `src/lookup.py`(`lookup_mapping_candidates`), `src/filters.py`(`filter_by_type`), `src/code_match.py`(`check_code_match`) — 전부 결정적(deterministic) 로직이며 API 키 없이 동작함
    - `src/llm_client.py` — Azure OpenAI 공용 클라이언트 래퍼(`chat_completion_json` Structured Output, `embed`). LLM이 필요한 노드는 전부 이 모듈을 통해서만 호출함
    - `src/evidence.py`(`infer_secondary_evidence`) — 결정적 로직으로 못 가린 후보에 임베딩 유사도/LLM 자체추론으로 점수를 매김. `_get_confirmed_mappings`로 골든셋이 아니라 파이프라인이 스스로 확정한 결과만 few-shot 힌트로 재사용(순환검증 방지)
    - `src/judge.py`(`judge_and_rank`) — `evidence_scores`로 confirmed/ambiguous/insufficient_metadata 최종 판정(`confidence_gap` 임계값 10%, top1 최소 확신도 0.5 — 둘 다 초기값, PoC 진행하며 튜닝 예정)
    - `src/clarification.py`(`request_clarification`, `MAX_ATTEMPTS=2`) — confirmed가 안 나올 때 후보/근거를 보여주는 질문을 만들고(`build_clarification_question`), 사람 답변을 반영해 LLM으로 재점수화함(`rescore_with_clarification`). "질문 생성/재점수화" 로직만 담당하고, 실제로 답을 받는 방식(터미널 `input()` vs Streamlit 위젯)은 호출하는 쪽이 각자 구현
    - `src/graph.py` — LangGraph `StateGraph` 조립 완료. 노드: `lookup_mapping_candidates → filter_by_type → check_code_match → infer_secondary_evidence → judge_and_rank → format_response`(confirmed) / `handle_exception`(no_match·version_mismatch·insufficient_metadata, 그리고 2회 되물어도 안 풀린 ambiguous·insufficient_metadata 최종 종료) / `request_clarification`(confirmed가 아니면 터미널 `input()`으로 실제로 되묻고 `judge_and_rank`로 재진입하는 루프, 최대 2회)
    - **주의**: `request_clarification`이 실제로 동작하므로, `python3 src/graph.py`(인자 없이 골든셋 전체 실행)는 `settle_method_cd`처럼 ambiguous가 나오는 케이스에서 **터미널이 멈추고 입력을 기다림**. 자동화된 배치 실행이 필요하면 특정 컬럼만 인자로 넘기거나(`python3 src/graph.py ACC_WHT_AGG.pay_dt`), stdin으로 답변을 미리 파이프해야 함(예: `echo "답변" | python3 src/graph.py ACC_WHT_AGG.settle_method_cd`)
    - `data/schema.json`, `data/mapping_definition.json`, `data/code_mapping.json`, `data/golden_set.json` — 합성 데모 데이터(TO-BE `ACC_WHT_AGG` 테이블, AS-IS 원천징수 테이블들). 골든셋은 **14건**: 원래 12건(사람이 검증한 SC-001 완료 기준 정답지, 고정 — 절대 수정하지 말 것) + 2026-09-05에 테스트 커버리지 갭 해소용으로 추가한 2건(`wht_reason_cd`: 코드값 disambiguation 경로 검증용, `settle_method_cd`: `judge_and_rank`의 matched_keys 우선배치·ambiguous 판정 경로 검증용). 새 2건도 기존 12건과 같은 원칙(사람이 의도를 갖고 설계한 시나리오, 시스템 자기산출 결과 아님)으로 추가됨
    - `tests/test_deterministic.py` — 골든셋 전체를 lookup→filter→code_match 순으로 실행해 검증. 결정적으로 끝까지 판정 가능한 케이스(confirmed 5건, version_mismatch 1건, no_match 2건, insufficient_metadata 1건)는 실제 상태와 일치, 나머지(LLM 판단 필요한 케이스)는 의도대로 "PENDING → 다음 단계 필요"로 넘어감
    - `python3 src/graph.py <컬럼>`(개별 컬럼, Azure OpenAI 키 필요) 실행 시 골든셋 14건 각각 의도한 범주로 판정됨을 확인함(no_match 2건, version_mismatch 1건, insufficient_metadata 1건은 호출마다 항상 동일 — 나머지는 confirmed 또는 ambiguous). 단, `payee_biz_no`는 LLM 자체추론 점수가 임계값 근처(0.5~0.6대)라 호출마다 `confirmed`/`ambiguous`가 갈릴 수 있음(비결정적 LLM 호출 특성 — 버그 아님). `settle_method_cd`는 도움되는 답을 주면 1회 만에 `confirmed`로, 도움 안 되는 답을 반복하면 2회 후 `handle_exception`(최종 ambiguous)으로 정직하게 종료됨을 실제로 확인함(`request_clarification` 루프 검증용 케이스)
    - `app.py` — Streamlit 기반 **시연 영상용 뷰어**(제품 기능 아님, 2주차 범위정의상 "비개발자용 UI"는 Out of Scope로 명시했으므로 이것과 혼동하지 말 것). TO-BE 컬럼명을 입력하면 결정적 로직 → (필요시) `infer_secondary_evidence` → `judge_and_rank`까지 그대로 호출해 최종 상태를 실시간으로 보여주고, confirmed가 아니면 `st.session_state` 기반으로 `request_clarification`까지 실제로 되물어 재판정함(최대 2회) — 영상 시연은 이 화면 기준. `streamlit run app.py`로 실행
- **아직 구현 안 됨 (다음 작업 대상, 우선순위순):**
    - `generate_rationale` — confirmed 결과에 내부 추론과 분리된 사용자 노출용 근거 문장 생성
    - `classify_intent`, SC-002 체인(`search_schema` 이하) — SC-001 완료 후 착수 예정, 아직 손 안 댐
    - `data/setup_demo_data.py`, `src/cli.py`, `tests/run_eval.py` 같은 실행 엔트리포인트도 아직 없음 (4주차 설계 문서상의 계획일 뿐, 실제 파일 아님)
- 이미 구현된 결정적 로직/기존 골든셋 12건에 손대야 할 이유가 생기면, 먼저 사용자에게 확인하고 진행할 것 — 이미 여러 차례 검증을 거친 코드임.

## `schemabridge/` 실행 및 테스트

- `pyproject.toml`/`requirements.txt`는 아직 없습니다. 지금까지 구현된 결정적 로직(`data_loader`/`lookup`/`filters`/`code_match`)은 표준 라이브러리(`json`, `os`)만 사용하므로 외부 패키지 설치 없이 바로 실행됩니다. `langgraph`/`langchain-openai`/`openai` 등은 LLM 판정 노드를 구현할 때가 되어야 실제로 필요해집니다.
- 결정적 파이프라인 검증 스크립트 실행(사실상 유일한 "테스트"):
  ```
  cd schemabridge && python3 tests/test_deterministic.py
  ```
  pytest 기반이 아니라 골든셋 14건을 돌려 표로 출력하는 스크립트이므로, 개별 케이스만 보려면 파일 하단의 `["ACC_WHT_AGG.income_type_cd", ...]` 목록을 수정하거나 `main()`을 직접 호출해 특정 컬럼만 확인하세요.
- **중요 — Python 버전 주의:** `src/data_loader.py`가 `dict | None` (PEP 604) 반환 타입 문법을 쓰기 때문에 **Python 3.10 이상**이 필요합니다. 이 머신의 기본 `python3`는 `/usr/bin/python3` (3.9.6)이며, 이 버전으로 실행하면 모듈 임포트 시점에 `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`로 즉시 실패합니다. 이 머신에는 3.10+가 별도로 설치되어 있지 않으므로(`pyenv`, Homebrew python 모두 없음), 실행을 요청받으면 먼저 3.10+ 인터프리터 확보 방법(pyenv 설치, Homebrew `python@3.11` 등)을 사용자와 확인하세요 — 임의로 `dict | None`을 `Optional[dict]`로 바꾸는 등 코드를 낮은 버전에 맞춰 되돌리지 마세요(4주차 설계 문서 기준 Python 3.11+가 명시적 전제입니다).

## 이 저장소에서 작업할 때

- 문서 작업(`N주차_완료.md`)과 `schemabridge/` 코드 작업은 성격이 다릅니다. 관련 요청을 받으면 사용자가 실제로 원하는 게 "N주차 문서 작성/수정"인지 "`schemabridge/`에 남은 LLM 판정 노드 구현"인지 먼저 확인하세요.
- SchemaBridge를 실제로 구현해달라는 요청을 받으면, `4주차_완료.md`의 "개발 환경 구축"과 기술 스택 표를 기준 스펙으로 삼고, 이미 확정된 선택(Azure OpenAI, LangGraph, Vector DB 미사용 등)에서 벗어나기 전에 반드시 사용자에게 먼저 확인하세요 — 여러 차례 멘토 피드백을 거쳐 확정된 내용입니다.
