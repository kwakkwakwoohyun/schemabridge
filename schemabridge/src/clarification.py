"""
Node: request_clarification

judge_and_rank가 confirmed를 못 내면(ambiguous/insufficient_metadata), 사람에게
현재 후보와 근거를 구체적으로 보여주고 답을 받아 점수를 재산정한다(4주차_완료.md v3 설계).
최대 재시도 횟수는 MAX_ATTEMPTS로 두 진입점(graph.py, app.py)이 공유한다.

이 모듈은 "질문을 만들고" "답변으로 재점수화하는" 로직만 담당한다. 실제로 사람에게
묻고 답을 받는 방식(터미널 input() / Streamlit 위젯)은 호출하는 쪽(graph.py, app.py)이
각자의 인터페이스에 맞게 처리한다.

build_clarification_question은 단순히 후보를 나열하고 "힌트를 달라"고만 하면 사용자가
무슨 답을 해야 할지 감을 못 잡는다는 피드백에 따라(2026-09-05), 후보 간에 실제로
갈리는 지점이 뭔지 LLM이 판단해서 사람이 바로 답할 수 있는 구체적인 질문
(예: "이 값은 급여 원천징수에서 온 건가요, 이자소득 원천징수에서 온 건가요?")을
생성하도록 만들었다.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.llm_client import chat_completion_json

MAX_ATTEMPTS = 2

QUESTION_SCHEMA = {
    "name": "clarification_question",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
}

CLARIFICATION_SCHEMA = {
    "name": "clarification_rescore",
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


def build_clarification_question(to_be_column: str, ranked_result: list[dict]) -> str:
    candidate_lines = [
        f"- {r['table']}.{r['column']} (점수: {r['score']}, 근거: {r['rationale']})" for r in ranked_result
    ]
    prompt = (
        f"TO-BE 컬럼 '{to_be_column}'에 대한 AS-IS 매핑 후보들의 점수가 비슷해서 확정하지 못했다:\n"
        + "\n".join(candidate_lines)
        + "\n\n이 후보들을 구분하려면 업무 담당자(비개발자일 수 있음)에게 뭘 물어봐야 할지, "
        "바로 답할 수 있는 구체적인 질문을 하나만 만들어라. 테이블/컬럼명을 그대로 언급하지 "
        "말고, 이 값이 실제로 어떤 업무 상황·출처에서 오는지 묻는 형태로 만들어라 "
        "(예: '이 값은 급여 원천징수에서 온 건가요, 이자소득 원천징수에서 온 건가요?'). "
        "위 근거(rationale)에 힌트가 있으면 그걸 활용해 후보 간에 실제로 갈리는 지점을 짚어라."
    )

    result = chat_completion_json(
        messages=[
            {
                "role": "system",
                "content": "너는 AS-IS/TO-BE 컬럼 매핑을 판단하기 위해 업무 담당자에게 "
                "구체적으로 되묻는 질문을 설계하는 어시스턴트다.",
            },
            {"role": "user", "content": prompt},
        ],
        schema=QUESTION_SCHEMA,
    )
    return result["question"]


def rescore_with_clarification(to_be_column: str, ranked_result: list[dict], answer: str) -> list[dict]:
    candidate_lines = [
        f"- {r['table']}.{r['column']} (기존 점수: {r['score']}, 기존 근거: {r['rationale']})"
        for r in ranked_result
    ]
    prompt = (
        f"TO-BE 컬럼 '{to_be_column}'에 대한 AS-IS 매핑 후보들이다:\n"
        + "\n".join(candidate_lines)
        + f'\n\n사용자에게 구분 힌트를 물어봤고, 다음과 같이 답했다: "{answer}"\n\n'
        "이 답변을 반영해 각 후보의 점수(0~1)와 근거를 다시 매겨라. 답변이 특정 후보를 "
        "명확히 가리키면 그 후보 점수를 크게 높이고 나머지는 낮춰라. 답변이 여전히 "
        "도움이 안 되면 기존 점수와 비슷하게 유지해도 된다."
    )

    result = chat_completion_json(
        messages=[
            {
                "role": "system",
                "content": "너는 AS-IS/TO-BE 컬럼 매핑을 추론하는 어시스턴트다. "
                "사용자의 보충 설명을 반영해 후보 점수를 재산정한다.",
            },
            {"role": "user", "content": prompt},
        ],
        schema=CLARIFICATION_SCHEMA,
    )

    return [
        {
            "table": item["table"],
            "column": item["column"],
            "score": item["score"],
            "evidence_source": "clarification_response",
            "rationale": item["rationale"],
        }
        for item in result["candidates"]
    ]
