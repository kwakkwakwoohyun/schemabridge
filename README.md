# 🦖 SchemaBridge

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Orchestration](https://img.shields.io/badge/orchestration-LangGraph-6f42c1)
![Status](https://img.shields.io/badge/status-PoC%20in%20progress-yellow)

**AS-IS ↔ TO-BE 매핑 판단을 돕는 LLM Agent.**
증권사 차세대(핵심시스템 마이그레이션) 프로젝트에서, 매핑정의서를 대조해가며 TO-BE 쿼리를 개발하는 작업을 자동화합니다.

---

## 왜 만들었나

차세대 프로젝트에서는 AS-IS 코드를 분석해 TO-BE ERD를 설계하고, 이후 테이블·컬럼 매핑정의서를 엑셀로 수작업 작성합니다. 개발자는 TO-BE 쿼리를 짤 때마다 이 매핑정의서와 AS-IS 코드를 일일이 열어 대조해야 하는데, 문제는 AS-IS:TO-BE 관계가 항상 1:1이 아니라는 점입니다. 설계 변경으로 1:N으로 쪼개지거나 구조 자체가 바뀌는 경우, 그걸 사람이 그때그때 판단해서 반영해야 해서 개발 속도가 느려지고 실수 위험도 커집니다.

**SchemaBridge**는 매핑정의서를 근거로 여러 AS-IS 후보 중 최적 후보를 순위와 함께 추천하고, 근거가 부족하면 임의로 확정하지 않고 명시적으로 판단 불가 상태로 표시합니다.

## 핵심 시나리오

| | 설명 |
| --- | --- |
| **SC-001** (메인) | TO-BE 컬럼을 입력하면 매핑정의서를 정확 조회(Exact Lookup)해 AS-IS 후보를 찾고, `타입 필터 → 코드값 일치 → 설명 유사도/자체추론` 순으로 근거를 쌓아 순위를 매깁니다. 확신도가 낮으면 사람에게 넘기기 전에 먼저 구체적으로 되묻습니다(최대 2회). |
| **SC-002** (확장) | SC-001에서 확정된 매핑과 사전 정의된 조인 규칙으로, 범위가 제한된 TO-BE 집계 쿼리를 생성·검증·실행합니다(자유 형식 text-to-SQL이 아님). |

## 아키텍처

```mermaid
graph TD;
	__start__([__start__]):::first
	lookup_mapping_candidates(lookup_mapping_candidates)
	filter_by_type(filter_by_type)
	check_code_match(check_code_match)
	handle_exception(handle_exception)
	format_response(format_response)
	infer_secondary_evidence(infer_secondary_evidence)
	judge_and_rank(judge_and_rank)
	request_clarification(request_clarification)
	__end__([__end__]):::last
	__start__ --> lookup_mapping_candidates;
	lookup_mapping_candidates -.-> filter_by_type;
	lookup_mapping_candidates -.-> handle_exception;
	filter_by_type -.-> check_code_match;
	filter_by_type -.-> handle_exception;
	check_code_match -.-> format_response;
	check_code_match -.-> infer_secondary_evidence;
	infer_secondary_evidence --> judge_and_rank;
	judge_and_rank -.-> format_response;
	judge_and_rank -.-> request_clarification;
	judge_and_rank -.-> handle_exception;
	request_clarification --> judge_and_rank;
	format_response --> __end__;
	handle_exception --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

LangGraph로 조립된 판단 체인입니다. 정형 데이터(매핑정의서·코드 매핑정의서)는 벡터 검색이 아니라 정확 조회를 쓰고, 근거가 확실한 케이스는 결정적 로직만으로 바로 확정됩니다. 후보가 여러 개인데 결정적 로직으로 못 가리면 임베딩 유사도/LLM 자체추론(`infer_secondary_evidence`)으로 점수를 매기고, 그 점수차(`confidence_gap`)를 기준으로 `judge_and_rank`가 최종 confirmed/ambiguous/insufficient_metadata를 판정합니다. confirmed가 아니면 `request_clarification`이 실제로 사람에게 되묻고 답을 반영해 재점수화한 뒤 `judge_and_rank`로 되돌아갑니다(루프, 최대 2회) — 그래도 안 풀리면 `handle_exception`이 정직하게 종료합니다.

| 구분 | 선택 | 이유 |
| --- | --- | --- |
| Orchestration | LangGraph | 조건부 분기·예외 상태·재시도 루프를 노드/엣지로 명시 가능 |
| LLM / Embedding | Azure OpenAI (`gpt-5.6-luna` / `text-embedding-3-large`) | 사내 표준 제공 자원 |
| Retrieval | Exact Lookup + 경량 임베딩 유사도 (Vector DB 미사용) | 후보가 3~5개 수준으로 적어 즉석 비교로 충분 |
| Output Parsing | Structured Output (JSON Schema strict mode) | 내부 추론과 사용자 노출 근거(rationale) 분리 |
| Monitoring | Langfuse | LangGraph 콜백으로 연결 예정 |

## 지금 뭐가 되고 뭐가 안 되는지

- [x] `lookup_mapping_candidates` — 매핑정의서 정확 조회 + 버전 불일치 감지
- [x] `filter_by_type` — 타입 필수조건 필터
- [x] `check_code_match` — 코드값 일치 여부(강한 근거)
- [x] `infer_secondary_evidence` — 설명 유사도(임베딩) / 설명 없을 때 자체추론(LLM, Structured Output)
- [x] `judge_and_rank` — `confidence_gap` 기준 confirmed/ambiguous/insufficient_metadata 최종 판정
- [x] 위 노드들을 LangGraph 그래프로 조립 (`src/graph.py`)
- [x] `request_clarification` — 애매한 판정에 대해 사람에게 구체적으로 되묻는 루프(최대 2회, 답변 반영해 재점수화 후 재판정)
- [x] Streamlit 데모 뷰어 (`app.py`) — `judge_and_rank`/`request_clarification`까지 반영해 실시간 판정·되묻기 UI 표시(`st.session_state` 기반)
- [ ] `generate_rationale` — 내부 추론과 분리된 사용자 노출용 근거 문장 생성
- [ ] `classify_intent`, SC-002(신고서용 집계 쿼리 생성) 전체 — SC-001 완료 후 착수 예정

### 골든셋 14건 — 시연 가능한 케이스

`ACC_WHT_AGG` TO-BE 테이블 컬럼으로 5가지 상태를 전부 재현합니다. `streamlit run app.py` 또는 `python3 src/graph.py`로 직접 확인할 수 있습니다.

| 결과 | 예시 컬럼 | 판정 근거 |
| --- | --- | --- |
| `confirmed` | `pay_dt` / `payee_nm` / `pay_amt` | 매핑정의서상 후보가 1개뿐이라 확정 |
| `confirmed` | `income_type_cd` | 공통코드 매핑정의서에서 코드값 일치(후보 1개) — 강한 근거로 확정 |
| `confirmed` | `wht_reason_cd` | 후보 2개 중 코드값 일치가 정확히 1개 — `format_response`의 코드값 disambiguation 경로 |
| `confirmed` | `wht_tax_amt` | 후보 둘 다 설명 있음 → 임베딩 유사도(`description_similarity`)로 점수화 → `judge_and_rank`가 확정 |
| `confirmed` | `tax_rate` / `payee_biz_no` / `div_payee_nm` | 후보에 설명 없음 → LLM 자체추론(`self_inference`)으로 점수화 → `judge_and_rank`가 확정 |
| `ambiguous` → 되묻기 → `confirmed` | `settle_method_cd` | 후보 3개 중 2개가 동시에 코드값 일치(`matched_keys` 우선배치) + 설명 문구까지 동일해 임베딩 유사도로도 우열 불가 — `request_clarification`이 실제로 되묻는 걸 보여주는 대표 시연 케이스 |
| `no_match` | `updt_dt` / `biz_reg_no` | 매핑정의서에 해당 TO-BE 컬럼 자체가 없음 |
| `version_mismatch` | `reg_dt` | 매핑정의서가 가리키는 AS-IS 컬럼이 스키마 개편으로 사라짐 |
| `insufficient_metadata` | `div_wht_amt` | 후보의 데이터 타입 정보 자체가 없어 필수조건 판정 불가 |

원래 골든셋 12건(사람이 검증한 SC-001 완료 기준 정답지, 고정)에 더해, `wht_reason_cd`/`settle_method_cd` 2건은 테스트 커버리지 갭(후보 2개+ 중 코드값 disambiguation, `judge_and_rank`의 매치 우선배치·ambiguous 분기)을 검증하기 위해 추가한 케이스입니다. `payee_biz_no`처럼 LLM 판정 점수가 임계값 근처인 케이스는 호출마다 `confirmed`/`ambiguous`가 갈릴 수 있습니다(비결정적 LLM 호출 특성).

## 빠른 시작

```bash
cd schemabridge
python3.11 -m venv .venv
.venv/bin/pip install langgraph langchain-openai openai python-dotenv streamlit

# 결정적 파이프라인만 (외부 패키지 불필요, API 키 없이 실행 가능)
.venv/bin/python tests/test_deterministic.py

# LangGraph 전체 실행 (Azure OpenAI 키 필요 — .env.example 참고해 .env 준비)
.venv/bin/python -m src.graph
.venv/bin/python -m src.graph ACC_WHT_AGG.wht_tax_amt   # 특정 컬럼만

# 브라우저 데모
.venv/bin/streamlit run app.py
```

> **Python 3.10+ 필요** — 코드가 `dict | None` 같은 최신 타입 문법을 씁니다.
> LLM/임베딩이 필요한 노드(`infer_secondary_evidence`, `judge_and_rank`)를 쓰려면 `schemabridge/.env`에 Azure OpenAI 자격 정보가 있어야 합니다(`.env.example` 참고).

## 프로젝트 구조

```
schemabridge/
├── data/                    합성 데모 데이터 (스키마·매핑정의서·코드매핑·골든셋 14건)
├── src/
│   ├── data_loader.py       JSON 로더
│   ├── lookup.py            Node: lookup_mapping_candidates
│   ├── filters.py           Node: filter_by_type
│   ├── code_match.py        Node: check_code_match
│   ├── llm_client.py        Azure OpenAI 공용 클라이언트 (Structured Output / 임베딩)
│   ├── evidence.py          Node: infer_secondary_evidence
│   ├── judge.py             Node: judge_and_rank
│   ├── clarification.py     Node: request_clarification (질문 생성 + 답변 반영 재점수화)
│   └── graph.py             LangGraph 조립 + 엔트리포인트
├── tests/                   결정적 파이프라인 검증 스크립트
└── app.py                   Streamlit 시연용 뷰어
```

---

## 이 저장소에 대하여

`schemabridge/`는 "AI Master" 8주 과정의 PoC 산출물입니다. 저장소 루트에는 이 프로젝트를 설계해온 주차별 문서(문제 정의 → 시나리오 → 상세 설계 → PoC 구현)가 함께 들어 있습니다. 자세한 작업 규칙과 구현 현황은 [`CLAUDE.md`](./CLAUDE.md)를 참고하세요.
