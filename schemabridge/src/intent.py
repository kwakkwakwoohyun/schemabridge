"""
Node: classify_intent

그래프의 첫 진입점. 사용자 입력이 SC-001(TO-BE 컬럼 매핑 조회)인지 SC-002
(신고서용 집계 쿼리 요청)인지 분류한다(4주차_완료.md Step 1).

**입력의 겉모습(단일 식별자 vs 자연어 문장)이 아니라 의미로 판단한다.** "wht_tax_amt가
어디서 오는지 알려줘"처럼 자연어 문장이어도 특정 TO-BE 컬럼 하나의 매핑 근거를 묻는
질문이면 SC-001이고, "ACC_WHT_AGG.wht_tax_amt"처럼 식별자 형태가 아니라도 매핑
질문이라면 여전히 SC-001이다. 반대로 여러 컬럼을 모아 집계·리포트를 만들어달라는
요청이면 문장 형태와 무관하게 SC-002다. 그래서 프롬프트에 TO-BE 스키마(컬럼명+설명)를
같이 줘서, LLM이 자연어로 언급된 컬럼도 실제 식별자로 정규화할 수 있게 한다
(예: "원천징수세액" → ACC_WHT_AGG.wht_tax_amt).

기존 SC-001 골든셋(14건)이 전부 TABLE.column 형태라, 이 노드가 잘못 분류하면
회귀가 깨진다 — 그래서 구현 후 반드시 골든셋 전체로 회귀 테스트한다
(schemabridge/tests/test_deterministic.py는 이 노드 이전 단계만 다루므로 별도로
python3 src/graph.py <컬럼>으로 확인해야 함).

**2026-09-07 추가 — SC-002 안에서도 "탐색" vs "실행"을 구분한다(`sc002_mode`).**
처음엔 SC-002로 분류되면 무조건 search_schema -> generate_sql -> execute_sql까지
끝까지 실행해서 SQL과 결과를 보여줬는데, "원천세 집계 관련된 테이블 다 찾아줘"처럼
실제로는 어떤 테이블/컬럼이 관련 있는지 알고 싶을 뿐 쿼리 실행 결과까지는
필요 없는 요청도 SC-002로 분류되고 나면 항상 쿼리까지 만들어 실행해버리는 게
사용자 지적으로 드러났다. 그래서 SC-002일 때 sc002_mode를 추가로 분류한다:
"explore"(테이블/컬럼 정보만 알고 싶은 요청 — "찾아줘", "뭐가 관련있어",
"테이블 알려줘")면 search_schema 결과만 보여주고 generate_sql/execute_sql은
건너뛰고, "execute"(실제 집계 결과를 원하는 요청 — "뽑아줘", "집계해줘",
"만들어줘", "보고서 줘")면 기존처럼 끝까지 실행한다(src/graph.py,
src/schema_search.py, app.py 참고). SC-001이면 sc002_mode는 null.
"""

from src.data_loader import load_schema
from src.llm_client import chat_completion_json

INTENT_SCHEMA = {
    "name": "classified_intent",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["SC-001", "SC-002"]},
            "to_be_column": {"type": ["string", "null"]},
            "sc002_mode": {"type": ["string", "null"], "enum": ["explore", "execute", None]},
        },
        "required": ["intent", "to_be_column", "sc002_mode"],
        "additionalProperties": False,
    },
}


def _to_be_schema_context() -> str:
    schema = load_schema()
    lines = []
    for table, table_info in schema.get("TO-BE", {}).items():
        for column, col_info in table_info.get("columns", {}).items():
            lines.append(f"- {table}.{column}: {col_info.get('description') or '(설명 없음)'}")
    return "\n".join(lines)


def classify_intent(user_request: str) -> dict:
    prompt = (
        f"사용자 입력: \"{user_request}\"\n\n"
        "이 입력이 다음 중 무엇을 원하는지 의미로 판단해라(문장이냐 식별자냐 같은 "
        "겉모습으로 판단하지 마라):\n"
        "- SC-001: TO-BE 컬럼 하나가 AS-IS의 어느 컬럼에서 오는지(매핑 근거)를 묻는 요청. "
        "\"ACC_WHT_AGG.wht_tax_amt\"처럼 식별자 그대로 줄 수도 있고, \"원천징수세액이 "
        "어디서 오는거야?\", \"wht_tax_amt 매핑 좀 확인해줘\"처럼 자연어 문장으로 특정 "
        "컬럼 하나를 콕 집어 물어볼 수도 있다 — 문장이어도 SC-001이다.\n"
        "- SC-002: 여러 컬럼을 모아 신고서/집계 데이터(표 형태의 리포트)를 만들어달라는 요청.\n\n"
        "SC-001로 판단되면 to_be_column에 실제 TO-BE 컬럼 식별자('TABLE.column' 형태)를 "
        "채워라 — 사용자가 식별자를 그대로 안 쓰고 한글 설명이나 컬럼명만 언급했다면, "
        "아래 TO-BE 스키마를 참고해서 가장 일치하는 컬럼으로 정규화해라. SC-002로 "
        "판단되면 to_be_column은 null로 둬라.\n\n"
        "SC-002로 판단되면 추가로 sc002_mode도 채워라(SC-001이면 sc002_mode는 null):\n"
        "- \"explore\": 실제 데이터 결과가 아니라 어떤 테이블/컬럼이 관련 있는지 정보만 "
        "알고 싶은 요청. 예: \"원천세 집계 관련된 테이블 다 찾아줘\", \"어떤 테이블들이 "
        "관련있어?\", \"소득 관련 테이블 알려줘\"\n"
        "- \"execute\": 실제 집계된 데이터 결과(표)를 원하는 요청. 예: \"뽑아줘\", "
        "\"집계해줘\", \"신고서용 데이터 만들어줘\", \"보고서 줘\"\n\n"
        f"TO-BE 스키마(컬럼명: 설명):\n{_to_be_schema_context()}"
    )

    result = chat_completion_json(
        messages=[
            {
                "role": "system",
                "content": "너는 SchemaBridge Agent의 입력을 SC-001/SC-002 중 하나로 분류하는 "
                "라우터다. 입력의 형태가 아니라 실제로 뭘 원하는지(단일 컬럼의 매핑 근거 vs "
                "여러 컬럼을 모은 집계 리포트)를 보고 판단한다.",
            },
            {"role": "user", "content": prompt},
        ],
        schema=INTENT_SCHEMA,
    )
    return result
