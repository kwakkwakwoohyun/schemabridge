"""
Node: judge_and_rank

infer_secondary_evidence가 매긴 evidence_scores를 근거로 최종 confirmed/ambiguous/
insufficient_metadata를 판정한다(4주차_완료.md v5 설계).

판정 규칙:
1. 코드 매핑정의서상 코드값이 일치하는 후보가 하나라도 있으면, 그 후보군을 보조 근거
   점수와 무관하게 항상 1순위 후보군으로 우선 배치한다(순위 계산을 그 안에서만 함).
2. top1 점수가 LOW_CONFIDENCE_THRESHOLD 미만이면 insufficient_metadata.
3. top1-top2 점수차(confidence_gap)가 CONFIDENCE_GAP_THRESHOLD 미만이면 ambiguous.
4. 둘 다 아니면 confirmed.

두 임계값 모두 설계 문서상 "초기값, PoC 진행하며 실데이터로 튜닝 예정"으로 명시된
잠정치이며, 실제 골든셋 4건 실행 결과(gap 0.19~0.98)를 바탕으로 confirmed가
아주 드물게만 나오지 않도록 확인 후 그대로 채택했다.
"""

CONFIDENCE_GAP_THRESHOLD = 0.10
LOW_CONFIDENCE_THRESHOLD = 0.5


def judge_and_rank(evidence_scores: list[dict], code_match_results: list[dict]) -> dict:
    matched_keys = {(r["table"], r["column"]) for r in code_match_results if r["matched"]}

    if matched_keys:
        pool = [s for s in evidence_scores if (s["table"], s["column"]) in matched_keys]
    else:
        pool = evidence_scores

    ranked = sorted(pool, key=lambda s: s["score"], reverse=True)

    top1 = ranked[0]
    top2 = ranked[1] if len(ranked) > 1 else None
    confidence_gap = round(top1["score"] - top2["score"], 4) if top2 else 1.0

    if top1["score"] < LOW_CONFIDENCE_THRESHOLD:
        status = "insufficient_metadata"
    elif confidence_gap < CONFIDENCE_GAP_THRESHOLD:
        status = "ambiguous"
    else:
        status = "confirmed"

    return {
        "status": status,
        "confidence_gap": confidence_gap,
        "ranked_result": ranked,
        "winner": top1 if status == "confirmed" else None,
        "code_match_priority_applied": bool(matched_keys),
    }
