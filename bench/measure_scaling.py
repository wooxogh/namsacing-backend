"""M5: how does fetch_candidates_mysql latency scale with table size?

Reseeds the table at N ∈ {100, 1000, 10000, 50000} and measures the
production fetch_candidates_mysql function latency at K=20 each time.
This generates the scalability curve the CV claim should be evaluated against.

Output: bench/results/m5_scaling.json
"""
from __future__ import annotations

import json
import os
import random
import statistics
import subprocess
import sys
import time
from datetime import datetime

import django_setup  # noqa: F401

from similarity.services.db_search import fetch_candidates_mysql
from config import RESULTS_DIR


CATEGORIES = ["보이스피싱", "종교", "사기", "마약", "기타"]
QUERY_TERMS = [
    "택배 검찰 명의도용", "안전계좌 이체 확인", "신천지 모임 카톡",
    "중고나라 선입금 환불", "필로폰 텔레그램 거래",
]


def percentiles(samples: list[float]) -> dict:
    s = sorted(samples)
    n = len(s)

    def pct(p):
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
    }


def measure(iters: int, k: int) -> dict:
    # warmup
    for _ in range(5):
        fetch_candidates_mysql(random.choice(CATEGORIES), random.choice(QUERY_TERMS), k)
    latencies = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fetch_candidates_mysql(random.choice(CATEGORIES), random.choice(QUERY_TERMS), k)
        latencies.append(time.perf_counter() - t0)
    return percentiles(latencies)


def main():
    random.seed(19)
    sizes = [int(s) for s in os.getenv("BENCH_SIZES", "100,1000,10000,50000").split(",")]
    iters = int(os.getenv("BENCH_ITERS", "30"))
    k = int(os.getenv("BENCH_K", "20"))

    venv_python = sys.executable
    seed_path = os.path.join(os.path.dirname(__file__), "seed.py")
    out = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "k": k,
        "iters_per_size": iters,
        "by_size": {},
    }
    for n in sizes:
        print(f"[reseed] {n} posts")
        subprocess.run(
            [venv_python, seed_path, "--reset", "--posts", str(n), "--phones", "100"],
            check=True, cwd=os.path.dirname(__file__),
        )
        # Force a fresh Django connection so any cursor caching doesn't carry over
        from django.db import connection
        connection.close()

        print(f"[measure] N={n}, K={k}, iters={iters}")
        p = measure(iters, k)
        out["by_size"][n] = p
        print(f"  p50={p['p50_ms']:>6}ms  p99={p['p99_ms']:>6}ms")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "m5_scaling.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n→ wrote {out_path}")


if __name__ == "__main__":
    main()
