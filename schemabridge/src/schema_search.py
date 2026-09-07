"""
Node: search_schema (SC-002)

4주차 설계상으로는 "RAG로 TO-BE 스키마 조각 검색"이라고 되어 있지만, 실제로는
SC-001의 매핑정의서와 같은 논리다 — 이 Vertical Slice엔 리포트 정의가 딱 1건
(data/join_rules.json)뿐이라 임베딩 검색을 붙일 후보 자체가 없다. 그래서 여기도
Vector DB 없이 정확 조회로 구현한다(SC-001에서 이미 확정한 "후보가 적으면
즉석 비교, 대량 인프라 불필요" 방침과 동일).

**2026-09-07 재설계 — SC-002는 AS-IS가 아니라 TO-BE 테이블을 조회한다.**
처음 버전(~2026-09-05)은 매 쿼리마다 AS-IS 원천 테이블(ETC_INCOME/BIZ_INCOME)을
직접 UNION ALL로 합치는 "실시간 ETL형" 쿼리를 만들었고, join_rules.json의
column_map은 (AS-IS 테이블.컬럼 → TO-BE 컬럼) 쌍이었다. 사용자가 지적하길,
"차세대"는 마이그레이션이 끝나면 TO-BE 테이블들에 실제 데이터가 이미 들어있는
게 맞는 그림이고, 실무에서도 소득유형별로 TO-BE 테이블이 따로 관리되는 경우가
있다고 해서(2026-09-07), join_rules.json의 sources를 전부 TO-BE 테이블로 바꿨다
(배당소득은 SC-001이 쓰는 기존 ACC_WHT_AGG, 기타·사업소득은 이 리포트 전용으로
새로 추가한 ACC_ETC_INCOME_AGG/ACC_BIZ_INCOME_AGG — data/schema.json 참고).

**검증 로직도 성격이 바뀌었다.** AS-IS→TO-BE 버전에서는 "이 AS-IS 컬럼이 정말
저 TO-BE 컬럼의 의미와 같은가"를 판정해야 했으므로 SC-001의 전체 파이프라인
(filter_by_type → check_code_match → infer_secondary_evidence → judge_and_rank,
LLM 기반 확신도 판정)을 그대로 재사용했다. 지금은 sources가 전부 이미
"TO-BE 의미로 확정된" 테이블이라 그런 의미론적 애매함이 없다 — 남은 위험은
"join_rules.json에 적힌 테이블/컬럼이 schema.json에 실제로 존재하고 타입이
서로 맞는가"라는 구조적 오류뿐이다. 그래서 여기서는 결정적 도구인
filter_by_type만 재사용해 각 소스 컬럼을 리포트의 기준 컬럼(첫 번째 소스)과
타입 비교하고, LLM 판정 단계(check_code_match 이후)는 더 이상 부르지 않는다.
하나라도 존재하지 않거나 타입이 안 맞으면 리포트 전체를 찾지 못한 것으로
처리한다 — SC-002가 깨진 스키마 선언으로 SQL을 만들어 사용자를 속이지 않도록.

**주의 — 여기서 검증하는 건 "이 3개 소스가 다 유효한가"이지 "사용자 요청에
이 3개를 다 써야 하는가"가 아니다.** 사용자가 "사업소득만 뽑아줘"처럼 특정
소득유형만 요청하면 generate_sql이 schema_chunks에 안내된 income_type_label을
보고 해당 소스 테이블만 골라 쓴다(처음엔 항상 3개 전부를 UNION ALL하게 만들었다가,
사용자가 "요청에 따라 달라져야지 무조건 UNION ALL은 아니다"라고 지적해서
2026-09-07에 수정함 — src/sql_generation.py 참고). search_schema는 3개 소스를
전부 미리 검증해 두기만 하고, 실제로 몇 개를 쓸지는 generate_sql 쪽 판단이다.
"""

from src.data_loader import get_column_info, load_join_rules, load_schema
from src.filters import filter_by_type


def _validate_source_column(schema: dict, anchor_column: str, table: str, column: str) -> dict:
    """이 TO-BE 컬럼이 schema.json에 실제로 존재하고, 리포트 기준 컬럼과 타입이 맞는지 확인한다."""
    info = get_column_info(schema, "TO-BE", table, column)
    if info is None:
        return {"confirmed": False, "reason": f"{table}.{column} — schema.json TO-BE에 컬럼 없음"}

    candidate = {"table": table, "column": column, **info}
    filter_result = filter_by_type(anchor_column, [candidate])
    if filter_result["unknown_type"]:
        return {"confirmed": False, "reason": f"{table}.{column} — 타입 정보 없음"}
    if not filter_result["filtered"]:
        reason = filter_result["excluded"][0]["reason"] if filter_result["excluded"] else "타입 불일치"
        return {"confirmed": False, "reason": reason}
    return {"confirmed": True, "reason": f"{table}.{column}이 기준 컬럼({anchor_column})과 타입 일치"}


def search_schema() -> dict:
    join_rules = load_join_rules()
    report = join_rules["reports"][0] if join_rules.get("reports") else None

    if report is None:
        return {"found": False, "schema_chunks": [], "join_rule": None, "validation_error": None}

    schema = load_schema()
    anchor_table = report["sources"][0]["to_be_table"]

    for source in report["sources"]:
        for field, column in source["column_map"].items():
            anchor_column = f"{anchor_table}.{field}"
            result = _validate_source_column(schema, anchor_column, source["to_be_table"], column)
            if not result["confirmed"]:
                return {
                    "found": False,
                    "schema_chunks": [],
                    "join_rule": None,
                    "validation_error": f"{source['to_be_table']}.{column} 검증 실패: {result['reason']}",
                }

    source_labels = ", ".join(f"{s['to_be_table']}({s['income_type_label']})" for s in report["sources"])
    chunks = [
        f"리포트: {report['report_name']}",
        "고정 신고서 항목: "
        + ", ".join(f"{c['label']}({c['field']})" for c in report["report_columns"]),
        f"조회 대상 TO-BE 테이블 {len(report['sources'])}개(모두 이미 마이그레이션 완료되어 데이터가 들어있음): "
        f"{source_labels}",
    ]
    for source in report["sources"]:
        col_map_str = ", ".join(f"{field}→{col}" for field, col in source["column_map"].items())
        chunks.append(f"{source['to_be_table']}({source['income_type_label']}): 컬럼 매핑 {col_map_str}")
    chunks.append(
        f"결합 방식: {report['combine']} — 사용자 요청에 특정 소득유형이 지정되어 있으면 해당 "
        "테이블만 쓰고, 지정이 없으면 위 테이블 전체를 조인 없이 세로로 합친다(UNION ALL)."
    )

    return {"found": True, "schema_chunks": chunks, "join_rule": report, "validation_error": None}
