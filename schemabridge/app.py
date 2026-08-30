"""
SchemaBridge 데모 뷰어 (시연 영상용)

주의: 이건 제품 기능이 아니라 시연/발표용 도구입니다. 실제 판정 로직은
src/lookup.py, src/filters.py, src/code_match.py를 그대로 재사용합니다 —
이미 골든셋 12건으로 검증된 결정적 로직이라 여기서 다시 만들지 않습니다.

infer_secondary_evidence(임베딩 유사도/LLM 자체추론)는 구현되어 후보별 점수를
보여주지만, 최종 확정 노드(judge_and_rank)는 아직 없어서 순위만 보여주고
"PENDING"으로 정직하게 표시합니다.

실행: streamlit run app.py
"""

import streamlit as st

from src.lookup import lookup_mapping_candidates
from src.filters import filter_by_type
from src.code_match import check_code_match
from src.evidence import infer_secondary_evidence


def preliminary_status(lookup_result, filter_result, code_results):
    """결정적 로직만으로 판정 가능한 경우만 상태를 확정하고,
    나머지는 LLM 판정 노드가 필요하다는 걸 명시한다."""
    if lookup_result["status_hint"] in ("no_match", "version_mismatch"):
        return lookup_result["status_hint"]
    if not filter_result["filtered"]:
        return "no_match"
    if filter_result["unknown_type"]:
        return "insufficient_metadata (타입 정보 없음)"
    matched = [r for r in code_results if r["matched"]]
    if len(filter_result["filtered"]) == 1:
        return "confirmed"
    if len(matched) == 1:
        return "confirmed (코드값 일치로 강한 근거 확보)"
    return "PENDING (LLM 판정 노드 필요)"


st.set_page_config(page_title="SchemaBridge", page_icon="🔗", layout="centered")

st.title("SchemaBridge")
st.caption("AS-IS/TO-BE 매핑 판단 지원 Agent — 시연용 뷰어 (제품 UI 아님, 2주차 범위정의상 비개발자용 UI는 Out of Scope)")

to_be_column = st.text_input(
    "TO-BE 컬럼명을 입력하세요",
    placeholder="예: ACC_WHT_AGG.wht_tax_amt",
)
st.caption(
    "골든셋 예시: ACC_WHT_AGG.pay_dt / income_type_cd / wht_tax_amt / "
    "div_wht_amt / reg_dt / updt_dt 등"
)

if st.button("매핑 후보 조회", type="primary") and to_be_column:
    lookup_result = lookup_mapping_candidates(to_be_column)

    if lookup_result["status_hint"] == "no_match":
        st.error(f"매핑 정보 없음 (No-match) — 매핑정의서에 '{to_be_column}' 항목이 없습니다.")
    elif lookup_result["status_hint"] == "version_mismatch":
        st.warning(
            f"버전 불일치 (Version-mismatch) — 매핑정의서상 AS-IS 컬럼이 "
            f"최신 스키마에 없습니다. (참조 매핑정의서 버전: {lookup_result['source_version']})"
        )
    else:
        filter_result = filter_by_type(to_be_column, lookup_result["candidates"])
        code_results = check_code_match(filter_result["filtered"])
        status = preliminary_status(lookup_result, filter_result, code_results)

        st.subheader("판정 상태")
        st.code(status)

        st.subheader("AS-IS 후보")
        matched_map = {(r["table"], r["column"]): r["matched"] for r in code_results}
        rows = [
            {
                "테이블": c["table"],
                "컬럼": c["column"],
                "타입": c.get("type") or "(없음)",
                "설명": c.get("description") or "(없음)",
                "코드값 일치": "O" if matched_map.get((c["table"], c["column"])) else "X",
            }
            for c in filter_result["filtered"]
        ]
        if rows:
            st.table(rows)
        else:
            st.info("타입 조건을 통과한 후보가 없습니다.")

        if filter_result["excluded"]:
            with st.expander(f"타입 불일치로 제외된 후보 {len(filter_result['excluded'])}건"):
                for e in filter_result["excluded"]:
                    st.write(f"- {e['candidate']['table']}.{e['candidate']['column']}: {e['reason']}")

        if "PENDING" in status:
            with st.spinner("infer_secondary_evidence 실행 중 (Azure OpenAI 호출)..."):
                evidence_result = infer_secondary_evidence(to_be_column, filter_result["filtered"])

            st.subheader("근거 스코어링 결과 (infer_secondary_evidence)")
            st.table([
                {
                    "테이블": s["table"],
                    "컬럼": s["column"],
                    "점수": s["score"],
                    "근거 출처": s["evidence_source"],
                    "판단 근거": s["rationale"],
                }
                for s in evidence_result["evidence_scores"]
            ])
            st.info(
                "여기까지가 근거 스코어링 결과입니다. 최종 confirmed/ambiguous 확정과 "
                "사람에게 되묻는 로직(judge_and_rank/request_clarification)은 아직 구현 전이라 "
                "여기서 임의로 순위를 확정하지 않습니다."
            )
