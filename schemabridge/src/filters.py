"""
Node: filter_by_type

필수조건: 기준(anchor) 컬럼과 데이터 타입이 다른 후보는 제외한다.
타입 정보가 없는(None) 후보는 "제외"가 아니라 "판정 불가"로 별도 표시한다.
이 경우가 바로 4주차에서 정의한 Insufficient-Metadata의 트리거 중 하나다.

anchor_side(2026-09-09 추가, 기본값 "TO-BE" — 기존 호출부와 100% 동일하게 동작):
기준 컬럼이 TO-BE 쪽인지 AS-IS 쪽인지. SC-001 정방향(TO-BE 컬럼 기준 AS-IS 후보 필터링)이
기존 기본값 그대로고, 후보 자동 탐색(src/discovery.py)의 역방향 탐색(AS-IS 컬럼 기준으로
TO-BE 전체를 후보로 필터링)만 "AS-IS"를 명시적으로 넘긴다.
"""

from src.data_loader import get_column_info, load_schema


def filter_by_type(anchor_column: str, candidates: list[dict], anchor_side: str = "TO-BE") -> dict:
    anchor_table, anchor_col = anchor_column.split(".")
    schema = load_schema()
    anchor_info = get_column_info(schema, anchor_side, anchor_table, anchor_col)
    anchor_type = anchor_info.get("type") if anchor_info else None

    filtered = []
    excluded = []
    unknown_type = []

    for c in candidates:
        cand_type = c.get("type")
        if cand_type is None:
            # 타입 정보 자체가 없음 -> 필수조건을 확정적으로 적용 불가
            unknown_type.append({"candidate": c, "reason": "타입 정보 없음(UNKNOWN)"})
            filtered.append(c)  # 배제하지 않고 통과는 시키되 플래그를 남김
        elif cand_type == anchor_type:
            filtered.append(c)
        else:
            excluded.append({"candidate": c, "reason": f"타입 불일치: 기준={anchor_type} vs 후보={cand_type}"})

    return {
        "to_be_type": anchor_type,  # 키 이름은 하위 호환 위해 유지(정방향 기준 이름), 실제로는 anchor_type
        "filtered": filtered,
        "excluded": excluded,
        "unknown_type": unknown_type,  # 비어있지 않으면 Insufficient-Metadata 후보
    }
