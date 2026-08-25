"""
LangGraph 그래프 조립 지점 (엔트리포인트).

4주차 설계상의 전체 그래프 중, 지금 실제로 구현된 결정적 노드 3개
(lookup_mapping_candidates -> filter_by_type -> check_code_match)만 연결한다.

- classify_intent, SC-002 체인(search_schema 이하)은 아직 아무 구현도 없으므로 포함하지 않았다.
- infer_secondary_evidence/judge_and_rank/request_clarification/generate_rationale(전부 LLM 필요)도
  아직 없다. 이 부분이 필요한 케이스는 확정하지 않고 "await_llm_judgment"에서 PENDING으로 멈춘다.
- 즉 이 그래프가 낼 수 있는 결론은 confirmed / no_match / version_mismatch /
  insufficient_metadata / pending_llm 다섯 가지뿐이며, ambiguous 최종 판정은 아직 못 낸다
  (judge_and_rank가 있어야 나옴).

실행하려면 langgraph 패키지가 필요하다: pip install langgraph
"""

import json
import os
import sys
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.code_match import check_code_match
from src.filters import filter_by_type
from src.lookup import lookup_mapping_candidates


class AgentState(TypedDict, total=False):
    to_be_column: str
    candidates: list[dict]
    source_version: str | None
    status_hint: str | None
    filtered_candidates: list[dict]
    excluded_candidates: list[dict]
    unknown_type: list[dict]
    code_match_results: list[dict]
    route: str  # 각 노드가 다음 목적지(노드 이름)를 직접 써넣는 내부 신호
    exception_status: str | None
    final_answer: dict


def lookup_node(state: AgentState) -> dict:
    result = lookup_mapping_candidates(state["to_be_column"])
    route = "handle_exception" if result["status_hint"] in ("no_match", "version_mismatch") else "filter_by_type"
    return {
        "candidates": result["candidates"],
        "source_version": result["source_version"],
        "status_hint": result["status_hint"],
        "route": route,
    }


def filter_node(state: AgentState) -> dict:
    result = filter_by_type(state["to_be_column"], state["candidates"])
    if not result["filtered"] or result["unknown_type"]:
        route = "handle_exception"
    else:
        route = "check_code_match"
    return {
        "filtered_candidates": result["filtered"],
        "excluded_candidates": result["excluded"],
        "unknown_type": result["unknown_type"],
        "route": route,
    }


def code_match_node(state: AgentState) -> dict:
    results = check_code_match(state["filtered_candidates"])
    matched = [r for r in results if r["matched"]]
    if len(state["filtered_candidates"]) == 1 or len(matched) == 1:
        route = "format_response"
    else:
        route = "await_llm_judgment"
    return {"code_match_results": results, "route": route}


def handle_exception_node(state: AgentState) -> dict:
    if state.get("status_hint") in ("no_match", "version_mismatch"):
        status = state["status_hint"]
        reason = (
            "매핑정의서에 해당 TO-BE 컬럼 자체가 없음"
            if status == "no_match"
            else f"매핑정의서(source_version={state.get('source_version')})가 가리키는 "
            "AS-IS 컬럼이 현재 스키마에 없음"
        )
    elif not state.get("filtered_candidates"):
        status, reason = "no_match", "타입 필수조건을 통과하는 후보가 하나도 없음(타입 충돌)"
    else:
        unknown = state.get("unknown_type") or []
        cols = [f"{c['candidate']['table']}.{c['candidate']['column']}" for c in unknown]
        status, reason = "insufficient_metadata", f"타입 정보가 없는 후보 존재: {cols}"

    return {
        "exception_status": status,
        "final_answer": {"to_be_column": state["to_be_column"], "status": status, "reason": reason},
    }


def format_response_node(state: AgentState) -> dict:
    filtered = state["filtered_candidates"]
    if len(filtered) == 1:
        winner = filtered[0]
        reason = "매핑정의서상 후보가 1개뿐이라 확정"
    else:
        matched_keys = {(r["table"], r["column"]) for r in state["code_match_results"] if r["matched"]}
        winner = next(c for c in filtered if (c["table"], c["column"]) in matched_keys)
        reason = "코드 매핑정의서상 코드값이 일치하는 유일한 후보라 강한 근거로 확정"

    return {
        "exception_status": None,
        "final_answer": {
            "to_be_column": state["to_be_column"],
            "status": "confirmed",
            "table": winner["table"],
            "column": winner["column"],
            "reason": reason,
        },
    }


def await_llm_judgment_node(state: AgentState) -> dict:
    candidates = [f"{c['table']}.{c['column']}" for c in state["filtered_candidates"]]
    return {
        "exception_status": "pending_llm",
        "final_answer": {
            "to_be_column": state["to_be_column"],
            "status": "pending_llm",
            "candidates": candidates,
            "reason": "결정적 로직만으로는 후보 우열을 가릴 수 없음. "
            "infer_secondary_evidence/judge_and_rank(LLM) 구현 후 재판정 필요",
        },
    }


def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("lookup_mapping_candidates", lookup_node)
    graph.add_node("filter_by_type", filter_node)
    graph.add_node("check_code_match", code_match_node)
    graph.add_node("handle_exception", handle_exception_node)
    graph.add_node("format_response", format_response_node)
    graph.add_node("await_llm_judgment", await_llm_judgment_node)

    graph.add_edge(START, "lookup_mapping_candidates")
    # 각 노드가 state["route"]에 실제 목적지 노드 이름을 써넣으므로, 그 값을 그대로 따라간다.
    # path_map을 명시해야 print_ascii()/draw_mermaid() 같은 정적 시각화 도구가
    # 실행해보지 않고도 분기 가능한 노드를 전부 알 수 있다.
    graph.add_conditional_edges(
        "lookup_mapping_candidates",
        lambda s: s["route"],
        {"filter_by_type": "filter_by_type", "handle_exception": "handle_exception"},
    )
    graph.add_conditional_edges(
        "filter_by_type",
        lambda s: s["route"],
        {"check_code_match": "check_code_match", "handle_exception": "handle_exception"},
    )
    graph.add_conditional_edges(
        "check_code_match",
        lambda s: s["route"],
        {"format_response": "format_response", "await_llm_judgment": "await_llm_judgment"},
    )
    graph.add_edge("handle_exception", END)
    graph.add_edge("format_response", END)
    graph.add_edge("await_llm_judgment", END)

    return graph.compile()


def main() -> None:
    app = build_graph()

    if len(sys.argv) > 1:
        columns = sys.argv[1:]
    else:
        golden_path = os.path.join(os.path.dirname(__file__), "..", "data", "golden_set.json")
        with open(golden_path, encoding="utf-8") as f:
            columns = [c["to_be_column"] for c in json.load(f)]

    for col in columns:
        result = app.invoke({"to_be_column": col})
        print(json.dumps(result["final_answer"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
