"""
SchemaBridge 데모 뷰어 (시연 영상용)

주의: 이건 제품 기능이 아니라 시연/발표용 도구입니다. 실제 판정 로직은
src/lookup.py, src/filters.py, src/code_match.py를 그대로 재사용합니다 —
이미 골든셋으로 검증된 결정적 로직이라 여기서 다시 만들지 않습니다.

결정적 로직(lookup/filter/code_match)만으로 판정이 안 나는 경우
infer_secondary_evidence(임베딩 유사도/LLM 자체추론) -> judge_and_rank(confidence_gap
기준 최종 확정)까지 그대로 이어서 호출한다. 그래도 confirmed가 안 나오면(ambiguous/
insufficient_metadata) request_clarification 로직(src/clarification.py)으로 실제
되묻는다 — graph.py(CLI)와 달리 여기는 웹 서버라 input()을 못 쓰므로, st.text_input +
st.session_state로 같은 재판정 루프를 구현한다(최대 MAX_ATTEMPTS회).

confirmed로 확정되는 순간(단일 후보/코드값 일치/LLM 점수 확정/되묻기 후 확정 어느
경로든) generate_rationale(src/rationale.py)을 호출해 confidence_gap 퍼센트 같은
내부 계산값 대신 사람이 읽기 좋은 최종 근거 문장을 만든다. 화면에는 내부 판정
과정(1차 판정, AS-IS 후보, 근거 스코어링 표)과 이 최종 근거를 모두 보여줘서,
"내부 추론과 사용자 노출용 근거를 분리한다"는 설계 원칙을 실제로 보여준다.

st.session_state["result"]를 유일한 상태 저장소로 써서, "답변 제출" 버튼 클릭으로
스크립트가 처음부터 재실행되어도(Streamlit rerun 모델) 후보 목록·판정 결과가
사라지지 않고 이어지게 한다.

실행: streamlit run app.py
"""

import streamlit as st

from src.lookup import lookup_mapping_candidates
from src.filters import filter_by_type
from src.code_match import check_code_match
from src.evidence import infer_secondary_evidence
from src.judge import judge_and_rank
from src.clarification import MAX_ATTEMPTS, build_clarification_question, rescore_with_clarification
from src.rationale import generate_rationale


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


def render_result(result: dict) -> None:
    """st.session_state["result"]에 저장된 내용을 화면에 그린다.

    "매핑 후보 조회" 버튼을 눌렀을 때뿐 아니라, 되묻기 루프의 "답변 제출" 버튼을
    눌러서 스크립트가 재실행됐을 때도 이 함수가 항상 호출되어 지금까지의 진행
    상황(후보 목록, 판정 결과, 되묻은 횟수)을 그대로 이어서 보여준다.
    """
    if result["kind"] == "exception":
        lookup_result = result["lookup_result"]
        if lookup_result["status_hint"] == "no_match":
            st.error(f"매핑 정보 없음 (No-match) — 매핑정의서에 '{result['to_be_column']}' 항목이 없습니다.")
        else:
            st.warning(
                f"버전 불일치 (Version-mismatch) — 매핑정의서상 AS-IS 컬럼이 "
                f"최신 스키마에 없습니다. (참조 매핑정의서 버전: {lookup_result['source_version']})"
            )
        return

    filter_result = result["filter_result"]
    code_results = result["code_results"]
    status = result["status"]

    st.subheader("1차 판정 (결정적 로직)")
    st.code(status)
    if "PENDING" in status:
        st.caption("결정적 로직만으로는 확정 불가 — 아래에서 근거 스코어링 + 최종 판정을 이어서 진행합니다.")

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

    clar = result.get("clarification")
    if clar is None:
        if "confirmed" in status:
            st.subheader("최종 판정 근거")
            winner = result["winner"]
            st.success(f"CONFIRMED — {winner['table']}.{winner['column']}\n\n{result['final_rationale']}")
        return

    st.subheader("근거 스코어링 & 최종 판정 (내부 상태)")
    st.caption(f"1위·2위 점수차(confidence_gap): {clar['confidence_gap']:.0%} (확정 임계값: 10%)")
    st.table([
        {
            "테이블": s["table"],
            "컬럼": s["column"],
            "점수": s["score"],
            "근거 출처": s["evidence_source"],
            "판단 근거": s["rationale"],
        }
        for s in clar["ranked_result"]
    ])

    if clar["status"] == "confirmed":
        winner = clar["winner"]
        st.subheader("최종 판정 근거")
        st.success(f"CONFIRMED — {winner['table']}.{winner['column']}\n\n{clar['final_rationale']}")
        return

    if clar["attempts"] >= MAX_ATTEMPTS:
        st.error(
            f"{clar['attempts']}회 되물어도 확정하지 못했습니다 ({clar['status'].upper()}). "
            "사람의 최종 판단이 필요합니다."
        )
        with st.expander("지금까지 주고받은 답변"):
            for i, a in enumerate(clar["answers"], 1):
                st.write(f"{i}차 답변: {a}")
        return

    # 아직 되물을 기회가 남음 -> 되묻기 UI
    st.warning(
        f"{clar['status'].upper()} — 확신이 서지 않아 되묻습니다 "
        f"({clar['attempts'] + 1}/{MAX_ATTEMPTS}회차)"
    )
    st.write(clar["question"])
    answer = st.text_input("답변을 입력하세요", key=f"clarify_input_{clar['attempts']}")
    if st.button("답변 제출", key=f"clarify_submit_{clar['attempts']}") and answer:
        with st.spinner("답변을 반영해서 재판정 중 (Azure OpenAI 호출)..."):
            updated_scores = rescore_with_clarification(result["to_be_column"], clar["ranked_result"], answer)
            rejudged = judge_and_rank(updated_scores, code_results)
        clar["ranked_result"] = rejudged["ranked_result"]
        clar["status"] = rejudged["status"]
        clar["winner"] = rejudged["winner"]
        clar["confidence_gap"] = rejudged["confidence_gap"]
        clar["attempts"] += 1
        clar["answers"].append(answer)
        if clar["status"] == "confirmed":
            with st.spinner("근거 문장 생성 중 (Azure OpenAI 호출)..."):
                clar["final_rationale"] = generate_rationale(
                    result["to_be_column"], clar["winner"], ranked_result=clar["ranked_result"],
                    clarification_answers=clar["answers"],
                )
            clar["question"] = None
        elif clar["attempts"] < MAX_ATTEMPTS:
            with st.spinner("다음 질문 생성 중..."):
                clar["question"] = build_clarification_question(result["to_be_column"], clar["ranked_result"])
        else:
            clar["question"] = None
        st.rerun()


st.set_page_config(page_title="SchemaBridge", page_icon="🔗", layout="centered")

st.title("SchemaBridge")
st.caption("AS-IS/TO-BE 매핑 판단 지원 Agent — 시연용 뷰어 (제품 UI 아님, 2주차 범위정의상 비개발자용 UI는 Out of Scope)")

to_be_column = st.text_input(
    "TO-BE 컬럼명을 입력하세요",
    placeholder="예: ACC_WHT_AGG.wht_tax_amt",
)
st.caption(
    "골든셋 예시: ACC_WHT_AGG.pay_dt / income_type_cd / wht_tax_amt / settle_method_cd(되묻기 시연용) / "
    "div_wht_amt / reg_dt / updt_dt 등"
)

if st.button("매핑 후보 조회", type="primary") and to_be_column:
    lookup_result = lookup_mapping_candidates(to_be_column)

    if lookup_result["status_hint"] in ("no_match", "version_mismatch"):
        st.session_state.result = {
            "kind": "exception",
            "to_be_column": to_be_column,
            "lookup_result": lookup_result,
        }
    else:
        filter_result = filter_by_type(to_be_column, lookup_result["candidates"])
        code_results = check_code_match(filter_result["filtered"])
        status = preliminary_status(lookup_result, filter_result, code_results)

        result = {
            "kind": "normal",
            "to_be_column": to_be_column,
            "lookup_result": lookup_result,
            "filter_result": filter_result,
            "code_results": code_results,
            "status": status,
            "clarification": None,
        }

        if "PENDING" in status:
            with st.spinner("infer_secondary_evidence 실행 중 (Azure OpenAI 호출)..."):
                evidence_result = infer_secondary_evidence(to_be_column, filter_result["filtered"])
            judged = judge_and_rank(evidence_result["evidence_scores"], code_results)
            clarification = {
                "ranked_result": judged["ranked_result"],
                "status": judged["status"],
                "winner": judged["winner"],
                "confidence_gap": judged["confidence_gap"],
                "attempts": 0,
                "answers": [],
                "question": None,
                "final_rationale": None,
            }
            if judged["status"] == "confirmed":
                with st.spinner("근거 문장 생성 중 (Azure OpenAI 호출)..."):
                    clarification["final_rationale"] = generate_rationale(
                        to_be_column, judged["winner"], ranked_result=judged["ranked_result"]
                    )
            else:
                with st.spinner("되물을 질문 생성 중 (Azure OpenAI 호출)..."):
                    clarification["question"] = build_clarification_question(to_be_column, judged["ranked_result"])
            result["clarification"] = clarification
        elif "confirmed" in status:
            # 결정적 로직(단일 후보 / 코드값 일치)만으로 이미 확정된 경우.
            if len(filter_result["filtered"]) == 1:
                winner = filter_result["filtered"][0]
            else:
                matched_keys = {(r["table"], r["column"]) for r in code_results if r["matched"]}
                winner = next(c for c in filter_result["filtered"] if (c["table"], c["column"]) in matched_keys)
            with st.spinner("근거 문장 생성 중 (Azure OpenAI 호출)..."):
                result["final_rationale"] = generate_rationale(to_be_column, winner)
            result["winner"] = winner

        st.session_state.result = result

# 렌더링은 버튼 클릭 여부와 무관하게 항상 session_state를 기준으로 그린다 —
# "답변 제출" 버튼을 눌러 재실행됐을 때도 지금까지의 진행 상황이 이어지도록.
if "result" in st.session_state:
    render_result(st.session_state.result)
