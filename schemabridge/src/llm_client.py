"""
Azure OpenAI 공용 클라이언트 래퍼.

LLM이 필요한 노드(infer_secondary_evidence, judge_and_rank, request_clarification,
generate_rationale)가 전부 이 모듈을 통해서만 Azure OpenAI를 호출한다.
- chat_completion_json: Structured Output(JSON Schema strict mode) 호출.
  gpt-5.6-luna 배포가 이 방식을 지원하는지는 4주차_완료.md에 기록된 테스트로 검증됨.
- embed: 임베딩 호출(text-embedding-3-large).

.env는 schemabridge/.env 하나만 사용(gitignore 처리됨, 저장소에 값이 커밋되지 않음).
"""

import os

from dotenv import load_dotenv
from openai import AzureOpenAI

_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")

_client: AzureOpenAI | None = None


def get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        load_dotenv(_ENV_PATH)
        _client = AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=os.environ["AZURE_OPENAI_API_VERSION"],
        )
    return _client


def chat_completion_json(messages: list[dict], schema: dict) -> dict:
    """Structured Output으로 호출하고 파싱된 dict를 반환한다.

    schema는 {"name": ..., "strict": True, "schema": {...}} 형태
    (json_schema 필드 안쪽 내용, response_format 래핑은 여기서 처리).
    """
    import json

    client = get_client()
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]

    resp = client.chat.completions.create(
        model=deployment,
        messages=messages,
        response_format={"type": "json_schema", "json_schema": schema},
    )
    return json.loads(resp.choices[0].message.content)


def embed(texts: list[str]) -> list[list[float]]:
    client = get_client()
    deployment = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME"]

    resp = client.embeddings.create(model=deployment, input=texts)
    return [item.embedding for item in resp.data]
