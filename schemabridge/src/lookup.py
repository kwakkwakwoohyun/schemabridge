"""
Node: lookup_mapping_candidates

매핑정의서를 정확 조회(Exact Lookup)한다. 임베딩/유사도 검색이 아니라
to_be_column 문자열과 완전히 일치하는 항목을 딕셔너리에서 찾는 것 뿐이다.

버전 불일치(Version-mismatch) 감지도 여기서 함께 수행한다:
매핑정의서가 가리키는 AS-IS 테이블/컬럼이 현재 스키마에 실제로 존재하는지 확인한다.
"""

from src.data_loader import get_column_info, load_mapping_definition, load_schema


def lookup_mapping_candidates(to_be_column: str) -> dict:
    # data/mapping_definition.json(매핑정의서) 파일을 dict로 반환
    mapping_def = load_mapping_definition()

    # data/schema.json(TO-BE/AS-IS 테이블 스키마 전체)을 읽어서 dict로 반환
    schema = load_schema()

    source_version = mapping_def.get("source_version")

    entry = next(
        (e for e in mapping_def["entries"] if e["to_be_column"] == to_be_column),
        None,
    )

    if entry is None:
        return {
            "to_be_column": to_be_column,
            "candidates": [],
            "source_version": source_version,
            "status_hint": "no_match",  # 후보 자체(사용자 입력)가 매핑정의서에 없음
        }

    candidates = []
    version_mismatch_found = False
    for c in entry["candidates"]:
        col_info = get_column_info(schema, "AS-IS", c["table"], c["column"])
        if col_info is None:
            # 매핑정의서는 이 컬럼을 가리키지만 현재 AS-IS 스키마엔 없음 -> 버전 불일치
            version_mismatch_found = True
            continue
        candidates.append({
            "table": c["table"],
            "column": c["column"],
            "type": col_info.get("type"),
            "description": col_info.get("description"),
            "sample_data": col_info.get("sample_data"),
            "fk": col_info.get("fk"),
        })

    status_hint = None
    # 매핑정의서는 있는데 스키마엔 없으면 -> 스키마가 바꼈는데 매핑정의서가 최신화 안되있는 경우
    if version_mismatch_found and not candidates:
        status_hint = "version_mismatch"
    # 매핑정의서에 등록돼 있는데, 거기 달린 후보 리스트가 텅 비어있는 경우(데이터 이상 케이스를 방어하는 코드)
    elif not candidates:
        status_hint = "no_match"

    return {
        "to_be_column": to_be_column,
        "candidates": candidates,
        "source_version": source_version,
        "status_hint": status_hint,
    }
