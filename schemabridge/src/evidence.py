"""
Node: infer_secondary_evidence

check_code_match까지 통과했지만(강한 근거 없음) 후보가 여전히 2개 이상 남은 경우,
설명 유무에 따라 처리 경로를 나눈다(4주차_완료.md v8 설계):

- AS-IS 후보에 description이 있으면: TO-BE 설명과 임베딩 코사인 유사도로 스코어링.
- description이 없으면: 컬럼명/타입/샘플값만으로 LLM 자체추론(배치 1회 호출, Structured Output).
  이때 similar_confirmed_mappings(참고용 힌트)는 골든셋이 아니라, 매핑정의서 전체를
  결정적 파이프라인(lookup -> filter -> code_match)으로 재실행해서 나온 confirmed 결과를 재사용한다.

이 노드는 최종 판정을 내리지 않는다. 각 후보에 score/rationale을 붙여
judge_and_rank로 넘기기 위한 근거 하나를 추가하는 역할만 한다.

anchor_side(2026-09-09 추가, 기본값 "TO-BE" — 기존 호출부와 100% 동일하게 동작):
후보 자동 탐색(src/discovery.py)의 역방향 탐색(AS-IS 컬럼 기준으로 TO-BE 전체를
후보로 스코어링)만 "AS-IS"를 명시적으로 넘긴다. _get_confirmed_mappings 힌트는
TO-BE 기준 매핑정의서를 재실행하는 로직이라 anchor_side="AS-IS"일 때는 사용하지 않는다.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.code_match import check_code_match
from src.data_loader import get_column_info, load_mapping_definition, load_schema
from src.filters import filter_by_type
from src.llm_client import chat_completion_json, embed
from src.lookup import lookup_mapping_candidates

SELF_INFERENCE_SCHEMA = {
    "name": "self_inference_scores",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "column": {"type": "string"},
                        "score": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["table", "column", "score", "rationale"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["candidates"],
        "additionalProperties": False,
    },
}


def infer_secondary_evidence(anchor_column: str, filtered_candidates: list[dict], anchor_side: str = "TO-BE") -> dict:
    """anchor_side(2026-09-09 추가, 기본값 "TO-BE" — 기존 호출부와 100% 동일하게 동작):
    기준 컬럼이 TO-BE 쪽인지 AS-IS 쪽인지. 후보 자동 탐색(src/discovery.py)의 역방향
    탐색만 "AS-IS"를 명시적으로 넘겨서, AS-IS 컬럼 기준으로 TO-BE 전체 후보를 스코어링한다.
    """
    schema = load_schema()
    anchor_table, anchor_col = anchor_column.split(".")
    anchor_info = get_column_info(schema, anchor_side, anchor_table, anchor_col) or {}
    anchor_description = anchor_info.get("description")

    if anchor_description:
        # 후보 중 설명이 있는 애들 -> 기준 설명 vs 후보 설명 임베딩 코사인 유사도 비교
        with_desc = [c for c in filtered_candidates if c.get("description")]
        # 후보 중 설명이 없는 애들 -> 컬럼명/타입/샘플값으로 LLM이 추론
        without_desc = [c for c in filtered_candidates if not c.get("description")]
    else:
        # 기준 컬럼 자체에 설명이 없으면 유사도 비교 기준이 없으므로 전부 자체추론으로 보낸다.
        with_desc, without_desc = [], filtered_candidates

    scores = []
    # 후보 Description 이 있으면 (비교대상있음 벡터 유사도)
    if with_desc:
        # 벡터 유사도 비교
        scores.extend(_score_by_description_similarity(anchor_description, with_desc))
    # 기준 Desc는 있는데 후보가 없는 경우 + 기준 Desc부터 없는 경우
    if without_desc:
        confirmed_hints = _get_confirmed_mappings(exclude_column=anchor_column) if anchor_side == "TO-BE" else []
        # 자체추론
        scores.extend(
            _score_by_self_inference(anchor_column, anchor_description, without_desc, confirmed_hints, anchor_side)
        )

    scores.sort(key=lambda s: s["score"], reverse=True)
    return {"to_be_column": anchor_column, "evidence_scores": scores}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b)

# 벡터 유사도 검색?
def _score_by_description_similarity(to_be_description: str, candidates: list[dict]) -> list[dict]:
    texts = [to_be_description] + [c["description"] for c in candidates]
    vectors = embed(texts)
    to_be_vec, cand_vecs = vectors[0], vectors[1:]

    scores = []
    for c, vec in zip(candidates, cand_vecs):
        sim = _cosine_similarity(to_be_vec, vec)
        scores.append({
            "table": c["table"],
            "column": c["column"],
            "score": round(sim, 4),
            "evidence_source": "description_similarity",
            "rationale": f"TO-BE 설명 '{to_be_description}' vs AS-IS 설명 '{c['description']}' 임베딩 코사인 유사도",
        })
    return scores

# 자체추론
def _score_by_self_inference(
    anchor_column: str,
    anchor_description: str | None,
    candidates: list[dict],
    confirmed_hints: list[dict],
    anchor_side: str = "TO-BE",
) -> list[dict]:
    candidate_side = "AS-IS" if anchor_side == "TO-BE" else "TO-BE"
    candidate_lines = [
        f"- {c['table']}.{c['column']} (타입: {c.get('type')}, 샘플값: {c.get('sample_data')})" for c in candidates
    ]
    hint_lines = [
        f"- TO-BE {h['to_be_column']} -> AS-IS {h['table']}.{h['column']} (이미 결정적으로 확정된 매핑)"
        for h in confirmed_hints
    ]

    prompt = (
        f"{anchor_side} 컬럼 '{anchor_column}'"
        + (f" (설명: {anchor_description})" if anchor_description else " (설명 없음)")
        + f"에 대해 아래 {candidate_side} 후보 중 어느 것이 올바른 매핑인지 판단하라.\n"
        "각 후보는 description이 없으므로 컬럼명, 데이터 타입, 샘플값만으로 추론해야 한다.\n\n"
        "후보:\n" + "\n".join(candidate_lines) + "\n\n"
        + (
            "참고(다른 컬럼에서 이미 결정적 로직으로 확정된 매핑 사례 - 패턴 참고용):\n"
            + "\n".join(hint_lines) + "\n\n"
            if hint_lines
            else ""
        )
        + "각 후보에 대해 0~1 사이 score(정답일 가능성)와 rationale(판단 근거)을 답하라."
    )

    result = chat_completion_json(
        messages=[
            {
                "role": "system",
                "content": "너는 AS-IS/TO-BE 컬럼 매핑을 추론하는 어시스턴트다. "
                "설명이 없는 경우 컬럼명, 타입, 샘플 데이터 패턴으로 추론한다.",
            },
            {"role": "user", "content": prompt},
        ],
        schema=SELF_INFERENCE_SCHEMA,
    )

    return [
        {
            "table": item["table"],
            "column": item["column"],
            "score": item["score"],
            "evidence_source": "self_inference",
            "rationale": item["rationale"],
        }
        for item in result["candidates"]
    ]


def _get_confirmed_mappings(exclude_column: str) -> list[dict]:
    """매핑정의서 전체를 결정적 파이프라인으로 재실행해 이미 confirmed로 떨어지는
    항목만 모은다(graph.py의 check_code_match/format_response 노드와 동일 규칙).
    골든셋의 정답을 재사용하는 게 아니라, 시스템이 스스로 결정적으로 확정한 결과만 재사용한다.
    """
    mapping_def = load_mapping_definition()
    confirmed = []

    for entry in mapping_def["entries"]:
        col = entry["to_be_column"]
        if col == exclude_column:
            continue

        lookup_result = lookup_mapping_candidates(col)
        if lookup_result["status_hint"] in ("no_match", "version_mismatch"):
            continue

        filter_result = filter_by_type(col, lookup_result["candidates"])
        filtered = filter_result["filtered"]
        if filter_result["unknown_type"] or not filtered:
            continue

        if len(filtered) == 1:
            winner = filtered[0]
        else:
            code_results = check_code_match(filtered)
            matched = [r for r in code_results if r["matched"]]
            if len(matched) != 1:
                continue  # 결정적으로 확정되지 않음 -> 힌트 풀에서 제외
            matched_key = (matched[0]["table"], matched[0]["column"])
            winner = next(c for c in filtered if (c["table"], c["column"]) == matched_key)

        confirmed.append({"to_be_column": col, "table": winner["table"], "column": winner["column"]})

    return confirmed


if __name__ == "__main__":
    col = sys.argv[1] if len(sys.argv) > 1 else "ACC_WHT_AGG.wht_tax_amt"
    lookup_result = lookup_mapping_candidates(col)
    filter_result = filter_by_type(col, lookup_result["candidates"])
    print(json.dumps(infer_secondary_evidence(col, filter_result["filtered"]), ensure_ascii=False, indent=2))
