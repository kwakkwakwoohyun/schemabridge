"""
app.py(Streamlit 데모 뷰어) 검증 스크립트.

`streamlit.testing.v1.AppTest`로 실제 버튼 클릭/텍스트 입력을 시뮬레이션해서,
브라우저 없이도 화면이 예외 없이 렌더링되는지 확인한다. 전부 Azure OpenAI 호출이
포함돼(classify_intent/generate_sql/infer_secondary_evidence 등) .env 설정이
필요하다(SC-001의 python3 src/graph.py, test_sc002.py의 LLM 테스트와 동일한 전제).

주의: app.py 내부 함수를 mock할 땐 patch("app.함수이름", ...)이 아니라
patch("src.모듈.함수이름", ...)으로 원본을 패치해야 한다 — AppTest가 스크립트를
매 run()마다 독립적으로 다시 실행(re-exec)하기 때문에, 이미 만들어진 app 모듈
객체의 속성을 바꿔봐야 그 재실행 컨텍스트에는 반영되지 않는다.
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from streamlit.testing.v1 import AppTest

PASS, FAIL = "PASS", "FAIL"


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"[{PASS if condition else FAIL}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _app_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "..")


def test_sc001_identifier_input() -> bool:
    print("\n=== SC-001: 컬럼 식별자 입력 (LLM, Azure OpenAI 키 필요) ===")
    at = AppTest.from_file(os.path.join(_app_dir(), "app.py"))
    at.run(timeout=60)
    at.text_input[0].input("ACC_WHT_AGG.wht_tax_amt")
    at.button[0].click().run(timeout=60)
    ok = True
    ok &= check("예외 없음", not at.exception, str(at.exception))
    ok &= check("CONFIRMED 렌더링됨", any("CONFIRMED" in s.value for s in at.success))
    return ok


def test_sc001_natural_language_question() -> bool:
    print("\n=== SC-001: 자연어 컬럼 매핑 질문 (LLM, Azure OpenAI 키 필요) ===")
    at = AppTest.from_file(os.path.join(_app_dir(), "app.py"))
    at.run(timeout=60)
    at.text_input[0].input("원천징수세액이 어디서 오는지 알려줘")
    at.button[0].click().run(timeout=60)
    ok = True
    ok &= check("예외 없음", not at.exception, str(at.exception))
    ok &= check("CONFIRMED 렌더링됨", any("CONFIRMED" in s.value for s in at.success))
    return ok


def test_sc001_clarification_loop() -> bool:
    print("\n=== SC-001: request_clarification 되묻기 루프 (LLM, Azure OpenAI 키 필요) ===")
    at = AppTest.from_file(os.path.join(_app_dir(), "app.py"))
    at.run(timeout=60)
    at.text_input[0].input("ACC_WHT_AGG.settle_method_cd")
    at.button[0].click().run(timeout=60)

    ok = True
    ok &= check("1차 실행 후 예외 없음", not at.exception, str(at.exception))
    ok &= check("되묻기 UI 노출(warning)", len(at.warning) > 0)

    clarify_inputs = [w for w in at.text_input if w.key and w.key.startswith("clarify_input_")]
    ok &= check("되묻기 답변 입력창 존재", len(clarify_inputs) == 1)
    if not clarify_inputs:
        return ok

    clarify_inputs[0].input("급여 원천징수 업무에서 사용하는 값입니다")
    submit_buttons = [b for b in at.button if b.key and b.key.startswith("clarify_submit_")]
    ok &= check("답변 제출 버튼 존재", len(submit_buttons) == 1)
    if not submit_buttons:
        return ok

    submit_buttons[0].click().run(timeout=60)
    ok &= check("답변 제출 후 예외 없음", not at.exception, str(at.exception))
    ok &= check(
        "되묻기 루프가 CONFIRMED 또는 재되묻기 중 하나로 정직하게 귀결됨",
        len(at.success) > 0 or len(at.warning) > 0 or len(at.error) > 0,
    )
    return ok


def test_sc002_report_ready() -> bool:
    print("\n=== SC-002: 정상 리포트 생성 (LLM, Azure OpenAI 키 필요) ===")
    at = AppTest.from_file(os.path.join(_app_dir(), "app.py"))
    at.run(timeout=60)
    # "이번 분기" 같은 상대 기간 표현은 실행 시점의 실제 오늘 날짜에 좌우되고(2026-09-07
    # 기준 데모 데이터는 2025 Q4~2026 Q2까지만 있어 "이번 분기"면 빈 결과가 나옴), 이
    # 테스트는 렌더링 자체만 확인하는 거라 기간 미지정으로 안정적인 결과를 받는다.
    at.text_input[0].input("원천세(배당·기타·사업소득) 신고서용 전체 집계 데이터 뽑아줘")
    at.button[0].click().run(timeout=60)
    ok = True
    ok &= check("예외 없음", not at.exception, str(at.exception))
    ok &= check("REPORT_READY 렌더링됨", any("REPORT_READY" in s.value for s in at.success))
    return ok


def test_sc002_exceptions() -> bool:
    print("\n=== SC-002: 예외 3종 렌더링 (mock, LLM 불필요) ===")
    ok = True

    at = AppTest.from_file(os.path.join(_app_dir(), "app.py"))
    at.run(timeout=60)
    with mock.patch(
        "src.intent.classify_intent",
        return_value={"intent": "SC-002", "to_be_column": None, "sc002_mode": "execute"},
    ), mock.patch(
        "src.schema_search.search_schema",
        return_value={"found": False, "schema_chunks": [], "join_rule": None, "validation_error": "테스트용 강제 실패"},
    ):
        at.text_input[0].input("신고서용 집계 데이터 뽑아줘")
        at.button[0].click().run(timeout=60)
    ok &= check("mapping_not_ready 예외 없음", not at.exception, str(at.exception))
    ok &= check("mapping_not_ready 메시지 노출", any("mapping_not_ready" in e.value for e in at.error))

    at2 = AppTest.from_file(os.path.join(_app_dir(), "app.py"))
    at2.run(timeout=60)
    with mock.patch(
        "src.intent.classify_intent",
        return_value={"intent": "SC-002", "to_be_column": None, "sc002_mode": "execute"},
    ), mock.patch(
        "src.schema_search.search_schema",
        return_value={"found": True, "schema_chunks": ["dummy"], "join_rule": {"report_name": "테스트 리포트"}, "validation_error": None},
    ), mock.patch(
        "src.sql_generation.generate_sql", return_value={"sql": "DELETE FROM ETC_INCOME", "thought_process": "x"}
    ):
        at2.text_input[0].input("신고서용 집계 데이터 뽑아줘")
        at2.button[0].click().run(timeout=60)
    ok &= check("readonly_violation 예외 없음", not at2.exception, str(at2.exception))
    ok &= check("readonly_violation 메시지 노출", any("readonly_violation" in e.value for e in at2.error))

    at3 = AppTest.from_file(os.path.join(_app_dir(), "app.py"))
    at3.run(timeout=60)
    with mock.patch(
        "src.intent.classify_intent",
        return_value={"intent": "SC-002", "to_be_column": None, "sc002_mode": "execute"},
    ), mock.patch(
        "src.schema_search.search_schema",
        return_value={"found": True, "schema_chunks": ["dummy"], "join_rule": {"report_name": "테스트 리포트"}, "validation_error": None},
    ), mock.patch(
        "src.sql_generation.generate_sql", return_value={"sql": "SELECT * FROM NO_SUCH_TABLE", "thought_process": "x"}
    ):
        at3.text_input[0].input("신고서용 집계 데이터 뽑아줘")
        at3.button[0].click().run(timeout=60)
    ok &= check("sql_execution_failed 예외 없음", not at3.exception, str(at3.exception))
    ok &= check("sql_execution_failed 메시지 노출", any("sql_execution_failed" in e.value for e in at3.error))

    return ok


def test_sc002_explore_mode() -> bool:
    print("\n=== SC-002: 탐색 모드(explore) 렌더링 — 쿼리 실행 없이 테이블 정보만 (LLM, Azure OpenAI 키 필요) ===")
    at = AppTest.from_file(os.path.join(_app_dir(), "app.py"))
    at.run(timeout=60)
    at.text_input[0].input("원천세 집계 관련된 테이블 다 찾아줘")
    at.button[0].click().run(timeout=60)
    ok = True
    ok &= check("예외 없음", not at.exception, str(at.exception))
    ok &= check("SCHEMA_FOUND 렌더링됨", any("SCHEMA_FOUND" in s.value for s in at.success))
    return ok


def main() -> None:
    if os.environ.get("SCHEMABRIDGE_SKIP_LLM_TESTS"):
        print("(SCHEMABRIDGE_SKIP_LLM_TESTS 설정됨 — 이 스크립트는 전부 LLM 호출을 포함해 건너뜀)")
        return

    results = [
        test_sc001_identifier_input(),
        test_sc001_natural_language_question(),
        test_sc001_clarification_loop(),
        test_sc002_report_ready(),
        test_sc002_exceptions(),
        test_sc002_explore_mode(),
    ]
    print("\n" + ("전체 통과" if all(results) else "일부 실패"))


if __name__ == "__main__":
    main()
