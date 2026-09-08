"""
SchemaBridge 데모 뷰어 (시연 영상용)

주의: 이건 제품 기능이 아니라 시연/발표용 도구입니다. 실제 판정 로직은
src/lookup.py, src/filters.py, src/code_match.py를 그대로 재사용합니다 —
이미 골든셋으로 검증된 결정적 로직이라 여기서 다시 만들지 않습니다.

이 프로젝트는 SC-001(컬럼 매핑 판정)과 SC-002(신고서용 집계 쿼리 생성)를 모두
포함하는 하나의 프로젝트라서, 이 데모 화면도 입력 하나로 둘 다 받는다 —
graph.py(CLI)와 똑같이 classify_intent()로 먼저 분류한 뒤 갈라진다.

**SC-001 경로(정방향, TO-BE→AS-IS)**: 결정적 로직(lookup/filter/code_match)만으로 판정이
안 나는 경우 infer_secondary_evidence(임베딩 유사도/LLM 자체추론) -> judge_and_rank
(confidence_gap 기준 최종 확정)까지 그대로 이어서 호출한다. 그래도 confirmed가 안
나오면(ambiguous/insufficient_metadata) request_clarification 로직(src/clarification.py)
으로 실제 되묻는다 — graph.py(CLI)와 달리 여기는 웹 서버라 input()을 못 쓰므로,
st.text_input + st.session_state로 같은 재판정 루프를 구현한다(최대 MAX_ATTEMPTS회).
confirmed로 확정되는 순간 generate_rationale(src/rationale.py)을 호출해 confidence_gap
퍼센트 같은 내부 계산값 대신 사람이 읽기 좋은 최종 근거 문장을 만든다.

**SC-001 경로(역방향, AS-IS→TO-BE, 2026-09-08 추가)**: classify_intent가 as_is_column을
채우면 run_sc001_reverse가 lookup_reverse_mapping(src/lookup.py, 매핑정의서 역인덱스
조회, 결정적)만 호출한다. 후보 랭킹이 필요 없어(현재 데이터 기준 AS-IS 컬럼 하나가
둘 이상의 TO-BE 컬럼에 걸치는 경우 0건) filter/code_match/evidence/judge 파이프라인을
타지 않고, matches가 1건이면 바로 generate_rationale로 근거 문장을 만들어 confirmed로
끝낸다. classify_intent가 TO-BE/AS-IS 어느 쪽인지조차 확신 못 하면(컬럼명이 양쪽에
겹치는 경우, src/intent.py 참고) direction_ambiguous로 다시 질문해달라고 안내한다.

**SC-002 경로**: classify_intent가 매긴 sc002_mode로 갈라진다(2026-09-07 추가 —
src/intent.py docstring 참고). mode="explore"(예: "원천세 집계 관련 테이블 다
찾아줘")면 search_schema(join_rules.json 조회 + SC-001 도구로 실제 재검증)만
호출하고 검증된 테이블/컬럼 정보를 그대로 보여준다 — generate_sql/execute_sql은
부르지 않는다. mode="execute"(예: "뽑아줘", "집계해줘")면 기존처럼
search_schema -> generate_sql -> validate_readonly -> execute_sql을 그대로
호출한다. 실패하면 generate_sql로 되돌아가는 self_correct 재시도(최대
MAX_SQL_ATTEMPTS회)를 사람 개입 없이 버튼 클릭 한 번 안에서 자동으로 돈다
(request_clarification과 달리 사람 답변이 필요 없는 루프라 화면 rerun 없이
그 자리에서 끝까지 처리 가능).

st.session_state["result"]를 유일한 상태 저장소로 써서, "답변 제출" 버튼 클릭으로
스크립트가 처음부터 재실행되어도(Streamlit rerun 모델) 후보 목록·판정 결과가
사라지지 않고 이어지게 한다.

실행: streamlit run app.py
"""

import streamlit as st

from src.lookup import lookup_mapping_candidates, lookup_reverse_mapping
from src.filters import filter_by_type
from src.code_match import check_code_match
from src.evidence import infer_secondary_evidence
from src.judge import judge_and_rank
from src.clarification import MAX_ATTEMPTS, build_clarification_question, rescore_with_clarification
from src.rationale import generate_rationale
from src.intent import classify_intent
from src.schema_search import search_schema
from src.sql_generation import generate_sql
from src.sql_validation import validate_readonly
from src.sql_executor import MAX_SQL_ATTEMPTS, execute_sql


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

    if result["kind"] == "direction_ambiguous":
        # 2026-09-08 추가: 입력이 TO-BE/AS-IS 어느 쪽 컬럼을 말하는지, 혹은 어느
        # 테이블인지조차 classify_intent가 확신하지 못한 경우(src/intent.py 참고) —
        # 억지로 추측하지 않고 사용자에게 테이블명을 포함해 다시 질문해달라고 안내한다.
        st.warning(
            "입력이 TO-BE 컬럼을 묻는 건지 AS-IS 컬럼을 묻는 건지 특정할 수 없습니다. "
            "테이블명을 포함해서 다시 질문해 주세요(예: 'LBR_WHT.settle_method_cd가 "
            "TO-BE 어디로 매핑돼?')."
        )
        return

    if result["kind"] == "reverse_exception":
        # 2026-09-08 추가: 역방향(AS-IS -> TO-BE) 조회에서 no_match/ambiguous
        reverse_result = result["reverse_result"]
        if not reverse_result["matches"]:
            st.error(
                f"매핑 정보 없음 (No-match) — '{result['as_is_column']}'을(를) TO-BE로 "
                "매핑하는 항목이 매핑정의서에 없거나, 이 AS-IS 컬럼 자체가 현재 스키마에 없습니다."
            )
        else:
            candidates = ", ".join(m["to_be_column"] for m in reverse_result["matches"])
            st.warning(f"여러 TO-BE 컬럼에 매핑됨 (Ambiguous) — {candidates}")
        return

    if result["kind"] == "reverse_normal":
        # 2026-09-08 추가: 역방향(AS-IS -> TO-BE) 조회 confirmed. lookup_reverse_mapping이
        # 이미 단일 확정이라(현재 데이터 기준 후보 랭킹 불필요) 정방향처럼 1차 판정/AS-IS
        # 후보 표/근거 스코어링 표를 보여줄 게 없고, 바로 최종 근거만 보여준다.
        st.subheader("역방향 조회 결과 (AS-IS → TO-BE)")
        st.success(f"CONFIRMED — {result['as_is_column']} → {result['to_be_column']}\n\n{result['rationale']}")
        return

    if result["kind"] == "report":
        render_report_result(result)
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


def render_report_result(result: dict) -> None:
    """SC-002(신고서용 집계 쿼리) 결과를 그린다. self_correct 재시도는 사람 개입이
    필요 없는 루프라 버튼 클릭 한 번 안에서 전부 끝내고, 여기서는 최종 결과만
    보여준다(성공 또는 3가지 예외 중 하나)."""
    st.subheader("SC-002 — 신고서용 집계 쿼리")

    status = result["status"]
    if status == "mapping_not_ready":
        st.error(f"매핑 준비 안 됨 (mapping_not_ready) — {result['reason']}")
        return
    if status == "schema_found":
        st.success(f"SCHEMA_FOUND — {result['report_name']} (테이블 정보만 조회, 쿼리 실행 없음)")
        st.write("고정 신고서 항목: " + ", ".join(result["report_columns"]))
        for source in result["sources"]:
            col_map_str = ", ".join(f"{field}→{col}" for field, col in source["column_map"].items())
            st.write(f"- **{source['to_be_table']}** ({source['income_type_label']}): {col_map_str}")
        return
    if status == "readonly_violation":
        st.error(f"Read-only 검증 실패 (readonly_violation) — {result['reason']}")
        st.code(result["sql"], language="sql")
        return
    if status == "sql_execution_failed":
        st.error(f"SQL 실행 실패 (sql_execution_failed) — {result['reason']}")
        with st.expander(f"시도한 SQL/에러 이력 ({len(result['attempts_log'])}회)"):
            for i, a in enumerate(result["attempts_log"], 1):
                st.write(f"**{i}차 시도**")
                st.code(a["sql"], language="sql")
                st.write(f"에러: {a['error']}")
        return

    # report_ready
    st.success(f"REPORT_READY — {result['report_name']} (생성까지 {result['attempts']}회 시도)")
    st.subheader("생성된 SQL")
    st.code(result["sql"], language="sql")
    st.subheader("실행 결과")
    rows = [dict(zip(result["columns"], row)) for row in result["rows"]]
    st.table(rows)


def run_sc001(to_be_column: str) -> dict:
    lookup_result = lookup_mapping_candidates(to_be_column)

    if lookup_result["status_hint"] in ("no_match", "version_mismatch"):
        return {"kind": "exception", "to_be_column": to_be_column, "lookup_result": lookup_result}

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

    return result


def run_sc001_reverse(as_is_column: str) -> dict:
    """SC-001 역방향(AS-IS -> TO-BE, 2026-09-08 추가). lookup_reverse_mapping은
    매핑정의서 역인덱스 조회라 후보 랭킹이 필요 없다(현재 데이터 기준 matches는
    0건 또는 1건) — filter_by_type/check_code_match/infer_secondary_evidence/
    judge_and_rank를 태우지 않고 바로 확정하거나 no_match/ambiguous로 끝낸다."""
    as_is_table, as_is_col = as_is_column.split(".", 1)
    reverse_result = lookup_reverse_mapping(as_is_table, as_is_col)

    if len(reverse_result["matches"]) != 1:
        return {"kind": "reverse_exception", "as_is_column": as_is_column, "reverse_result": reverse_result}

    winner_to_be_column = reverse_result["matches"][0]["to_be_column"]
    winner = {"table": as_is_table, "column": as_is_col}
    with st.spinner("근거 문장 생성 중 (Azure OpenAI 호출)..."):
        rationale = generate_rationale(winner_to_be_column, winner)

    return {
        "kind": "reverse_normal",
        "as_is_column": as_is_column,
        "to_be_column": winner_to_be_column,
        "rationale": rationale,
    }


def run_sc002(user_request: str, sc002_mode: str | None) -> dict:
    with st.spinner("search_schema 실행 중 (조인 규칙 조회 + SC-001 도구로 재검증)..."):
        schema_result = search_schema()

    if not schema_result["found"]:
        return {
            "kind": "report",
            "user_request": user_request,
            "status": "mapping_not_ready",
            "reason": schema_result.get("validation_error")
            or "사전 정의된 리포트/조인 규칙이 없음 — SC-001에서 매핑을 먼저 확정해야 함",
        }

    join_rule = schema_result["join_rule"]

    if sc002_mode == "explore":
        # 실제 쿼리를 만들거나 실행하지 않고, search_schema가 이미 검증해 둔
        # 테이블/컬럼 정보만 보여준다 — generate_sql/execute_sql은 부르지 않는다.
        return {
            "kind": "report",
            "user_request": user_request,
            "status": "schema_found",
            "report_name": join_rule["report_name"],
            "report_columns": [c["field"] for c in join_rule["report_columns"]],
            "sources": [
                {
                    "to_be_table": s["to_be_table"],
                    "income_type_label": s["income_type_label"],
                    "column_map": s["column_map"],
                }
                for s in join_rule["sources"]
            ],
        }

    schema_chunks = schema_result["schema_chunks"]
    sql_error = None
    attempts_log = []

    for attempt in range(1, MAX_SQL_ATTEMPTS + 1):
        with st.spinner(f"generate_sql 실행 중 (Azure OpenAI 호출, {attempt}/{MAX_SQL_ATTEMPTS}회차)..."):
            sql = generate_sql(schema_chunks, user_request, sql_error)["sql"]

        validation = validate_readonly(sql)
        if not validation["is_valid"]:
            return {
                "kind": "report",
                "user_request": user_request,
                "status": "readonly_violation",
                "reason": validation["reason"],
                "sql": sql,
            }

        with st.spinner(f"execute_sql 실행 중 ({attempt}/{MAX_SQL_ATTEMPTS}회차)..."):
            exec_result = execute_sql(sql)

        if exec_result["error"] is None:
            return {
                "kind": "report",
                "user_request": user_request,
                "status": "report_ready",
                "report_name": join_rule["report_name"],
                "sql": sql,
                "columns": exec_result["columns"],
                "rows": exec_result["rows"],
                "attempts": attempt,
            }

        attempts_log.append({"sql": sql, "error": exec_result["error"]})
        sql_error = exec_result["error"]

    return {
        "kind": "report",
        "user_request": user_request,
        "status": "sql_execution_failed",
        "reason": f"{MAX_SQL_ATTEMPTS}회 재시도해도 SQL 실행 실패 — 마지막 에러: {sql_error}",
        "attempts_log": attempts_log,
    }


st.set_page_config(page_title="SchemaBridge", page_icon="🔗", layout="centered")

st.title("SchemaBridge")
st.caption("AS-IS/TO-BE 매핑 판단 지원 Agent — 시연용 뷰어 (제품 UI 아님, 2주차 범위정의상 비개발자용 UI는 Out of Scope)")

user_request = st.text_input(
    "요청을 입력하세요 (컬럼 매핑 질문 또는 신고서 집계 요청)",
    placeholder="예: ACC_WHT_AGG.wht_tax_amt / 2026년 1분기 원천세(배당·기타·사업소득) 신고서용 집계 데이터 뽑아줘",
)
st.caption(
    "SC-001 예시(정방향, TO-BE→AS-IS): ACC_WHT_AGG.pay_dt / income_type_cd / wht_tax_amt / "
    "settle_method_cd(되묻기 시연용) / div_wht_amt / reg_dt / updt_dt, 또는 \"원천징수세액이 "
    "어디서 오는지 알려줘\"처럼 자연어로 물어봐도 됩니다.  \n"
    "SC-001 예시(역방향, AS-IS→TO-BE, 2026-09-08 추가): \"LBR_WHT.wht_amt가 TO-BE 어디로 "
    "매핑돼?\", 또는 \"이 AS-IS 컬럼(BIZ_INCOME.income_cd) 매핑되는 TO-BE 컬럼 찾아줘\"처럼 "
    "자연어로도 물어볼 수 있습니다. 컬럼명만으로 방향/테이블을 특정할 수 없으면 "
    "다시 질문해달라고 안내합니다.  \n"
    "SC-002 예시(execute, 기간 미지정 전체 조회): \"원천세(배당·기타·사업소득) 신고서용 전체 집계 "
    "데이터 뽑아줘\"  \n"
    "SC-002 예시(execute, 기간 필터 — 데모 데이터는 2025년 4분기~2026년 2분기): "
    "\"2026년 1분기 원천세 신고서용 집계 데이터 뽑아줘\"  \n"
    "SC-002 예시(explore, 테이블 정보만): \"원천세 집계 관련된 테이블 다 찾아줘\""
)

if st.button("실행", type="primary") and user_request:
    with st.spinner("classify_intent 실행 중 (Azure OpenAI 호출)..."):
        classified = classify_intent(user_request)

    if classified["intent"] == "SC-001":
        if classified["to_be_column"]:
            st.session_state.result = run_sc001(classified["to_be_column"])
        elif classified["as_is_column"]:
            st.session_state.result = run_sc001_reverse(classified["as_is_column"])
        else:
            st.session_state.result = {"kind": "direction_ambiguous"}
    else:
        st.session_state.result = run_sc002(user_request, classified["sc002_mode"])

# 렌더링은 버튼 클릭 여부와 무관하게 항상 session_state를 기준으로 그린다 —
# "답변 제출" 버튼을 눌러 재실행됐을 때도 지금까지의 진행 상황이 이어지도록.
if "result" in st.session_state:
    render_result(st.session_state.result)
