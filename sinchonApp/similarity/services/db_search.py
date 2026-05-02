# similarity/services/db_search.py
from django.db import connection
from prometheus_client import Histogram, Counter
import time

DB_SEARCH_LATENCY = Histogram(
    "db_search_latency_seconds",
    "Latency of DB candidate search queries",
    ["path"]
)

DB_SEARCH_FALLBACK_TOTAL = Counter(
    "db_search_fallback_total",
    "Number of times DB search fell back from FULLTEXT to recent-order query"
)

def fetch_candidates_mysql(category: str, query_text: str, limit: int = 20):
    """
    1순위: MySQL FULLTEXT(title, content) 점수로 상위 K개
    2순위: FULLTEXT 미구성/오류 시 -> 최신순 LIMIT K (카테고리 필수)
    """
    q = (query_text or "").strip()

    # FULLTEXT 경로 latency 측정
    start_time = time.time()
    try:
        sql = """
        SELECT id, category, title, content, created_at
        FROM wasscam_post
        WHERE category = %s
        ORDER BY MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) DESC, created_at DESC
        LIMIT %s;
        """
        with connection.cursor() as cur:
            cur.execute(sql, [category, q, limit])
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        if not rows:
            raise RuntimeError("no rows from fulltext")

        return rows

    except Exception:
        DB_SEARCH_FALLBACK_TOTAL.inc()
        # fallback 경로 latency 측정
        fallback_start = time.time()
        sql = """
        SELECT id, category, title, content, created_at
        FROM wasscam_post
        WHERE category = %s
        ORDER BY created_at DESC
        LIMIT %s;
        """
        with connection.cursor() as cur:
            cur.execute(sql, [category, limit])
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    finally:
        elapsed = time.time() - start_time
        DB_SEARCH_LATENCY.labels(path="fulltext_or_fallback").observe(elapsed)