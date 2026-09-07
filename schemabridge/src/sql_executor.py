"""
Node: execute_sql

validate_readonly를 통과한 SQL을 data/demo.db(SQLite)에 실제로 실행한다.
validate_readonly가 이미 텍스트 수준에서 쓰기 쿼리를 걸러내지만, 여기서도
`PRAGMA query_only = ON`으로 한 번 더 방어한다(단일 검사에 기대지 않고
결정적 검사 + 실행 단계 방어를 이중으로 두는 방식 — judge.py의 top1 확신도
+ confidence_gap 이중 게이트와 같은 발상).

MAX_SQL_ATTEMPTS(self_correct 재시도 최대 횟수)를 여기 두고 graph.py(CLI)와
app.py(Streamlit) 양쪽이 그대로 import해서 같이 쓴다 — clarification.py의
MAX_ATTEMPTS와 같은 이유(숫자가 두 군데 따로 박혀서 하나만 바뀌는 사고 방지).
"""

import sqlite3

from data.setup_demo_db import ensure_demo_db

MAX_SQL_ATTEMPTS = 3


def execute_sql(sql: str) -> dict:
    db_path = ensure_demo_db()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = [list(r) for r in cursor.fetchall()]
        return {"columns": columns, "rows": rows, "error": None}
    except sqlite3.Error as e:
        return {"columns": [], "rows": [], "error": str(e)}
    finally:
        conn.close()
