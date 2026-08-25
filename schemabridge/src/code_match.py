"""
Node: check_code_match (강한 근거)

코드 매핑정의서를 정확 조회해 각 후보가 TO-BE 공통코드와 매핑이 확인된 컬럼인지 확인한다.
"""

from src.data_loader import load_code_mapping


def check_code_match(candidates: list[dict]) -> list[dict]:
    code_map = load_code_mapping()
    matched_set = {(e["table"], e["column"]) for e in code_map["entries"]}

    results = []
    for c in candidates:
        matched = (c["table"], c["column"]) in matched_set
        results.append({
            "table": c["table"],
            "column": c["column"],
            "matched": matched,
        })
    return results
