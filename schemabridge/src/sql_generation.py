"""
Node: generate_sql (SC-002)

search_schema가 넘긴 schema_chunks(고정 신고서 항목 + 조회 대상 TO-BE 테이블
안내)를 근거로 SQLite에서 바로 실행 가능한 SELECT 전용 SQL을 만든다. 자유 형식
text-to-SQL이 아니라 "이미 정해진 조회 대상을 SQL 문법으로 옮기는" 좁은 역할이라,
그만큼 프롬프트도 스키마 컨텍스트에 있는 대로만 쓰라고 강하게 제약한다.

**2026-09-07 변경**: 예전엔 AS-IS 소스 테이블들을 UNION ALL로 직접 합치는
쿼리를 만들었지만, 이제 소스 자체가 이미 마이그레이션된 TO-BE 테이블들이다
(배당소득=ACC_WHT_AGG, 기타소득=ACC_ETC_INCOME_AGG, 사업소득=ACC_BIZ_INCOME_AGG
— src/schema_search.py docstring 참고). 조인은 여전히 필요 없다(각 테이블이
이미 리포트에 필요한 컬럼을 다 갖고 있음).

**2026-09-07 추가 수정 — 무조건 3개 전부 UNION ALL은 아니다.** 처음엔 안내된
소스 테이블을 항상 전부 UNION ALL하도록 프롬프트를 짰는데, 사용자가 "사용자의
프롬프트에 따라 쿼리를 만들어야지 무조건 UNION ALL은 아니다"라고 지적해서
바로잡았다. search_schema는 이 리포트에서 쓸 수 있는 소스 후보 전체(검증까지
끝난 것)를 넘길 뿐이고, 그중 실제로 몇 개를 쓸지는 사용자 요청을 보고
generate_sql이 판단한다 — 요청이 특정 소득유형(예: "사업소득만")을 지정하면
그 소스 하나만, 지정이 없으면 안내된 소스 전체를 쓴다. 2개 이상을 쓸 때만
UNION ALL이 필요하다.

**2026-09-07 추가 — 기간(WHERE) 조건.** 처음엔 report_columns에 pay_dt(지급일자)가
없어서 생성되는 쿼리에 WHERE절 자체가 나올 여지가 없었다 — 신고서는 원래 항상
특정 분기/연도 단위인데(3주차_완료.md 입력 예시도 "2026년 2분기 ... 신고서용
집계 데이터"), 그 기간 조건을 걸 방법이 없었던 것. 사용자가 지적해서
report_columns에 pay_dt를 추가하고(data/join_rules.json), 데모 데이터도 여러
분기(2025 Q4~2026 Q2)에 걸치도록 늘렸다(data/setup_demo_db.py). pay_dt는
'YYYY-MM-DD' 형식 TEXT라 문자열 비교로 바로 날짜 범위를 걸 수 있다. 사용자
요청이 특정 기간(예: "2026년 1분기", "지난 분기", "이번 달")을 지정하면 pay_dt에
대한 WHERE 조건(BETWEEN 또는 >=/<=)을 추가하고, 기간을 지정하지 않은 요청이면
WHERE절 없이 전체 기간을 조회한다. "이번 분기/이번 달/최근" 같은 상대 표현을
해석할 수 있도록 오늘 날짜를 프롬프트에 같이 준다.

error_feedback이 있으면(=execute_sql이 실패해서 self_correct 루프로 되돌아온 경우)
이전 에러 메시지를 프롬프트에 그대로 포함해 같은 실수를 피하도록 한다.
thought_process는 내부 로그용(Langfuse 트레이스 등)이지 사용자 노출용이
아니다 — 사용자에게 보여줄 설명은 이 SQL이 실행된 뒤 별도로 만든다.
"""

from datetime import date

from src.llm_client import chat_completion_json

SQL_SCHEMA = {
    "name": "generated_sql",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "thought_process": {"type": "string"},
        },
        "required": ["sql", "thought_process"],
        "additionalProperties": False,
    },
}


def generate_sql(schema_chunks: list[str], query: str, error_feedback: str | None = None) -> dict:
    context = "\n".join(f"- {c}" for c in schema_chunks)
    error_note = (
        f"\n\n이전 시도가 다음 에러로 실패했다. 같은 원인을 피해서 다시 작성해라: {error_feedback}"
        if error_feedback
        else ""
    )

    prompt = (
        f"오늘 날짜: {date.today().isoformat()}\n"
        f"사용자 요청: \"{query}\"\n\n"
        f"아래는 이미 확정된 스키마·조회 대상이다. 여기 나온 테이블/컬럼만 사용해라 — "
        f"목록에 없는 테이블/컬럼을 지어내지 마라:\n{context}{error_note}\n\n"
        "SQLite에서 바로 실행 가능한 SELECT 전용 쿼리를 하나만 작성해라. 안내된 TO-BE 소스 "
        "테이블 중 사용자 요청에 맞는 것만 골라 써라 — 요청이 특정 소득유형(예: \"사업소득만\", "
        "\"배당소득 빼고\")을 지정하면 해당 테이블만 쓰고, 특정 소득유형을 지정하지 않은 일반적인 "
        "요청이면 안내된 소스 테이블 전체를 쓴다. 2개 이상의 테이블을 쓸 때만 UNION ALL로 세로로 "
        "합쳐라(조인 아님, 각 테이블이 이미 필요한 컬럼을 다 갖고 있음).\n\n"
        "요청에 특정 기간(예: \"2026년 1분기\", \"지난 분기\", \"이번 달\", \"10월 이후\")이 "
        "언급되어 있으면 pay_dt('YYYY-MM-DD' 형식 TEXT)에 대한 WHERE 조건(BETWEEN 또는 "
        ">=/<= 문자열 비교)을 추가해라 — 위의 오늘 날짜를 기준으로 상대 표현(이번/지난/최근)을 "
        "해석해라. 기간이 언급되어 있지 않으면 WHERE절 없이 전체 기간을 조회해라.\n\n"
        "각 컬럼에 고정 신고서 항목의 field 이름으로 별칭(AS)을 붙여라. SELECT 이외의 문장"
        "(INSERT/UPDATE/DELETE/DROP 등)은 절대 만들지 마라."
    )

    result = chat_completion_json(
        messages=[
            {
                "role": "system",
                "content": "너는 확정된 조인 규칙을 SQL 문법으로 옮기는 어시스턴트다. "
                "스키마에 없는 테이블/컬럼을 지어내지 않고, SELECT 이외의 쓰기 쿼리는 절대 생성하지 않는다.",
            },
            {"role": "user", "content": prompt},
        ],
        schema=SQL_SCHEMA,
    )
    return result
