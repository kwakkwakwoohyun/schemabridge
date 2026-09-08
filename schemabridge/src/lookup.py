"""
Node: lookup_mapping_candidates / lookup_reverse_mapping

매핑정의서를 정확 조회(Exact Lookup)한다. 임베딩/유사도 검색이 아니라
문자열이 완전히 일치하는 항목을 딕셔너리에서 찾는 것 뿐이다.

- lookup_mapping_candidates: TO-BE 컬럼 -> AS-IS 후보(들). 여러 개가 나올 수 있어
  filter_by_type 이후 판정 파이프라인이 필요하다. 버전 불일치(Version-mismatch) 감지도
  여기서 함께 수행한다: 매핑정의서가 가리키는 AS-IS 테이블/컬럼이 현재 스키마에 실제로
  존재하는지 확인한다.
- lookup_reverse_mapping(2026-09-08 추가): AS-IS 컬럼 -> TO-BE 컬럼(들). entries를
  스캔해서 이 AS-IS 컬럼을 candidates로 갖는 entry를 찾는 역인덱스 조회다. AS-IS
  컬럼 하나가 둘 이상의 TO-BE 컬럼에 걸쳐 나오는 경우가 현재 데이터엔 없어서(0건),
  정방향과 달리 후보 랭킹 파이프라인 없이 바로 확정 가능하다.
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


def lookup_reverse_mapping(as_is_table: str, as_is_column: str) -> dict:
    """AS-IS 컬럼 하나가 매핑정의서 상 어느 TO-BE 컬럼(들)에 대응하는지 역방향으로 조회한다.

    정방향(lookup_mapping_candidates)은 TO-BE 컬럼 하나 -> AS-IS 후보 여러 개가 나올 수
    있어서 이후 filter_by_type/check_code_match/infer_secondary_evidence/judge_and_rank로
    순위를 매기는 별도 파이프라인이 필요하다. 반대로 역방향은 매핑정의서 entries를
    스캔해서 이 AS-IS 컬럼을 candidates로 갖는 entry를 찾는 것뿐이라(2026-09-08 기준
    실제 데이터로는 AS-IS 컬럼 하나가 둘 이상의 TO-BE 컬럼에 걸쳐 나오는 경우가 0건),
    후보 랭킹이 필요 없는 정확 조회에 가깝다. 그래서 정방향 함수에 방향 파라미터를
    얹어 억지로 합치지 않고 별도 함수로 둔다.
    """
    mapping_def = load_mapping_definition()
    schema = load_schema()
    source_version = mapping_def.get("source_version")

    if get_column_info(schema, "AS-IS", as_is_table, as_is_column) is None:
        return {
            "as_is_table": as_is_table,
            "as_is_column": as_is_column,
            "matches": [],
            "source_version": source_version,
            "status_hint": "no_match",  # 입력 자체가 현재 AS-IS 스키마에 없음
        }

    matches = []
    for entry in mapping_def["entries"]:
        hit = any(
            c["table"] == as_is_table and c["column"] == as_is_column for c in entry["candidates"]
        )
        if not hit:
            continue
        to_be_table, to_be_col = entry["to_be_column"].split(".", 1)
        to_be_info = get_column_info(schema, "TO-BE", to_be_table, to_be_col)
        if to_be_info is None:
            # 매핑정의서는 이 TO-BE 컬럼을 가리키지만 현재 TO-BE 스키마엔 없음 -> 버전 불일치로 스킵
            continue
        matches.append({
            "to_be_column": entry["to_be_column"],
            "type": to_be_info.get("type"),
            "description": to_be_info.get("description"),
        })

    return {
        "as_is_table": as_is_table,
        "as_is_column": as_is_column,
        "matches": matches,
        "source_version": source_version,
        "status_hint": None if matches else "no_match",
    }
