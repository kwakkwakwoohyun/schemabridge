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

2026-09-09 추가 — low_confidence_threshold/confidence_gap_threshold를 옵션 파라미터로
받는다(기본값은 위 상수 그대로라 기존 호출부는 100% 동일하게 동작). 후보 자동 탐색
(src/discovery.py)은 매핑정의서에 등록되어 사람이 미리 검증해둔 후보가 아니라 시스템이
스스로 찾아낸 후보라서, 더 엄격한 값(DISCOVERY_LOW_CONFIDENCE_THRESHOLD/
DISCOVERY_CONFIDENCE_GAP_THRESHOLD, src/discovery.py 참고)을 넘겨서 더 확신이 클 때만
confirmed로 확정하게 한다.
"""

CONFIDENCE_GAP_THRESHOLD = 0.10
LOW_CONFIDENCE_THRESHOLD = 0.5


def judge_and_rank(
    evidence_scores: list[dict],
    code_match_results: list[dict],
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
    confidence_gap_threshold: float = CONFIDENCE_GAP_THRESHOLD,
) -> dict:
    matched_keys = {(r["table"], r["column"]) for r in code_match_results if r["matched"]}

    if matched_keys:
        pool = [s for s in evidence_scores if (s["table"], s["column"]) in matched_keys]
    else:
        pool = evidence_scores

    ranked = sorted(pool, key=lambda s: s["score"], reverse=True)

    top1 = ranked[0]
    top2 = ranked[1] if len(ranked) > 1 else None
    confidence_gap = round(top1["score"] - top2["score"], 4) if top2 else 1.0

    if top1["score"] < low_confidence_threshold:
        status = "insufficient_metadata"
    elif confidence_gap < confidence_gap_threshold:
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
