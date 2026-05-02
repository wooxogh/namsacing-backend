"""M1 + M5: measure latency of fetch_candidates_mysql's two paths.

Two paths in production code (sinchonApp/similarity/services/db_search.py):

  Path A (intended FULLTEXT):
      ORDER BY MATCH(title, body) AGAINST (%s IN NATURAL LANGUAGE MODE)

  Path B (fallback, on exception):
      ORDER BY created_at DESC

This script reproduces both queries against the bench DB and reports
latency at K ∈ {20, 50, 100} and N ∈ {present row count}.

Three-way comparison:
  A1) production SQL exactly as written (MATCH(title, body)) — expected to
      error because column is `content`, not `body`. We capture the error.
  A2) corrected SQL (MATCH(title, content)) — what production WOULD do if
      the typo were fixed. This is the realistic FULLTEXT measurement.
  B)  fallback SQL — what production currently runs every request because
      A1 always errors.

Output: bench/results/m1_db_search.json
"""
from __future__ import annotations

import json
import os
import random
import statistics
import time
from datetime import datetime

import pymysql

from config import DB, RESULTS_DIR


SQL_PROD_FULLTEXT = """
SELECT id, category, title, content, created_at
FROM wasscam_post
WHERE category = %s
ORDER BY MATCH(title, body) AGAINST (%s IN NATURAL LANGUAGE MODE) DESC, created_at DESC
LIMIT %s
"""

SQL_CORRECTED_FULLTEXT = """
SELECT id, category, title, content, created_at
FROM wasscam_post
WHERE category = %s
ORDER BY MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) DESC, created_at DESC
LIMIT %s
"""

SQL_FALLBACK = """
SELECT id, category, title, content, created_at
FROM wasscam_post
WHERE category = %s
ORDER BY created_at DESC
LIMIT %s
"""

CATEGORIES = ["보이스피싱", "종교", "사기", "마약", "기타"]
QUERY_TERMS = [
    "택배 검찰 명의도용",
    "안전계좌 이체 확인",
    "신천지 모임 카톡",
    "중고나라 선입금 환불",
    "필로폰 텔레그램 거래",
    "보험금 환급 본인확인",
]


def time_query(cur, sql: str, params: tuple) -> tuple[float, int, str | None]:
    """Execute once. Return (elapsed_seconds, row_count, error_or_none)."""
    t0 = time.perf_counter()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        return (time.perf_counter() - t0, len(rows), None)
    except Exception as e:
        return (time.perf_counter() - t0, 0, f"{type(e).__name__}: {e}")


def percentiles(samples: list[float]) -> dict:
    s = sorted(samples)
    n = len(s)
    if not n:
        return {"n": 0}

    def pct(p):
        # nearest-rank
        k = max(0, min(n - 1, int(round(p * (n - 1)))))
        return s[k]

    return {
        "n": n,
        "min_ms": round(s[0] * 1000, 2),
        "p50_ms": round(pct(0.50) * 1000, 2),
        "p90_ms": round(pct(0.90) * 1000, 2),
        "p99_ms": round(pct(0.99) * 1000, 2),
        "max_ms": round(s[-1] * 1000, 2),
        "mean_ms": round(statistics.mean(s) * 1000, 2),
        "stdev_ms": round(statistics.stdev(s) * 1000, 2) if n > 1 else 0,
    }


def warmup(cur, sql: str, params: tuple, n: int = 5):
    for _ in range(n):
        try:
            cur.execute(sql, params)
            cur.fetchall()
        except Exception:
            pass


def measure_path(cur, name: str, sql: str, k_values: list[int], iters: int) -> dict:
    print(f"  [{name}]")
    results: dict = {"name": name, "sql": " ".join(sql.split()), "by_k": {}}
    for k in k_values:
        latencies: list[float] = []
        rows_seen: list[int] = []
        first_error: str | None = None

        # warmup
        cat = random.choice(CATEGORIES)
        q = random.choice(QUERY_TERMS)
        warmup(cur, sql, (cat, q, k) if "%s" in sql.split("WHERE")[1] and sql.count("%s") == 3 else (cat, k))

        for i in range(iters):
            cat = random.choice(CATEGORIES)
            q = random.choice(QUERY_TERMS)
            params = (cat, q, k) if sql.count("%s") == 3 else (cat, k)
            elapsed, n_rows, err = time_query(cur, sql, params)
            latencies.append(elapsed)
            rows_seen.append(n_rows)
            if err and first_error is None:
                first_error = err

        results["by_k"][k] = {
            **percentiles(latencies),
            "rows_returned_avg": round(statistics.mean(rows_seen), 1),
            "first_error": first_error,
        }
        print(f"    K={k:>3}: p50={results['by_k'][k]['p50_ms']:>6}ms "
              f"p99={results['by_k'][k]['p99_ms']:>6}ms "
              f"rows~{results['by_k'][k]['rows_returned_avg']:.0f} "
              f"{'ERR' if first_error else 'OK'}")
    return results


def db_size(cur) -> int:
    cur.execute("SELECT COUNT(*) AS c FROM wasscam_post")
    return cur.fetchone()["c"]


def measure_prod_function(k_values: list[int], iters: int) -> dict:
    """Call the actual production fetch_candidates_mysql via Django ORM connection.

    This captures real-world overhead (try/except, dict zip, ORM cursor) on
    top of the raw SQL latency. This is the number the CV's '~90ms' claim
    should be compared against.
    """
    import django_setup  # noqa: F401  (side-effect import)
    from similarity.services.db_search import fetch_candidates_mysql

    print("  [prod_fetch_candidates_mysql via Django]")
    out: dict = {"by_k": {}}
    # warmup
    for _ in range(5):
        fetch_candidates_mysql(random.choice(CATEGORIES), random.choice(QUERY_TERMS), 20)

    for k in k_values:
        latencies: list[float] = []
        rows_seen: list[int] = []
        for _ in range(iters):
            cat = random.choice(CATEGORIES)
            q = random.choice(QUERY_TERMS)
            t0 = time.perf_counter()
            rows = fetch_candidates_mysql(cat, q, k)
            latencies.append(time.perf_counter() - t0)
            rows_seen.append(len(rows))
        out["by_k"][k] = {
            **percentiles(latencies),
            "rows_returned_avg": round(statistics.mean(rows_seen), 1),
        }
        print(f"    K={k:>3}: p50={out['by_k'][k]['p50_ms']:>6}ms "
              f"p99={out['by_k'][k]['p99_ms']:>6}ms "
              f"rows~{out['by_k'][k]['rows_returned_avg']:.0f}")
    return out


def main():
    random.seed(7)
    iters = int(os.getenv("BENCH_ITERS", "50"))
    k_values = [20, 50, 100]

    conn = pymysql.connect(**DB)
    out = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "iters_per_k": iters,
        "k_values": k_values,
    }
    try:
        with conn.cursor() as cur:
            n = db_size(cur)
            out["db_rows"] = n
            print(f"[db] {n} rows in wasscam_post, iters={iters}")

            print("[measure] production FULLTEXT (MATCH(title, body)) — expected to ERROR")
            out["path_A1_prod_fulltext"] = measure_path(
                cur, "prod_fulltext_typo", SQL_PROD_FULLTEXT, k_values, iters
            )

            print("[measure] corrected FULLTEXT (MATCH(title, content))")
            out["path_A2_corrected_fulltext"] = measure_path(
                cur, "corrected_fulltext", SQL_CORRECTED_FULLTEXT, k_values, iters
            )

            print("[measure] fallback (created_at DESC)")
            out["path_B_fallback"] = measure_path(
                cur, "fallback_recent", SQL_FALLBACK, k_values, iters
            )

            # EXPLAIN for the corrected FULLTEXT and fallback to prove index usage
            cur.execute(
                "EXPLAIN " + SQL_CORRECTED_FULLTEXT,
                ("보이스피싱", "택배 검찰", 20),
            )
            out["explain_corrected_fulltext"] = cur.fetchall()
            cur.execute("EXPLAIN " + SQL_FALLBACK, ("보이스피싱", 20))
            out["explain_fallback"] = cur.fetchall()
    finally:
        conn.close()

    print("[measure] production fetch_candidates_mysql() via Django (real path)")
    out["path_PROD_fn"] = measure_prod_function(k_values, iters)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "m1_db_search.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n→ wrote {out_path}")


if __name__ == "__main__":
    main()
