"""
Node: filter_by_type

필수조건: TO-BE 컬럼과 데이터 타입이 다른 후보는 제외한다.
타입 정보가 없는(None) 후보는 "제외"가 아니라 "판정 불가"로 별도 표시한다.
이 경우가 바로 4주차에서 정의한 Insufficient-Metadata의 트리거 중 하나다.
"""

from src.data_loader import get_column_info, load_schema


def filter_by_type(to_be_column: str, candidates: list[dict]) -> dict:
    to_be_table, to_be_col = to_be_column.split(".")
    schema = load_schema()
    to_be_info = get_column_info(schema, "TO-BE", to_be_table, to_be_col)
    to_be_type = to_be_info.get("type") if to_be_info else None

    filtered = []
    excluded = []
    unknown_type = []

    for c in candidates:
        cand_type = c.get("type")
        if cand_type is None:
            # 타입 정보 자체가 없음 -> 필수조건을 확정적으로 적용 불가
            unknown_type.append({"candidate": c, "reason": "타입 정보 없음(UNKNOWN)"})
            filtered.append(c)  # 배제하지 않고 통과는 시키되 플래그를 남김
        elif cand_type == to_be_type:
            filtered.append(c)
        else:
            excluded.append({"candidate": c, "reason": f"타입 불일치: TO-BE={to_be_type} vs AS-IS={cand_type}"})

    return {
        "to_be_type": to_be_type,
        "filtered": filtered,
        "excluded": excluded,
        "unknown_type": unknown_type,  # 비어있지 않으면 Insufficient-Metadata 후보
    }
