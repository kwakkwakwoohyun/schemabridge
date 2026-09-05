"""
결정적 로직 3종(lookup_mapping_candidates -> filter_by_type -> check_code_match)을
골든셋 전체(data/golden_set.json)에 대해 실제로 실행하고, 여기까지만으로 판정 가능한
케이스는 맞는지 검증한다.

judge_and_rank/infer_secondary_evidence는 다음 태스크(LLM)에서 구현하므로,
여기서는 "여기까지의 파이프라인 출력이 기대한 모양인지"만 확인한다.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.lookup import lookup_mapping_candidates
from src.filters import filter_by_type
from src.code_match import check_code_match


def preliminary_status(lookup_result, filter_result, code_results):
    if lookup_result["status_hint"] in ("no_match", "version_mismatch"):
        return lookup_result["status_hint"]
    if not filter_result["filtered"]:
        return "no_match"
    if filter_result["unknown_type"]:
        return "insufficient_metadata (예상, 타입정보 없음)"
    matched = [r for r in code_results if r["matched"]]
    if len(filter_result["filtered"]) == 1:
        return "confirmed"
    if len(matched) == 1:
        return "confirmed (코드값으로 강한 근거 확보)"
    return "PENDING -> infer_secondary_evidence/judge_and_rank 필요 (다음 태스크)"


def main():
    golden_path = os.path.join(os.path.dirname(__file__), "..", "data", "golden_set.json")
    with open(golden_path, encoding="utf-8") as f:
        golden_set = json.load(f)

    print(f"{'TO-BE 컬럼':32} {'기대 상태':22} {'현재 파이프라인 판정':45} {'후보수':6}")
    print("-" * 115)

    for case in golden_set:
        col = case["to_be_column"]
        lookup_result = lookup_mapping_candidates(col)
        filter_result = filter_by_type(col, lookup_result["candidates"])
        code_results = check_code_match(filter_result["filtered"])
        status = preliminary_status(lookup_result, filter_result, code_results)

        n_candidates = len(lookup_result["candidates"])
        print(f"{col:32} {case['expected_status']:22} {status:45} {n_candidates:6}")

    print("\n--- 상세 예시 3건 ---")
    for col in ["ACC_WHT_AGG.income_type_cd", "ACC_WHT_AGG.wht_tax_amt", "ACC_WHT_AGG.reg_dt"]:
        print(f"\n[{col}]")
        lookup_result = lookup_mapping_candidates(col)
        print("  lookup:", json.dumps(lookup_result, ensure_ascii=False))
        filter_result = filter_by_type(col, lookup_result["candidates"])
        print("  filter:", json.dumps(filter_result, ensure_ascii=False))
        code_results = check_code_match(filter_result["filtered"])
        print("  code_match:", json.dumps(code_results, ensure_ascii=False))


if __name__ == "__main__":
    main()
