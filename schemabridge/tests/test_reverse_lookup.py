"""
SC-001 역방향(AS-IS → TO-BE) 조회 검증 스크립트 (2026-09-08 추가).

lookup_reverse_mapping(src/lookup.py)은 매핑정의서 역인덱스 조회라 결정적이고
API 키 없이 바로 검증 가능하다. classify_intent의 방향 판별(자연어 입력이 정방향/
역방향/방향불명 중 무엇으로 분류되는지)과 그래프 전체 체인은 LLM 호출이 필요해서
.env 설정이 있어야 통과한다(다른 테스트 스크립트와 동일한 전제).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.graph import build_graph
from src.intent import classify_intent
from src.lookup import lookup_reverse_mapping

PASS, FAIL = "PASS", "FAIL"


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"[{PASS if condition else FAIL}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def test_lookup_reverse_mapping_deterministic() -> bool:
    print("\n=== lookup_reverse_mapping (결정적, API 키 불필요) ===")
    ok = True

    r1 = lookup_reverse_mapping("LBR_WHT", "wht_amt")
    ok &= check(
        "LBR_WHT.wht_amt -> ACC_WHT_AGG.wht_tax_amt 단일 확정",
        [m["to_be_column"] for m in r1["matches"]] == ["ACC_WHT_AGG.wht_tax_amt"],
        str(r1),
    )

    r2 = lookup_reverse_mapping("BIZ_INCOME", "income_cd")
    ok &= check(
        "BIZ_INCOME.income_cd -> ACC_WHT_AGG.income_type_cd 단일 확정",
        [m["to_be_column"] for m in r2["matches"]] == ["ACC_WHT_AGG.income_type_cd"],
        str(r2),
    )

    r3 = lookup_reverse_mapping("DIV_WHT", "acct_nm")
    ok &= check(
        "DIV_WHT.acct_nm -> ACC_WHT_AGG.div_payee_nm 단일 확정",
        [m["to_be_column"] for m in r3["matches"]] == ["ACC_WHT_AGG.div_payee_nm"],
        str(r3),
    )

    # 스키마에는 있지만 매핑정의서 어느 entry의 candidates에도 등록되지 않은 AS-IS 컬럼
    r4 = lookup_reverse_mapping("LBR_WHT", "pay_amt")
    ok &= check(
        "매핑정의서에 등록 안 된 AS-IS 컬럼 -> no_match(matches 0건)",
        r4["matches"] == [] and r4["status_hint"] == "no_match",
        str(r4),
    )

    # 현재 스키마 자체에 없는 AS-IS 컬럼
    r5 = lookup_reverse_mapping("LBR_WHT", "no_such_column")
    ok &= check(
        "현재 AS-IS 스키마에 없는 컬럼 -> no_match",
        r5["matches"] == [] and r5["status_hint"] == "no_match",
        str(r5),
    )

    return ok


def test_classify_intent_direction() -> bool:
    print("\n=== classify_intent 방향 판별 (LLM, Azure OpenAI 키 필요) ===")
    ok = True

    r1 = classify_intent("LBR_WHT.wht_amt가 TO-BE 어디로 매핑돼?")
    ok &= check(
        "역방향 식별자 질문 -> SC-001, as_is_column 채워짐",
        r1["intent"] == "SC-001" and r1["as_is_column"] == "LBR_WHT.wht_amt" and not r1["to_be_column"],
        str(r1),
    )

    r2 = classify_intent("이 AS-IS 컬럼(BIZ_INCOME.income_cd) 매핑되는 TO-BE 컬럼 찾아줘")
    ok &= check(
        "역방향 자연어 질문 -> SC-001, as_is_column 정규화됨",
        r2["intent"] == "SC-001" and r2["as_is_column"] == "BIZ_INCOME.income_cd" and not r2["to_be_column"],
        str(r2),
    )

    # 정방향은 여전히 정방향으로 분류돼야 한다(회귀 확인)
    r3 = classify_intent("ACC_WHT_AGG.wht_tax_amt")
    ok &= check(
        "정방향 식별자 질문 -> SC-001, to_be_column 채워짐(회귀 확인)",
        r3["intent"] == "SC-001" and r3["to_be_column"] == "ACC_WHT_AGG.wht_tax_amt" and not r3["as_is_column"],
        str(r3),
    )

    return ok


def test_reverse_graph_end_to_end() -> bool:
    print("\n=== 역방향 SC-001 전체 그래프 체인 (LLM, Azure OpenAI 키 필요) ===")
    app = build_graph()
    result = app.invoke({"user_request": "LBR_WHT.wht_amt가 TO-BE 어디로 매핑돼?"})
    final = result["final_answer"]
    print(final)

    ok = True
    ok &= check("status == confirmed", final.get("status") == "confirmed", str(final))
    ok &= check("direction == as_is_to_to_be", final.get("direction") == "as_is_to_to_be", str(final))
    ok &= check(
        "to_be_column == ACC_WHT_AGG.wht_tax_amt",
        final.get("to_be_column") == "ACC_WHT_AGG.wht_tax_amt",
        str(final),
    )
    ok &= check(
        "table/column == LBR_WHT.wht_amt(사용자가 물어본 AS-IS 컬럼)",
        final.get("table") == "LBR_WHT" and final.get("column") == "wht_amt",
        str(final),
    )
    return ok


def main() -> None:
    results = [test_lookup_reverse_mapping_deterministic()]

    if os.environ.get("SCHEMABRIDGE_SKIP_LLM_TESTS"):
        print("\n(SCHEMABRIDGE_SKIP_LLM_TESTS 설정됨 — LLM 필요 테스트 건너뜀)")
    else:
        results.append(test_classify_intent_direction())
        results.append(test_reverse_graph_end_to_end())

    print("\n" + ("전체 통과" if all(results) else "일부 실패"))


if __name__ == "__main__":
    main()
