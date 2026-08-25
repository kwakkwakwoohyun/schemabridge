"""데모 데이터(스키마/매핑정의서/코드매핑) 로더. 전부 JSON 기반의 정확 조회 대상."""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_schema() -> dict:
    with open(os.path.join(DATA_DIR, "schema.json"), encoding="utf-8") as f:
        return json.load(f)


def load_mapping_definition() -> dict:
    with open(os.path.join(DATA_DIR, "mapping_definition.json"), encoding="utf-8") as f:
        return json.load(f)


def load_code_mapping() -> dict:
    with open(os.path.join(DATA_DIR, "code_mapping.json"), encoding="utf-8") as f:
        return json.load(f)


def get_column_info(schema: dict, side: str, table: str, column: str) -> dict | None:
    """side: 'TO-BE' or 'AS-IS'. 존재하지 않으면 None (버전 불일치 감지에 사용)."""
    table_info = schema.get(side, {}).get(table)
    if table_info is None:
        return None
    return table_info.get("columns", {}).get(column)
