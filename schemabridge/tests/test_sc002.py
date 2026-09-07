"""
SC-002(신고서용 집계 쿼리 생성) 검증 스크립트.

앞부분(결정적 로직: validate_readonly, DB 시딩, search_schema)은 API 키 없이
바로 실행 가능하다. classify_intent/generate_sql이 포함된 뒷부분은 Azure OpenAI
LLM 호출이 필요해서 .env 설정이 있어야 통과한다(SC-001의 python3 src/graph.py와
동일한 전제).
"""

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.setup_demo_db import ensure_demo_db
from src.graph import build_graph
from src.intent import classify_intent
from src.schema_search import search_schema
from src.sql_validation import validate_readonly

PASS, FAIL = "PASS", "FAIL"


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"[{PASS if condition else FAIL}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def test_validate_readonly() -> bool:
    print("\n=== validate_readonly (결정적, API 키 불필요) ===")
    ok = True
    ok &= check("정상 SELECT 통과", validate_readonly("SELECT * FROM ACC_WHT_AGG")["is_valid"])
    ok &= check("WITH절 통과", validate_readonly("WITH x AS (SELECT 1) SELECT * FROM x")["is_valid"])
    ok &= check("DROP TABLE 차단", not validate_readonly("DROP TABLE ACC_WHT_AGG")["is_valid"])
    ok &= check("DELETE 차단", not validate_readonly("DELETE FROM ACC_WHT_AGG")["is_valid"])
    ok &= check(
        "세미콜론 다중 문장 차단",
        not validate_readonly("SELECT 1; DELETE FROM ACC_WHT_AGG")["is_valid"],
    )
    return ok


def test_demo_db_seeded() -> bool:
    print("\n=== data/setup_demo_db.py 시딩 (결정적, API 키 불필요) ===")
    import sqlite3

    db_path = ensure_demo_db()
    conn = sqlite3.connect(db_path)
    ok = True
    # 2026-09-07: 기간 WHERE 필터가 실제로 결과를 바꾸는 걸 보여주려고 테이블당
    # 5건(전부 2026 Q1)에서 12건(2025 Q4/2026 Q1/2026 Q2 각 4건)으로 늘림.
    for table, expected in (("ACC_WHT_AGG", 12), ("ACC_ETC_INCOME_AGG", 12), ("ACC_BIZ_INCOME_AGG", 12)):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        ok &= check(f"{table} 행 수 = {expected}", count == expected, f"실제: {count}")
    conn.close()
    return ok


def test_search_schema() -> bool:
    print("\n=== search_schema (결정적, API 키 불필요) ===")
    ok = True
    result = search_schema()
    ok &= check("정의된 리포트 조회 성공", result["found"])
    ok &= check("schema_chunks 비어있지 않음", len(result["schema_chunks"]) > 0)

    with mock.patch("src.schema_search.load_join_rules", return_value={"reports": []}):
        empty_result = search_schema()
    ok &= check("리포트 정의 없을 때 found=False", empty_result["found"] is False)
    return ok


def test_classify_intent() -> bool:
    print("\n=== classify_intent (LLM, Azure OpenAI 키 필요) ===")
    ok = True
    r1 = classify_intent("ACC_WHT_AGG.wht_tax_amt")
    ok &= check("컬럼명 식별자 입력 -> SC-001", r1["intent"] == "SC-001", str(r1))

    r2 = classify_intent("이번 분기 원천세 신고서용 집계 데이터 뽑아줘")
    ok &= check(
        "자연어 리포트 요청 -> SC-002 execute",
        r2["intent"] == "SC-002" and r2["sc002_mode"] == "execute",
        str(r2),
    )

    # 자연어 문장이어도 "컬럼 하나의 매핑"을 묻는 거면 여전히 SC-001이어야 한다
    # (겉모습이 아니라 의미로 분류하는지 확인 — 2026-09-05 수정)
    r3 = classify_intent("원천징수세액이 어디서 오는지 알려줘")
    ok &= check(
        "자연어 컬럼 매핑 질문 -> SC-001",
        r3["intent"] == "SC-001" and r3["to_be_column"] == "ACC_WHT_AGG.wht_tax_amt",
        str(r3),
    )

    # 실제 데이터 결과가 아니라 관련 테이블 정보만 원하는 탐색 요청 -> SC-002 explore
    # (2026-09-07 추가 — 처음엔 SC-002로 분류만 되면 무조건 쿼리까지 만들어 실행했는데,
    # 이런 탐색 요청까지 그렇게 처리하는 게 사용자 지적으로 드러나 sc002_mode를 추가함)
    r4 = classify_intent("원천세 집계 관련된 테이블 다 찾아줘")
    ok &= check(
        "탐색 요청 -> SC-002 explore",
        r4["intent"] == "SC-002" and r4["sc002_mode"] == "explore",
        str(r4),
    )
    return ok


def test_sc002_end_to_end() -> bool:
    print("\n=== SC-002 전체 체인, 기간 미지정 (LLM, Azure OpenAI 키 필요) ===")
    # 기간을 지정하지 않은 요청("이번 분기"처럼 상대 기간을 언급하면 그 자체가
    # WHERE 필터로 해석되므로 일부러 기간 표현을 빼서 "전체 조회" 경로를 검증함).
    app = build_graph()
    result = app.invoke({"user_request": "원천세(배당·기타·사업소득) 신고서용 전체 집계 데이터 뽑아줘"})
    final = result["final_answer"]
    print(json.dumps(final, ensure_ascii=False, indent=2))

    ok = True
    ok &= check("status == report_ready", final.get("status") == "report_ready", str(final.get("status")))
    if final.get("status") == "report_ready":
        ok &= check(
            "행 수 == 36 (ACC_WHT_AGG 12 + ACC_ETC_INCOME_AGG 12 + ACC_BIZ_INCOME_AGG 12)",
            len(final["rows"]) == 36,
            str(len(final["rows"])),
        )
        ok &= check(
            "컬럼 5개(pay_dt/payee_nm/income_type_cd/pay_amt/wht_tax_amt)",
            set(final["columns"]) == {"pay_dt", "payee_nm", "income_type_cd", "pay_amt", "wht_tax_amt"},
            str(final["columns"]),
        )
    return ok


def test_sc002_single_income_type() -> bool:
    print("\n=== SC-002 특정 소득유형만 요청, 기간 미지정 (LLM, Azure OpenAI 키 필요) ===")
    # 요청이 특정 소득유형만 지정하면 generate_sql이 소스 테이블 전체를 UNION ALL하지 않고
    # 해당 테이블만 써야 한다 (처음엔 항상 3개 전부를 합치도록 짜서 사용자가 지적한 부분 —
    # src/sql_generation.py docstring 참고).
    app = build_graph()
    result = app.invoke({"user_request": "원천세 신고서용 집계 데이터 중 사업소득만 뽑아줘"})
    final = result["final_answer"]
    print(json.dumps(final, ensure_ascii=False, indent=2))

    ok = True
    ok &= check("status == report_ready", final.get("status") == "report_ready", str(final.get("status")))
    if final.get("status") == "report_ready":
        ok &= check("행 수 == 12 (ACC_BIZ_INCOME_AGG만)", len(final["rows"]) == 12, str(len(final["rows"])))
        income_types = {row[final["columns"].index("income_type_cd")] for row in final["rows"]}
        ok &= check("소득구분코드가 전부 사업소득(B01/B02)", income_types <= {"B01", "B02"}, str(income_types))
    return ok


def test_sc002_period_filter() -> bool:
    print("\n=== SC-002 기간(분기) 필터 (LLM, Azure OpenAI 키 필요) ===")
    # 2026-09-07 추가 — pay_dt를 report_columns에 넣기 전까지는 WHERE절 자체가
    # 나올 수 없는 구조였다. 절대 기간 표현("2026년 1분기")으로 실제 데이터가
    # 좁혀지는지 검증한다(상대 표현 "이번 분기"는 실행 시점의 실제 오늘 날짜에
    # 좌우되므로 회귀 테스트에는 절대 표현을 씀).
    app = build_graph()
    result = app.invoke({"user_request": "2026년 1분기 원천세(배당·기타·사업소득) 신고서용 집계 데이터 뽑아줘"})
    final = result["final_answer"]
    print(json.dumps(final, ensure_ascii=False, indent=2))

    ok = True
    ok &= check("status == report_ready", final.get("status") == "report_ready", str(final.get("status")))
    if final.get("status") == "report_ready":
        ok &= check(
            "행 수 == 12 (테이블당 2026 Q1 4건 x 3테이블, 분기 필터로 좁혀짐)",
            len(final["rows"]) == 12,
            str(len(final["rows"])),
        )
        pay_dts = [row[final["columns"].index("pay_dt")] for row in final["rows"]]
        ok &= check(
            "전부 2026년 1분기(01~03월) 안에 있음",
            all("2026-01" <= d <= "2026-03-31" for d in pay_dts),
            str(pay_dts),
        )
    return ok


def test_sc002_explore_mode() -> bool:
    print("\n=== SC-002 탐색 모드(explore) — 쿼리 실행 없이 테이블 정보만 (LLM, Azure OpenAI 키 필요) ===")
    app = build_graph()
    result = app.invoke({"user_request": "원천세 집계 관련된 테이블 다 찾아줘"})
    final = result["final_answer"]
    print(json.dumps(final, ensure_ascii=False, indent=2))

    ok = True
    ok &= check("status == schema_found", final.get("status") == "schema_found", str(final.get("status")))
    ok &= check("sql 키가 없음(쿼리 생성/실행 안 함)", "sql" not in final, str(final.keys()))
    if final.get("status") == "schema_found":
        table_names = {s["to_be_table"] for s in final["sources"]}
        ok &= check(
            "3개 TO-BE 테이블 정보 포함",
            table_names == {"ACC_WHT_AGG", "ACC_ETC_INCOME_AGG", "ACC_BIZ_INCOME_AGG"},
            str(table_names),
        )
    return ok


def main() -> None:
    results = [
        test_validate_readonly(),
        test_demo_db_seeded(),
        test_search_schema(),
    ]

    if os.environ.get("SCHEMABRIDGE_SKIP_LLM_TESTS"):
        print("\n(SCHEMABRIDGE_SKIP_LLM_TESTS 설정됨 — LLM 필요 테스트 건너뜀)")
    else:
        results.append(test_classify_intent())
        results.append(test_sc002_end_to_end())
        results.append(test_sc002_single_income_type())
        results.append(test_sc002_period_filter())
        results.append(test_sc002_explore_mode())

    print("\n" + ("전체 통과" if all(results) else "일부 실패"))


if __name__ == "__main__":
    main()
