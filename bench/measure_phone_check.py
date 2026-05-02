"""M2: latency of similarity.services.phone_check.check_spam_number.

Three scenarios:
  - cache_hit:  number exists in phone_checks → returns immediately
  - cache_miss_no_key: number missing AND APICK_API_KEY empty → "none" branch
                      (no external call). This is the realistic CI path.
  - cache_miss_mocked_api: number missing AND we monkeypatch requests.post
                           to return a synthetic apick response. Measures
                           the API → upsert → return code path WITHOUT actual
                           network cost.

Output: bench/results/m2_phone_check.json
"""
from __future__ import annotations

import json
import os
import random
import statistics
import time
from datetime import datetime
from unittest.mock import patch

import django_setup  # noqa: F401  side-effect

import pymysql
from similarity.services import phone_check as pc
from similarity.models import PhoneCheck
from config import DB, RESULTS_DIR


def percentiles(samples: list[float]) -> dict:
    s = sorted(samples)
    n = len(s)
    if not n:
        return {"n": 0}

    def pct(p):
        k = max(0, min(n - 1, int(round(p * (n - 1)))))
        return s[k]

    return {
        "n": n,
        "min_ms": round(s[0] * 1000, 3),
        "p50_ms": round(pct(0.50) * 1000, 3),
        "p90_ms": round(pct(0.90) * 1000, 3),
        "p99_ms": round(pct(0.99) * 1000, 3),
        "max_ms": round(s[-1] * 1000, 3),
        "mean_ms": round(statistics.mean(s) * 1000, 3),
    }


def cached_numbers(limit: int) -> list[str]:
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT number FROM phone_checks LIMIT %s", (limit,))
            return [r["number"] for r in cur.fetchall()]
    finally:
        conn.close()


def fresh_numbers(n: int, existing: set[str]) -> list[str]:
    out = []
    while len(out) < n:
        cand = "010" + f"{random.randint(0, 99999999):08d}"
        if cand not in existing:
            out.append(cand)
            existing.add(cand)
    return out


class FakeResp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def fake_apick_post(*args, **kwargs):
    # 30% spam, 70% clean — mimics realistic apick distribution
    if random.random() < 0.3:
        return FakeResp({"data": {
            "success": 1, "spam": "보이스피싱",
            "spam_count": str(random.randint(1, 800)),
            "registed_date": "2024-05", "cyber_crime": None,
        }})
    return FakeResp({"data": {
        "success": 1, "spam": None, "spam_count": "0",
        "registed_date": None, "cyber_crime": None,
    }})


def measure(label: str, callable_factory, iters: int) -> dict:
    print(f"  [{label}]")
    # Warmup
    for _ in range(5):
        callable_factory()()
    latencies = []
    for _ in range(iters):
        fn = callable_factory()
        t0 = time.perf_counter()
        fn()
        latencies.append(time.perf_counter() - t0)
    p = percentiles(latencies)
    print(f"    p50={p['p50_ms']:>7}ms  p90={p['p90_ms']:>7}ms  "
          f"p99={p['p99_ms']:>7}ms  n={p['n']}")
    return p


def main():
    random.seed(11)
    iters = int(os.getenv("BENCH_ITERS", "200"))
    out = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "iters": iters,
        "phone_check_rows": PhoneCheck.objects.count(),
    }
    print(f"[db] {out['phone_check_rows']} cached rows, iters={iters}")

    cached = cached_numbers(min(out["phone_check_rows"], iters * 2))
    if not cached:
        raise SystemExit("no cached rows; run seed.py first")
    existing = set(cached)

    # 1) cache hit
    def hit_factory():
        n = random.choice(cached)
        return lambda: pc.check_spam_number(n, use_cache=True)

    out["cache_hit"] = measure("cache_hit", hit_factory, iters)

    # 2) cache miss, no APICK key (real CI behavior with empty .env)
    def miss_no_key_factory():
        # Generate a brand-new number for each call so no cache row interferes.
        n = fresh_numbers(1, existing)[0]
        return lambda: pc.check_spam_number(n, use_cache=False)

    # Ensure APICK_KEY constant in the imported module is empty for this case
    pc.APICK_KEY = ""
    out["cache_miss_no_key"] = measure("cache_miss_no_key", miss_no_key_factory, iters)

    # 3) cache miss, mocked APICK API (measures upsert+JSON parse cost)
    pc.APICK_KEY = "test-key"  # any non-empty value triggers the API branch
    with patch.object(pc.requests, "post", side_effect=fake_apick_post):
        def miss_api_factory():
            n = fresh_numbers(1, existing)[0]
            return lambda: pc.check_spam_number(n, use_cache=False)

        out["cache_miss_mocked_api"] = measure(
            "cache_miss_mocked_api", miss_api_factory, iters
        )

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "m2_phone_check.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n→ wrote {out_path}")


if __name__ == "__main__":
    main()
