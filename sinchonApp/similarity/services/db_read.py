# similarity/services/db_read.py
from django.db import connection
from prometheus_client import Histogram
import time

DB_READ_LATENCY = Histogram(
    "db_read_latency_seconds",
    "Latency of DB read queries for wasscam_post",
    ["query_type"]
)

def fetch_cases_by_tag(tag: str):
    sql = """
    SELECT id, category, title, content, created_at
    FROM wasscam_post
    WHERE category = %s
    ORDER BY created_at DESC
    LIMIT 200;  -- 더미 적을 땐 넉넉히 불러오기
    """
    start_time = time.time()
    try:
        with connection.cursor() as cur:
            cur.execute(sql, [tag])
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        DB_READ_LATENCY.labels(query_type="fetch_cases_by_tag").observe(
            time.time() - start_time
        )
