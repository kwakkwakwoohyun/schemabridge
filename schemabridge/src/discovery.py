"""
Node: discover_candidates (2026-09-09 신규)

lookup_mapping_candidates/lookup_reverse_mapping(src/lookup.py)는 매핑정의서에
미리 등록된 후보 안에서만 검증한다 — 등록 자체가 없으면(no_match) 반대편 스키마는
쳐다보지도 않고 그냥 끝난다. 이 모듈은 그 경우에 반대편 스키마 전체를 후보로 삼아
기존 판정 파이프라인(filter_by_type -> check_code_match -> infer_secondary_evidence ->
judge_and_rank)에 그대로 태우기 위한 "원재료"(전체 컬럼 목록)만 만든다 — 스코어링/판정
로직을 새로 만들지 않고, 후보 소스만 "미리 정해진 리스트"에서 "스키마 전체"로 넓히는
역할만 한다.

- 정방향 탐색: TO-BE 컬럼 기준으로 AS-IS 스키마 전체를 후보로 (anchor_side="TO-BE",
  기존 filter_by_type/infer_secondary_evidence 기본값과 그대로 맞음)
- 역방향 탐색: AS-IS 컬럼 기준으로 TO-BE 스키마 전체를 후보로 (anchor_side="AS-IS")

DISCOVERY_LOW_CONFIDENCE_THRESHOLD/DISCOVERY_CONFIDENCE_GAP_THRESHOLD(2026-09-09 신규):
매핑정의서에 등록된 후보(사람이 미리 검증)와 달리 스스로 찾아낸 후보라서, judge_and_rank
(src/judge.py)의 일반 임계값(0.5/0.10)보다 더 엄격한 값을 써서 confirmed로 확정하는
문턱을 높인다. 확신이 덜 서면 ambiguous/insufficient_metadata로 떨어져서 request_clarification
(더 되물어보기) 또는 최종 정직한 미확정 종료로 이어진다 — "타입만 맞으면 다 갖다 붙이는"
위험한 자동화가 되지 않도록 하는 안전장치다.
"""

from src.data_loader import load_schema

DISCOVERY_LOW_CONFIDENCE_THRESHOLD = 0.75
DISCOVERY_CONFIDENCE_GAP_THRESHOLD = 0.20


def enumerate_all_columns(schema: dict, side: str) -> list[dict]:
    """schema.json의 side("TO-BE" 또는 "AS-IS") 전체를 lookup_mapping_candidates가
    반환하는 것과 같은 모양(table/column/type/description/sample_data/fk)의 리스트로 펼친다.

    타입 정보가 없는(None) 컬럼은 여기서 미리 제외한다 — 사람이 의도적으로 등록한
    소수 후보 중 하나가 타입 불명이면 "판정 불가"로 정직하게 플래그를 남기는 게 맞지만
    (filter_by_type의 unknown_type), 스키마 전체를 훑는 탐색에서는 전혀 무관한 컬럼
    하나가 타입 불명이라는 이유로 나머지 멀쩡한 후보들까지 몰아서 insufficient_metadata로
    막아버리면 탐색의 의미가 없어진다.
    """
    columns = []
    for table, table_info in schema.get(side, {}).items():
        for column, col_info in table_info.get("columns", {}).items():
            if col_info.get("type") is None:
                continue
            columns.append({"table": table, "column": column, **col_info})
    return columns


def discover_as_is_candidates(to_be_column: str) -> list[dict]:
    """정방향 탐색: TO-BE 컬럼 기준으로 AS-IS 스키마 전체를 후보로 반환."""
    schema = load_schema()
    return enumerate_all_columns(schema, "AS-IS")


def discover_to_be_candidates(as_is_column: str) -> list[dict]:
    """역방향 탐색: AS-IS 컬럼 기준으로 TO-BE 스키마 전체를 후보로 반환."""
    schema = load_schema()
    return enumerate_all_columns(schema, "TO-BE")
