"""
LangGraph 그래프 조립 지점 (엔트리포인트).

4주차 설계상의 전체 그래프(SC-001 + SC-002 확장 체인)를 전부 연결한다.

- classify_intent가 그래프의 첫 진입점이다. 입력의 형태(식별자 vs 자연어)가 아니라
  의미로 SC-001(컬럼 매핑 조회)/SC-002(신고서용 집계 쿼리 생성)를 가른다(src/intent.py).
- SC-001 정방향(TO-BE 컬럼이 주어짐) 체인(lookup_mapping_candidates -> filter_by_type ->
  check_code_match -> infer_secondary_evidence -> judge_and_rank ->
  request_clarification -> generate_rationale -> format_response)은 이전과 동일하다.
- **2026-09-08 추가 — SC-001 역방향(AS-IS 컬럼이 주어짐)**: classify_intent가
  as_is_column을 채우면 lookup_reverse_mapping(src/lookup.py, 매핑정의서 역인덱스
  조회, 결정적)으로 간다. 정방향과 달리 후보 랭킹이 필요 없어(현재 데이터 기준 AS-IS
  컬럼 하나가 둘 이상의 TO-BE 컬럼에 걸치는 경우 0건) filter_by_type 이하 판정
  파이프라인을 타지 않고, matches가 정확히 1건이면 generate_reverse_rationale로 바로
  가서 generate_rationale을 재사용해 근거 문장을 만든 뒤 format_response로 합류한다
  (format_response의 "direction" 필드로 정방향/역방향을 구분해 노출). matches가 0건이면
  reverse_no_match, 2건 이상이면 reverse_ambiguous로 handle_exception이 종료한다.
  classify_intent가 TO-BE/AS-IS 어느 쪽인지조차 확신 못 하면(컬럼명이 양쪽에 겹치는
  경우 등, src/intent.py 참고) to_be_column/as_is_column을 둘 다 null로 반환하고,
  classify_intent_node가 이를 direction_ambiguous로 바로 handle_exception에 보낸다.
- SC-002 체인: search_schema(src/schema_search.py, 조인 규칙 정확 조회) 이후
  classify_intent가 매긴 sc002_mode로 갈라진다. mode="explore"(테이블/컬럼
  정보만 원하는 탐색 요청, 2026-09-07 추가 — src/intent.py docstring 참고)면
  format_schema_response로 바로 가서 search_schema가 검증해 둔 테이블/컬럼
  정보만 최종 답변으로 보여주고 끝난다(쿼리 생성·실행 없음). mode="execute"
  (실제 집계 결과를 원하는 요청, 기본값)면 기존처럼 generate_sql(src/sql_generation.py,
  LLM) -> validate_readonly(src/sql_validation.py, 결정적) -> (통과)
  execute_sql(src/sql_executor.py, SQLite 실제 실행) -> (성공) format_report_response /
  (실패, 재시도<3) generate_sql로 되돌아가는 self_correct 루프 / (실패, 재시도>=3
  또는 validate_readonly 위반 또는 리포트 정의 없음) handle_exception까지 간다.
  self_correct는 별도 노드가 아니라 이 재시도 루프 자체다.
- infer_secondary_evidence(LLM 임베딩 유사도 / 자체추론)가 매긴 evidence_scores를
  judge_and_rank가 받아 confidence_gap(1위-2위 점수차) 기준으로 confirmed/ambiguous/
  insufficient_metadata를 최종 판정한다(임계값은 src/judge.py 참고, 4주차 설계 v5의
  "초기값, PoC 진행하며 튜닝 예정" 잠정치).
- confirmed가 아니면 request_clarification이 실제로 되묻고(터미널 input()) 답을 받아
  점수를 재산정한 뒤 judge_and_rank로 되돌아간다(루프, 최대 MAX_ATTEMPTS=2회).
  그래도 confirmed가 안 나오면 handle_exception이 최종 ambiguous/insufficient_metadata로
  종료한다. request_clarification의 실제 시연은 이 CLI가 아니라 app.py(Streamlit,
  st.session_state 기반)에서 한다 — input()은 웹 서버 안에서 답을 받을 방법이 없어서다.
- confirmed 경로(단일 후보 / 코드값 일치 / LLM 점수 확정, 세 갈래 전부)는 format_response로
  바로 가지 않고 generate_rationale을 먼저 거친다. confidence_gap 퍼센트나 matched_keys
  같은 내부 계산값을 그대로 노출하는 대신, LLM이 사람이 읽기 좋은 근거 문장으로 바꿔서
  반환한다(4주차 설계 "내부 추론과 사용자 노출용 근거 분리" 원칙, src/rationale.py 참고).
- SC-001이 낼 수 있는 결론은 confirmed / no_match / version_mismatch /
  insufficient_metadata / ambiguous / direction_ambiguous(방향 자체를 특정 못 함,
  2026-09-08 추가). SC-002가 낼 수 있는 결론은 schema_found(mode=explore
  성공, format_schema_response) / report_ready(mode=execute 성공, format_report_response) /
  mapping_not_ready(리포트 정의 없음) / readonly_violation(쓰기 쿼리 시도) /
  sql_execution_failed(3회 재시도 소진) — 뒤 3개는 전부 handle_exception이 정직하게 종료한다.

실행하려면 langgraph, openai, python-dotenv 패키지가 필요하고,
LLM이 필요한 노드 실행 시 schemabridge/.env에 Azure OpenAI 설정이 있어야 한다.
SC-002는 추가로 data/setup_demo_db.py가 만드는 SQLite(data/demo.db, 최초 실행 시
자동 생성)가 필요하다.
"""

import json
import os
import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.clarification import MAX_ATTEMPTS, build_clarification_question, rescore_with_clarification
from src.code_match import check_code_match
from src.data_loader import get_column_info, load_schema
from src.discovery import (
    DISCOVERY_CONFIDENCE_GAP_THRESHOLD,
    DISCOVERY_LOW_CONFIDENCE_THRESHOLD,
    discover_as_is_candidates,
    discover_to_be_candidates,
)
from src.evidence import infer_secondary_evidence
from src.filters import filter_by_type
from src.intent import classify_intent
from src.judge import judge_and_rank
from src.lookup import lookup_mapping_candidates, lookup_reverse_mapping
from src.rationale import generate_rationale
from src.schema_search import search_schema
from src.sql_executor import MAX_SQL_ATTEMPTS, execute_sql
from src.sql_generation import generate_sql
from src.sql_validation import validate_readonly


class AgentState(TypedDict, total=False):
    user_request: str
    to_be_column: str
    as_is_column: str | None  # 역방향(AS-IS -> TO-BE) 조회 시에만 채워짐
    reverse_matches: list[dict]
    discovered: bool  # 매핑정의서 등록 없이 스키마 전체를 탐색해 찾은 후보인지(2026-09-09 추가)
    candidates: list[dict]
    source_version: str | None
    status_hint: str | None
    filtered_candidates: list[dict]
    excluded_candidates: list[dict]
    unknown_type: list[dict]
    code_match_results: list[dict]
    evidence_scores: list[dict]
    confidence_gap: float
    ranked_result: list[dict]
    judged_winner: dict | None
    judged_status: str | None
    clarification_attempts: int
    clarification_answers: list[str]
    rationale: str
    # --- SC-002 ---
    sc002_mode: str | None  # "explore"(테이블 정보만) or "execute"(쿼리 생성·실행까지)
    join_rule: dict | None
    schema_chunks: list[str]
    sql: str
    sql_attempts: int
    sql_error: str | None
    query_result: dict | None
    validation_error: str | None
    route: str  # 각 노드가 다음 목적지(노드 이름)를 직접 써넣는 내부 신호
    exception_status: str | None
    final_answer: dict


def classify_intent_node(state: AgentState) -> dict:
    result = classify_intent(state["user_request"])
    if result["intent"] == "SC-001":
        if result["to_be_column"]:
            return {
                "to_be_column": result["to_be_column"],
                "as_is_column": None,
                "route": "lookup_mapping_candidates",
            }
        if result["as_is_column"]:
            return {
                "as_is_column": result["as_is_column"],
                "to_be_column": None,
                "route": "lookup_reverse_mapping",
            }
        # 방향(TO-BE→AS-IS / AS-IS→TO-BE)조차 확신할 수 없는 경우(src/intent.py 참고) —
        # 억지로 추측하지 않고 사용자에게 다시 물어봐야 한다.
        return {"exception_status": "direction_ambiguous", "route": "handle_exception"}
    return {"sc002_mode": result["sc002_mode"], "route": "search_schema"}


def lookup_reverse_node(state: AgentState) -> dict:
    as_is_table, as_is_col = state["as_is_column"].split(".", 1)
    result = lookup_reverse_mapping(as_is_table, as_is_col)
    if not result["matches"]:
        # "matches 0건"은 두 경우를 다 포함한다: (a) AS-IS 컬럼 자체가 현재 스키마에 없음,
        # (b) 컬럼은 있는데 매핑정의서 어느 entry도 이걸 candidates로 등록하지 않음.
        # (a)는 앵커 자체가 무효라 탐색이 의미 없고, (b)만 탐색 대상이다(2026-09-09 추가).
        schema = load_schema()
        if get_column_info(schema, "AS-IS", as_is_table, as_is_col) is None:
            return {"exception_status": "reverse_no_match", "route": "handle_exception"}
        return {"route": "discover_to_be_candidates"}
    if len(result["matches"]) > 1:
        return {
            "reverse_matches": result["matches"],
            "exception_status": "reverse_ambiguous",
            "route": "handle_exception",
        }
    return {"to_be_column": result["matches"][0]["to_be_column"], "route": "generate_reverse_rationale"}


def discover_as_is_node(state: AgentState) -> dict:
    # 정방향 탐색(2026-09-09 추가): lookup_mapping_candidates가 no_match(매핑정의서에
    # 등록 자체가 없음)일 때, AS-IS 스키마 전체를 후보로 삼아 기존 filter_by_type 이하
    # 판정 파이프라인에 그대로 태운다(src/discovery.py 참고).
    candidates = discover_as_is_candidates(state["to_be_column"])
    if not candidates:
        return {"status_hint": "no_match", "route": "handle_exception"}
    return {"candidates": candidates, "discovered": True, "route": "filter_by_type"}


def discover_to_be_node(state: AgentState) -> dict:
    # 역방향 탐색(2026-09-09 추가): TO-BE 스키마 전체를 후보로 삼는다. 정방향과 달리
    # 앵커가 AS-IS 컬럼이라 filter_by_type/infer_secondary_evidence를 anchor_side="AS-IS"로
    # 불러야 해서 filter_by_type_reverse_node/infer_secondary_evidence_reverse_node를 새로 둔다.
    candidates = discover_to_be_candidates(state["as_is_column"])
    if not candidates:
        return {"exception_status": "reverse_no_match", "route": "handle_exception"}
    return {"candidates": candidates, "discovered": True, "route": "filter_by_type_reverse"}


def filter_by_type_reverse_node(state: AgentState) -> dict:
    result = filter_by_type(state["as_is_column"], state["candidates"], anchor_side="AS-IS")
    if not result["filtered"] or result["unknown_type"]:
        route = "handle_exception"
    else:
        route = "check_code_match"  # check_code_match_node가 as_is_column 존재로 방향을 스스로 판단
    return {
        "filtered_candidates": result["filtered"],
        "excluded_candidates": result["excluded"],
        "unknown_type": result["unknown_type"],
        "exception_status": "reverse_no_match" if route == "handle_exception" else None,
        "route": route,
    }


def infer_secondary_evidence_reverse_node(state: AgentState) -> dict:
    result = infer_secondary_evidence(state["as_is_column"], state["filtered_candidates"], anchor_side="AS-IS")
    return {"evidence_scores": result["evidence_scores"]}


def generate_reverse_rationale_node(state: AgentState) -> dict:
    # 역방향(단순 조회)은 후보 랭킹이 없어(lookup_reverse_mapping이 이미 단일 확정)
    # filter_by_type 이하 판정 파이프라인을 태우지 않고 바로 근거 문장을 만든다.
    # 역방향 탐색(2026-09-09 추가)은 judge_and_rank_node가 랭킹까지 마친 뒤 이 노드로
    # 합류하므로 ranked_result/discovered가 채워져 있으면 그대로 rationale에 반영한다.
    # generate_rationale은 "TO-BE X는 AS-IS Y로 매핑됐다"는 동일한 사실을 설명하므로
    # 정방향/역방향, 단순조회/탐색 어느 쪽에서 호출해도 그대로 재사용 가능하다.
    as_is_table, as_is_col = state["as_is_column"].split(".", 1)
    winner = {"table": as_is_table, "column": as_is_col}
    rationale = generate_rationale(
        to_be_column=state["to_be_column"],
        winner=winner,
        ranked_result=state.get("ranked_result"),
        discovered=state.get("discovered", False),
    )
    return {"judged_winner": winner, "rationale": rationale}


def lookup_node(state: AgentState) -> dict:
    result = lookup_mapping_candidates(state["to_be_column"])
    # lookup_mapping_candidates 를 실행하고 result의 status_hint 값을 보고 다음 노드는 어디로갈지 판단.
    # no_match(매핑정의서에 등록 자체가 없음)만 탐색으로 보낸다(2026-09-09 추가) — version_mismatch
    # (등록은 있는데 그 AS-IS 컬럼이 스키마에서 사라짐)는 탐색 대상이 아니라 그대로 예외 처리.
    if result["status_hint"] == "no_match":
        route = "discover_as_is_candidates"
    elif result["status_hint"] == "version_mismatch":
        route = "handle_exception"
    else:
        route = "filter_by_type"
    return {
        "candidates": result["candidates"],
        "source_version": result["source_version"],
        "status_hint": result["status_hint"],
        "route": route,
    }


def filter_node(state: AgentState) -> dict:
    result = filter_by_type(state["to_be_column"], state["candidates"])

    # 타입 불일치시 handle_exception 노드로
    if not result["filtered"] or result["unknown_type"]:
        route = "handle_exception"
    # 타입 일치시 check_code_match 노드로
    else:
        route = "check_code_match"
    return {
        "filtered_candidates": result["filtered"],
        "excluded_candidates": result["excluded"],
        "unknown_type": result["unknown_type"],
        "route": route,
    }


def code_match_node(state: AgentState) -> dict:
    # is_reverse: 역방향 탐색(discover_to_be_node를 거쳐 as_is_column이 앵커인 경우)만
    # True다. 역방향 "단순 조회"(lookup_reverse_node)는 이 노드를 거치지 않고 바로
    # generate_reverse_rationale로 가므로 여기 도달하는 역방향은 항상 탐색이다(2026-09-09 추가).
    is_reverse = bool(state.get("as_is_column"))
    results = check_code_match(state["filtered_candidates"])
    matched = [r for r in results if r["matched"]]
    shortcut = len(state["filtered_candidates"]) == 1 or len(matched) == 1

    if shortcut and is_reverse:
        if len(state["filtered_candidates"]) == 1:
            winner = state["filtered_candidates"][0]
        else:
            matched_keys = {(r["table"], r["column"]) for r in matched}
            winner = next(c for c in state["filtered_candidates"] if (c["table"], c["column"]) in matched_keys)
        return {
            "code_match_results": results,
            "to_be_column": f"{winner['table']}.{winner['column']}",
            "route": "generate_reverse_rationale",
        }

    if shortcut:
        route = "generate_rationale"
    elif is_reverse:
        route = "infer_secondary_evidence_reverse"
    else:
        route = "infer_secondary_evidence"
    return {"code_match_results": results, "route": route}


def infer_secondary_evidence_node(state: AgentState) -> dict:
    result = infer_secondary_evidence(state["to_be_column"], state["filtered_candidates"])
    return {"evidence_scores": result["evidence_scores"]}


def judge_and_rank_node(state: AgentState) -> dict:
    # discovered면 더 엄격한 임계값을 쓴다(2026-09-09 추가) — 매핑정의서에 등록되어 사람이
    # 미리 검증한 후보가 아니라 시스템이 스스로 찾아낸 후보라서, 확신이 더 클 때만 confirmed.
    discovered = state.get("discovered", False)
    if discovered:
        result = judge_and_rank(
            state["evidence_scores"],
            state["code_match_results"],
            DISCOVERY_LOW_CONFIDENCE_THRESHOLD,
            DISCOVERY_CONFIDENCE_GAP_THRESHOLD,
        )
    else:
        result = judge_and_rank(state["evidence_scores"], state["code_match_results"])

    is_reverse = bool(state.get("as_is_column"))
    extra: dict = {}

    if result["status"] == "confirmed":
        if is_reverse:
            winner = result["winner"]
            extra["to_be_column"] = f"{winner['table']}.{winner['column']}"
            route = "generate_reverse_rationale"
        else:
            route = "generate_rationale"
    elif discovered:
        # 탐색 경로는 되묻기 루프 없이, 엄격한 기준으로 판정한 결과를 그대로 정직하게 종료한다.
        route = "handle_exception"
        extra["exception_status"] = "discovery_inconclusive"
    elif state.get("clarification_attempts", 0) >= MAX_ATTEMPTS:
        route = "handle_exception"
    else:
        route = "request_clarification"

    return {
        "confidence_gap": result["confidence_gap"],
        "ranked_result": result["ranked_result"],
        "judged_winner": result["winner"],
        "judged_status": result["status"],
        "route": route,
        **extra,
    }


def handle_exception_node(state: AgentState) -> dict:
    sc002_status = state.get("exception_status")
    if sc002_status == "mapping_not_ready":
        reason = state.get("validation_error") or (
            "이 요청에 대응하는 사전 정의된 리포트/조인 규칙(data/join_rules.json)이 "
            "없음 — SC-001에서 매핑을 먼저 확정하고 리포트 정의를 등록해야 함"
        )
        return {
            "exception_status": sc002_status,
            "final_answer": {
                "user_request": state["user_request"],
                "status": sc002_status,
                "reason": reason,
            },
        }
    if sc002_status == "readonly_violation":
        return {
            "exception_status": sc002_status,
            "final_answer": {
                "user_request": state["user_request"],
                "status": sc002_status,
                "reason": f"생성된 SQL이 Read-only 검증을 통과하지 못함: {state.get('sql_error')}",
                "sql": state.get("sql"),
            },
        }
    if sc002_status == "sql_execution_failed":
        return {
            "exception_status": sc002_status,
            "final_answer": {
                "user_request": state["user_request"],
                "status": sc002_status,
                "reason": f"{state.get('sql_attempts', 0)}회 재시도해도 SQL 실행 실패 — "
                f"마지막 에러: {state.get('sql_error')}",
                "sql": state.get("sql"),
            },
        }
    if sc002_status == "direction_ambiguous":
        return {
            "exception_status": sc002_status,
            "final_answer": {
                "user_request": state["user_request"],
                "status": sc002_status,
                "reason": "입력이 TO-BE 컬럼을 묻는 건지 AS-IS 컬럼을 묻는 건지, 혹은 어느 "
                "테이블을 말하는 건지 특정할 수 없습니다. 테이블명을 포함해서 다시 질문해 "
                "주세요(예: 'LBR_WHT.settle_method_cd가 TO-BE 어디로 매핑돼?').",
            },
        }
    if sc002_status == "reverse_no_match":
        return {
            "exception_status": sc002_status,
            "final_answer": {
                "as_is_column": state.get("as_is_column"),
                "status": "no_match",
                "reason": f"'{state.get('as_is_column')}'을(를) TO-BE 컬럼으로 매핑하는 항목이 "
                "매핑정의서에 없거나, 이 AS-IS 컬럼 자체가 현재 스키마에 없습니다.",
            },
        }
    if sc002_status == "reverse_ambiguous":
        matches = state.get("reverse_matches", [])
        return {
            "exception_status": sc002_status,
            "final_answer": {
                "as_is_column": state.get("as_is_column"),
                "status": "ambiguous",
                "reason": "이 AS-IS 컬럼이 둘 이상의 TO-BE 컬럼에 매핑되어 있어 자동으로 하나를 "
                "고를 수 없습니다.",
                "candidates": [m["to_be_column"] for m in matches],
            },
        }
    if sc002_status == "discovery_inconclusive":
        # 매핑정의서 등록 없이 스키마 전체를 탐색했지만(2026-09-09 추가), 등록된 매핑보다
        # 엄격한 임계값(src/discovery.py)으로도 confirmed에 못 미친 경우 — 되묻기 루프
        # 없이 정직하게 종료하고, 참고할 수 있는 상위 후보를 그대로 노출한다.
        ranked = state.get("ranked_result") or []
        preview = [f"{r['table']}.{r['column']} (점수 {r['score']})" for r in ranked[:5]]
        is_reverse = bool(state.get("as_is_column"))
        anchor_key = {"as_is_column": state.get("as_is_column")} if is_reverse else {"to_be_column": state.get("to_be_column")}
        anchor_label = f"AS-IS 컬럼 '{state.get('as_is_column')}'" if is_reverse else f"TO-BE 컬럼 '{state.get('to_be_column')}'"
        return {
            "exception_status": sc002_status,
            "final_answer": {
                **anchor_key,
                "status": state.get("judged_status") or "insufficient_metadata",
                "reason": f"{anchor_label}은 매핑정의서에 등록되어 있지 않아 반대편 스키마 "
                "전체를 탐색했지만, 등록된 매핑보다 엄격한 기준으로도 확신할 수 있는 후보를 "
                "찾지 못했습니다. 사람의 검토가 필요합니다.",
                "candidates_considered": preview,
            },
        }

    if state.get("status_hint") in ("no_match", "version_mismatch"):
        status = state["status_hint"]
        if status == "no_match" and state.get("discovered"):
            reason = "매핑정의서에 등록이 없어 반대편 스키마 전체를 탐색했지만, 타입이 맞는 후보조차 하나도 없음"
        elif status == "no_match":
            reason = "매핑정의서에 해당 TO-BE 컬럼 자체가 없음"
        else:
            reason = (
                f"매핑정의서(source_version={state.get('source_version')})가 가리키는 "
                "AS-IS 컬럼이 현재 스키마에 없음"
            )
    elif not state.get("filtered_candidates"):
        status, reason = "no_match", "타입 필수조건을 통과하는 후보가 하나도 없음(타입 충돌)"
    elif state.get("judged_status") in ("ambiguous", "insufficient_metadata"):
        # judge_and_rank가 MAX_ATTEMPTS번 되물어도 confirmed를 못 낸 최종 종료 경로.
        status = state["judged_status"]
        attempts = state.get("clarification_attempts", 0)
        reason = f"{attempts}회 되물어도 확정하지 못함 — 사람의 최종 판단 필요"
        return {
            "exception_status": status,
            "final_answer": {
                "to_be_column": state["to_be_column"],
                "status": status,
                "reason": reason,
                "ranked_result": state.get("ranked_result", []),
                "clarification_answers": state.get("clarification_answers", []),
            },
        }
    else:
        unknown = state.get("unknown_type") or []
        cols = [f"{c['candidate']['table']}.{c['candidate']['column']}" for c in unknown]
        status, reason = "insufficient_metadata", f"타입 정보가 없는 후보 존재: {cols}"

    return {
        "exception_status": status,
        "final_answer": {"to_be_column": state["to_be_column"], "status": status, "reason": reason},
    }


def generate_rationale_node(state: AgentState) -> dict:
    # format_response 이전 단계에서 confirmed 경로 3가지(단일 후보/코드값 일치/LLM 점수 확정)의
    # winner를 여기서 한 번에 판정하고, 내부 계산값(confidence_gap 퍼센트, matched_keys 등)을
    # 그대로 노출하는 대신 LLM으로 사람이 읽기 좋은 근거 문장을 생성한다.
    filtered = state["filtered_candidates"]
    judged_winner = state.get("judged_winner")
    ranked_result = None
    if judged_winner:
        winner = judged_winner
        ranked_result = state.get("ranked_result")
    elif len(filtered) == 1:
        winner = filtered[0]
    else:
        matched_keys = {(r["table"], r["column"]) for r in state["code_match_results"] if r["matched"]}
        winner = next(c for c in filtered if (c["table"], c["column"]) in matched_keys)

    rationale = generate_rationale(
        to_be_column=state["to_be_column"],
        winner=winner,
        ranked_result=ranked_result,
        clarification_answers=state.get("clarification_answers"),
        discovered=state.get("discovered", False),
    )
    return {"judged_winner": winner, "rationale": rationale}


def format_response_node(state: AgentState) -> dict:
    winner = state["judged_winner"]
    direction = "as_is_to_to_be" if state.get("as_is_column") else "to_be_to_as_is"
    return {
        "exception_status": None,
        "final_answer": {
            "to_be_column": state["to_be_column"],
            "status": "confirmed",
            "direction": direction,
            "discovered": state.get("discovered", False),  # 2026-09-09 추가 — 등록된 매핑인지 탐색으로 찾은 건지
            "table": winner["table"],
            "column": winner["column"],
            "reason": state["rationale"],
        },
    }


def request_clarification_node(state: AgentState) -> dict:
    # 터미널 CLI용 최소 구현. 실제 시연(Streamlit)은 app.py가 st.session_state로
    # 같은 build_clarification_question/rescore_with_clarification을 재사용해 별도로 구현한다 —
    # input()은 웹 서버(app.py) 안에서 답을 받을 방법이 없어서 CLI 전용으로 남겨둠.
    question = build_clarification_question(state["to_be_column"], state["ranked_result"])
    answer = input(f"\n[request_clarification] {question}\n> ")

    updated_scores = rescore_with_clarification(state["to_be_column"], state["ranked_result"], answer)
    attempts = state.get("clarification_attempts", 0) + 1
    answers = state.get("clarification_answers", []) + [answer]

    return {
        "evidence_scores": updated_scores,
        "clarification_attempts": attempts,
        "clarification_answers": answers,
    }


def search_schema_node(state: AgentState) -> dict:
    result = search_schema()
    if not result["found"]:
        return {
            "exception_status": "mapping_not_ready",
            "validation_error": result.get("validation_error"),
            "route": "handle_exception",
        }
    route = "format_schema_response" if state.get("sc002_mode") == "explore" else "generate_sql"
    return {"schema_chunks": result["schema_chunks"], "join_rule": result["join_rule"], "route": route}


def format_schema_response_node(state: AgentState) -> dict:
    # sc002_mode == "explore": 실제 쿼리를 만들거나 실행하지 않고, search_schema가
    # 이미 검증해 둔 테이블/컬럼 정보만 보여준다(src/intent.py docstring 참고).
    join_rule = state["join_rule"]
    sources = [
        {
            "to_be_table": s["to_be_table"],
            "income_type_label": s["income_type_label"],
            "column_map": s["column_map"],
        }
        for s in join_rule["sources"]
    ]
    return {
        "exception_status": None,
        "final_answer": {
            "user_request": state["user_request"],
            "status": "schema_found",
            "report_name": join_rule["report_name"],
            "report_columns": [c["field"] for c in join_rule["report_columns"]],
            "sources": sources,
        },
    }


def generate_sql_node(state: AgentState) -> dict:
    result = generate_sql(state["schema_chunks"], state["user_request"], state.get("sql_error"))
    return {"sql": result["sql"], "route": "validate_readonly"}


def validate_readonly_node(state: AgentState) -> dict:
    result = validate_readonly(state["sql"])
    if result["is_valid"]:
        return {"route": "execute_sql"}
    return {"exception_status": "readonly_violation", "sql_error": result["reason"], "route": "handle_exception"}


def execute_sql_node(state: AgentState) -> dict:
    result = execute_sql(state["sql"])
    attempts = state.get("sql_attempts", 0) + 1

    if result["error"] is None:
        return {"query_result": result, "sql_attempts": attempts, "route": "format_report_response"}

    if attempts < MAX_SQL_ATTEMPTS:
        route = "generate_sql"  # self_correct: 에러를 반영해 generate_sql로 되돌아가는 재시도 루프
        return {"sql_error": result["error"], "sql_attempts": attempts, "route": route}

    return {
        "sql_error": result["error"],
        "sql_attempts": attempts,
        "exception_status": "sql_execution_failed",
        "route": "handle_exception",
    }


def format_report_response_node(state: AgentState) -> dict:
    result = state["query_result"]
    return {
        "exception_status": None,
        "final_answer": {
            "user_request": state["user_request"],
            "status": "report_ready",
            "report_name": state["join_rule"]["report_name"],
            "sql": state["sql"],
            "columns": result["columns"],
            "rows": result["rows"],
        },
    }


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("lookup_mapping_candidates", lookup_node)
    graph.add_node("lookup_reverse_mapping", lookup_reverse_node)
    graph.add_node("discover_as_is_candidates", discover_as_is_node)
    graph.add_node("discover_to_be_candidates", discover_to_be_node)
    graph.add_node("filter_by_type_reverse", filter_by_type_reverse_node)
    graph.add_node("infer_secondary_evidence_reverse", infer_secondary_evidence_reverse_node)
    graph.add_node("generate_reverse_rationale", generate_reverse_rationale_node)
    graph.add_node("filter_by_type", filter_node)
    graph.add_node("check_code_match", code_match_node)
    graph.add_node("handle_exception", handle_exception_node)
    graph.add_node("format_response", format_response_node)
    graph.add_node("infer_secondary_evidence", infer_secondary_evidence_node)
    graph.add_node("judge_and_rank", judge_and_rank_node)
    graph.add_node("request_clarification", request_clarification_node)
    graph.add_node("generate_rationale", generate_rationale_node)
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("search_schema", search_schema_node)
    graph.add_node("generate_sql", generate_sql_node)
    graph.add_node("validate_readonly", validate_readonly_node)
    graph.add_node("execute_sql", execute_sql_node)
    graph.add_node("format_report_response", format_report_response_node)
    graph.add_node("format_schema_response", format_schema_response_node)

    graph.add_edge(START, "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        lambda s: s["route"],
        {
            "lookup_mapping_candidates": "lookup_mapping_candidates",
            "lookup_reverse_mapping": "lookup_reverse_mapping",
            "search_schema": "search_schema",
            "handle_exception": "handle_exception",  # 방향(direction_ambiguous) 자체를 판별 못 한 경우
        },
    )
    # 각 노드가 state["route"]에 실제 목적지 노드 이름을 써넣으므로, 그 값을 그대로 따라간다.
    # path_map을 명시해야 print_ascii()/draw_mermaid() 같은 정적 시각화 도구가
    # 실행해보지 않고도 분기 가능한 노드를 전부 알 수 있다.
    graph.add_conditional_edges(
        "lookup_mapping_candidates",
        lambda s: s["route"],
        {
            "filter_by_type": "filter_by_type",
            "discover_as_is_candidates": "discover_as_is_candidates",
            "handle_exception": "handle_exception",
        },
    )
    graph.add_conditional_edges(
        "lookup_reverse_mapping",
        lambda s: s["route"],
        {
            "generate_reverse_rationale": "generate_reverse_rationale",
            "discover_to_be_candidates": "discover_to_be_candidates",
            "handle_exception": "handle_exception",
        },
    )
    graph.add_conditional_edges(
        "discover_as_is_candidates",
        lambda s: s["route"],
        {"filter_by_type": "filter_by_type", "handle_exception": "handle_exception"},
    )
    graph.add_conditional_edges(
        "discover_to_be_candidates",
        lambda s: s["route"],
        {"filter_by_type_reverse": "filter_by_type_reverse", "handle_exception": "handle_exception"},
    )
    graph.add_conditional_edges(
        "filter_by_type_reverse",
        lambda s: s["route"],
        {"check_code_match": "check_code_match", "handle_exception": "handle_exception"},
    )
    graph.add_edge("generate_reverse_rationale", "format_response")
    graph.add_conditional_edges(
        "filter_by_type",
        lambda s: s["route"],
        {"check_code_match": "check_code_match", "handle_exception": "handle_exception"},
    )
    graph.add_conditional_edges(
        "check_code_match",
        lambda s: s["route"],
        {
            "generate_rationale": "generate_rationale",
            "generate_reverse_rationale": "generate_reverse_rationale",
            "infer_secondary_evidence": "infer_secondary_evidence",
            "infer_secondary_evidence_reverse": "infer_secondary_evidence_reverse",
        },
    )
    graph.add_edge("infer_secondary_evidence", "judge_and_rank")
    graph.add_edge("infer_secondary_evidence_reverse", "judge_and_rank")
    graph.add_conditional_edges(
        "judge_and_rank",
        lambda s: s["route"],
        {
            "generate_rationale": "generate_rationale",
            "generate_reverse_rationale": "generate_reverse_rationale",
            "request_clarification": "request_clarification",
            "handle_exception": "handle_exception",
        },
    )
    graph.add_edge("request_clarification", "judge_and_rank")  # 답변 반영 후 재판정 루프
    graph.add_edge("generate_rationale", "format_response")
    graph.add_conditional_edges(
        "search_schema",
        lambda s: s["route"],
        {
            "generate_sql": "generate_sql",
            "format_schema_response": "format_schema_response",
            "handle_exception": "handle_exception",
        },
    )
    graph.add_edge("generate_sql", "validate_readonly")
    graph.add_conditional_edges(
        "validate_readonly",
        lambda s: s["route"],
        {"execute_sql": "execute_sql", "handle_exception": "handle_exception"},
    )
    graph.add_conditional_edges(
        "execute_sql",
        lambda s: s["route"],
        {
            "format_report_response": "format_report_response",
            "generate_sql": "generate_sql",  # self_correct: 에러 반영해 재생성하는 루프
            "handle_exception": "handle_exception",
        },
    )
    graph.add_edge("handle_exception", END)
    graph.add_edge("format_response", END)
    graph.add_edge("format_report_response", END)
    graph.add_edge("format_schema_response", END)

    return graph.compile()


def main() -> None:
    app = build_graph()

    if len(sys.argv) > 1:
        requests = sys.argv[1:]
    else:
        golden_path = os.path.join(os.path.dirname(__file__), "..", "data", "golden_set.json")
        with open(golden_path, encoding="utf-8") as f:
            requests = [c["to_be_column"] for c in json.load(f)]

    for req in requests:
        result = app.invoke({"user_request": req})
        print(json.dumps(result["final_answer"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
