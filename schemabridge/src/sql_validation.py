"""
Node: validate_readonly

generate_sql이 만든 SQL을 실제로 실행하기 전에 Read-only인지 결정적으로 검증한다.
LLM 호출 없음 — "SELECT 이외의 쓰기 쿼리는 절대 생성하지 않는다"(4주차_완료.md
제약 사항)를 사람이 아니라 코드가 기계적으로 보장해야 하는 지점이라 일부러
결정적으로 짰다. execute_sql 쪽에서도 `PRAGMA query_only = ON`으로 한 번 더
방어하지만(defense in depth), 최종 실행 여부를 가르는 판정은 여기서 끝난다.
"""

import re

# 하나라도 포함되면 즉시 거부. 대소문자 무시, 단어 경계 기준으로 매칭.
_FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "ATTACH", "DETACH", "PRAGMA", "GRANT", "REVOKE", "VACUUM",
)


def validate_readonly(sql: str) -> dict:
    stripped = sql.strip().rstrip(";").strip()

    if not stripped:
        return {"is_valid": False, "reason": "빈 SQL"}

    # 세미콜론으로 구분된 여러 문장(예: "SELECT 1; DROP TABLE x") 금지.
    if ";" in stripped:
        return {"is_valid": False, "reason": "세미콜론으로 구분된 다중 문장은 허용하지 않음"}

    first_word = re.match(r"^\s*(\w+)", stripped)
    first_word = first_word.group(1).upper() if first_word else ""
    if first_word not in ("SELECT", "WITH"):
        return {"is_valid": False, "reason": f"SELECT/WITH로 시작해야 함(입력: '{first_word}')"}

    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", stripped, re.IGNORECASE):
            return {"is_valid": False, "reason": f"금지된 키워드 포함: {keyword}"}

    return {"is_valid": True, "reason": "SELECT/WITH 단일 문장, 금지 키워드 없음"}
