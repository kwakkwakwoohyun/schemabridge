"""
Node: generate_rationale

confirmed으로 판정된 매핑에 대해, 내부 판정 로직(confidence_gap 퍼센트, matched_keys
같은 기술적 계산값)과 분리된, 사람이 읽기 좋은 근거 문장을 LLM으로 생성한다
(4주차_완료.md 아키텍처 "출력 파싱 — 내부 추론과 사용자 노출용 근거 분리" 원칙,
도구 명세: 입력 ranked_result/status/clarification_answers(optional), 출력 rationale: str).

판정 경로(단일 후보 / 코드값 일치 / LLM 점수 확정 / 되묻기 후 확정)에 상관없이
이 노드 하나가 최종 사용자 노출용 문장을 만든다.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.llm_client import chat_completion_json

RATIONALE_SCHEMA = {
    "name": "mapping_rationale",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "rationale": {"type": "string"},
        },
        "required": ["rationale"],
        "additionalProperties": False,
    },
}


def generate_rationale(
    to_be_column: str,
    winner: dict,
    ranked_result: list[dict] | None = None,
    clarification_answers: list[str] | None = None,
) -> str:
    winner_line = f"{winner['table']}.{winner['column']}"
    ranked_lines = (
        "\n".join(
            f"- {r['table']}.{r['column']} (점수: {r['score']}, 근거 출처: {r['evidence_source']}, "
            f"근거: {r['rationale']})"
            for r in ranked_result
        )
        if ranked_result
        else "(비교 대상 없이 후보가 하나이거나, 코드값이 유일하게 일치해 확정됨)"
    )
    clarification_note = (
        "\n\n담당자에게 되물어 받은 답변: " + " / ".join(clarification_answers)
        if clarification_answers
        else ""
    )

    prompt = (
        f"TO-BE 컬럼 '{to_be_column}'은 AS-IS 컬럼 '{winner_line}'로 매핑이 확정됐다.\n"
        f"판정 근거:\n{ranked_lines}{clarification_note}\n\n"
        "이 판정을 매핑정의서를 검토하는 개발자에게 보여줄 한두 문장짜리 근거 설명을 "
        "작성해라. 점수·퍼센트·임계값 같은 내부 계산값을 그대로 나열하지 말고, "
        "왜 이 AS-IS 컬럼이 업무적으로 맞는지를 자연스러운 문장으로 설명해라."
    )

    result = chat_completion_json(
        messages=[
            {
                "role": "system",
                "content": "너는 AS-IS/TO-BE 컬럼 매핑 확정 결과를 개발자에게 설명하는 어시스턴트다. "
                "내부 계산 로직을 그대로 노출하지 않고, 업무적으로 납득되는 근거를 간결하게 전달한다.",
            },
            {"role": "user", "content": prompt},
        ],
        schema=RATIONALE_SCHEMA,
    )
    return result["rationale"]
