"""
SC-001 후보 자동 탐색(2026-09-09 추가) 검증 스크립트.

매핑정의서에 등록이 없는 컬럼을 물었을 때 no_match로 바로 끝내지 않고, 반대편
스키마 전체를 후보로 삼아 기존 판정 파이프라인(filter_by_type -> check_code_match ->
infer_secondary_evidence -> judge_and_rank, 더 엄격한 임계값)에 태우는 기능(src/discovery.py).

결정적인 부분(enumerate_all_columns, anchor_side 파라미터 전달)은 API 키 없이 검증한다.
탐색 성공/실패 판정은 LLM/임베딩 호출이 필요해서 .env 설정이 있어야 통과한다. classify_intent의
방향 판별 자체는 이미 test_reverse_lookup.py가 다루고 있고 별도의 비결정성 이슈가 있어서,
여기서는 classify_intent를 거치지 않고 그래프 노드 함수를 직접 순서대로 호출해 탐색 로직만
독립적으로 검증한다(SCHEMABRIDGE_SKIP_LLM_TESTS=1로 LLM 필요한 부분만 건너뛸 수 있음).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.data_loader import load_schema
from src.discovery import DISCOVERY_CONFIDENCE_GAP_THRESHOLD, DISCOVERY_LOW_CONFIDENCE_THRESHOLD, enumerate_all_columns
from src.graph import (
    code_match_node,
    discover_as_is_node,
    discover_to_be_node,
    filter_by_type_reverse_node,
    filter_node,
    generate_rationale_node,
    generate_reverse_rationale_node,
    handle_exception_node,
    infer_secondary_evidence_node,
    infer_secondary_evidence_reverse_node,
    judge_and_rank_node,
    lookup_node,
    lookup_reverse_node,
)

PASS, FAIL = "PASS", "FAIL"


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"[{PASS if condition else FAIL}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def test_enumerate_all_columns() -> bool:
    print("\n=== enumerate_all_columns (결정적, API 키 불필요) ===")
    ok = True
    schema = load_schema()

    as_is_cols = enumerate_all_columns(schema, "AS-IS")
    ok &= check("AS-IS 전체 컬럼 목록이 비어있지 않음", len(as_is_cols) > 0)
    ok &= check(
        "타입 정보가 없는 컬럼(DIV_WHT.wht_amt)은 제외됨",
        not any(c["table"] == "DIV_WHT" and c["column"] == "wht_amt" for c in as_is_cols),
    )
    ok &= check(
        "타입이 있는 컬럼(LBR_WHT.pay_amt)은 포함됨",
        any(c["table"] == "LBR_WHT" and c["column"] == "pay_amt" for c in as_is_cols),
    )

    to_be_cols = enumerate_all_columns(schema, "TO-BE")
    ok &= check(
        "TO-BE 전체 컬럼 목록에 새 탐색용 컬럼(lbr_pay_amt) 포함",
        any(c["table"] == "ACC_WHT_AGG" and c["column"] == "lbr_pay_amt" for c in to_be_cols),
    )
    return ok


def test_forward_discovery_success() -> bool:
    print("\n=== 정방향 탐색 성공: ACC_WHT_AGG.lbr_pay_amt -> LBR_WHT.pay_amt (LLM, 키 필요) ===")
    col = "ACC_WHT_AGG.lbr_pay_amt"
    state = {"to_be_column": col, "as_is_column": None}
    state.update(lookup_node(state))
    ok = check("등록 없음 -> discover_as_is_candidates로 라우팅", state["route"] == "discover_as_is_candidates")
    state.update(discover_as_is_node(state))
    state.update(filter_node(state))
    state.update(code_match_node(state))
    if state["route"] == "infer_secondary_evidence":
        state.update(infer_secondary_evidence_node(state))
    state.update(judge_and_rank_node(state))
    print("  judge_and_rank:", state["judged_status"], "gap=", state["confidence_gap"])
    ok &= check("엄격한 기준으로도 confirmed", state["judged_status"] == "confirmed", str(state["judged_status"]))
    if state["route"] == "generate_rationale":
        state.update(generate_rationale_node(state))
        winner = state["judged_winner"]
        ok &= check(
            "실제로 LBR_WHT.pay_amt를 찾아냄",
            winner["table"] == "LBR_WHT" and winner["column"] == "pay_amt",
            str(winner),
        )
        ok &= check("탐색 결과임이 근거 문장에 드러남", "탐색" in state["rationale"] or "매핑정의서" in state["rationale"])
    return ok


def test_forward_discovery_honest_failure() -> bool:
    print("\n=== 정방향 탐색이 정직하게 실패: ACC_WHT_AGG.data_quality_flag_cd (LLM, 키 필요) ===")
    col = "ACC_WHT_AGG.data_quality_flag_cd"
    state = {"to_be_column": col, "as_is_column": None}
    state.update(lookup_node(state))
    state.update(discover_as_is_node(state))
    state.update(filter_node(state))
    state.update(code_match_node(state))
    if state["route"] == "infer_secondary_evidence":
        state.update(infer_secondary_evidence_node(state))
    state.update(judge_and_rank_node(state))
    ok = check(
        "AS-IS에 실제 대응 원본이 없어 confirmed로 잘못 확정하지 않음",
        state["judged_status"] != "confirmed",
        str(state["judged_status"]),
    )
    ok &= check("되묻기 루프 없이 곧장 handle_exception으로", state["route"] == "handle_exception")
    if state["route"] == "handle_exception":
        final = handle_exception_node(state)
        ok &= check(
            "exception_status == discovery_inconclusive",
            final.get("exception_status") == "discovery_inconclusive",
            str(final),
        )
    return ok


def test_reverse_discovery_success() -> bool:
    print("\n=== 역방향 탐색 성공: INT_WHT.rate_cd -> ACC_WHT_AGG.tax_rate (LLM, 키 필요) ===")
    as_is_col = "INT_WHT.rate_cd"
    state = {"as_is_column": as_is_col, "to_be_column": None}
    state.update(lookup_reverse_node(state))
    ok = check("등록 없음(컬럼은 실존) -> discover_to_be_candidates로 라우팅", state["route"] == "discover_to_be_candidates")
    state.update(discover_to_be_node(state))
    state.update(filter_by_type_reverse_node(state))
    state.update(code_match_node(state))
    if state["route"] == "infer_secondary_evidence_reverse":
        state.update(infer_secondary_evidence_reverse_node(state))
    state.update(judge_and_rank_node(state))
    print("  judge_and_rank:", state["judged_status"], "gap=", state["confidence_gap"])
    ok &= check("엄격한 기준으로도 confirmed", state["judged_status"] == "confirmed", str(state["judged_status"]))
    if state["route"] == "generate_reverse_rationale":
        state.update(generate_reverse_rationale_node(state))
        ok &= check(
            "실제로 ACC_WHT_AGG.tax_rate를 찾아냄",
            state.get("to_be_column") == "ACC_WHT_AGG.tax_rate",
            str(state.get("to_be_column")),
        )
    return ok


def test_reverse_discovery_honest_failure() -> bool:
    print("\n=== 역방향 탐색이 정직하게 실패: ETC_INCOME.pay_dt (LLM, 키 필요) ===")
    # SC-002 전용 TO-BE 테이블 3개(ACC_WHT_AGG/ACC_ETC_INCOME_AGG/ACC_BIZ_INCOME_AGG)가
    # pay_dt를 완전히 동일한 설명("지급일자")으로 정의해서 구조적으로 진짜 동점(gap=0.0)이
    # 나는 케이스 — 경계값 근처(0.7~0.8대)라 LLM 호출마다 흔들리는 케이스보다 훨씬 안정적으로
    # 재현되는 "확신 없음" 시나리오다.
    as_is_col = "ETC_INCOME.pay_dt"
    state = {"as_is_column": as_is_col, "to_be_column": None}
    state.update(lookup_reverse_node(state))
    if state["route"] != "discover_to_be_candidates":
        return check("discover_to_be_candidates로 라우팅", False, str(state))
    state.update(discover_to_be_node(state))
    state.update(filter_by_type_reverse_node(state))
    state.update(code_match_node(state))
    if state["route"] == "infer_secondary_evidence_reverse":
        state.update(infer_secondary_evidence_reverse_node(state))
    state.update(judge_and_rank_node(state))
    print("  judge_and_rank:", state["judged_status"], "gap=", state["confidence_gap"])
    ok = check(
        "TO-BE 3개 테이블이 동일 정의라 confidence_gap=0 -> confirmed로 확정하지 않음",
        state["judged_status"] != "confirmed",
        str(state["judged_status"]),
    )
    ok &= check("되묻기 루프 없이 곧장 handle_exception으로", state["route"] == "handle_exception")
    return ok


def test_strict_thresholds_are_stricter() -> bool:
    print("\n=== 탐색 전용 임계값이 등록된 매핑보다 엄격함 (결정적, API 키 불필요) ===")
    ok = True
    ok &= check(
        "DISCOVERY_LOW_CONFIDENCE_THRESHOLD > 0.5(등록된 매핑 기준)",
        DISCOVERY_LOW_CONFIDENCE_THRESHOLD > 0.5,
        str(DISCOVERY_LOW_CONFIDENCE_THRESHOLD),
    )
    ok &= check(
        "DISCOVERY_CONFIDENCE_GAP_THRESHOLD > 0.10(등록된 매핑 기준)",
        DISCOVERY_CONFIDENCE_GAP_THRESHOLD > 0.10,
        str(DISCOVERY_CONFIDENCE_GAP_THRESHOLD),
    )
    return ok


def main() -> None:
    results = [test_enumerate_all_columns(), test_strict_thresholds_are_stricter()]

    if os.environ.get("SCHEMABRIDGE_SKIP_LLM_TESTS"):
        print("\n(SCHEMABRIDGE_SKIP_LLM_TESTS 설정됨 — LLM 필요 테스트 건너뜀)")
    else:
        results.append(test_forward_discovery_success())
        results.append(test_forward_discovery_honest_failure())
        results.append(test_reverse_discovery_success())
        results.append(test_reverse_discovery_honest_failure())

    print("\n" + ("전체 통과" if all(results) else "일부 실패"))


if __name__ == "__main__":
    main()
