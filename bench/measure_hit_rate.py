"""M6: cache hit rate vs effective latency / external API call savings.

Production-side question: 캐시에 데이터가 N개밖에 없으면 effective latency 와
apick API 호출량은 hit rate 에 어떻게 의존하나?

Model:
  - 미리 N_warm 개의 "알려진 사기 번호" 를 캐시에 적재
  - 각 요청은 hit_rate 확률로 알려진 번호 (cache hit), 그 외엔 새 번호 (cache miss)
  - cache miss 는 mocked apick API → DB upsert 경로
  - 요청당 latency 측정, 평균/p50/p99 산출

Output: bench/results/m6_hit_rate.json + ASCII chart in stdout.
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
from similarity.utils.phone import normalize_kr_number
from config import DB, RESULTS_DIR


HIT_RATES = [0.0, 0.30, 0.50, 0.70, 0.80, 0.90, 0.95, 0.99]
N_WARM = 100        # 캐시에 미리 적재할 알려진 번호 수
N_REQUESTS = 500    # 각 hit_rate 별 요청 수
APICK_MOCK_LATENCY_MS = 200.0  # 실제 apick 평균 응답 시간 추정 (네트워크 포함)
                                # mock 으로 sleep 시뮬레이션. 실제 측정은 별도 프로젝트.


def fresh_phone(seen: set[str]) -> str:
    while True:
        n = normalize_kr_number("010" + f"{random.randint(0, 99999999):08d}")
        if n not in seen:
            seen.add(n)
            return n


def warm_cache(numbers: list[str]):
    """Pre-populate cache via direct DB insert (faster than going through apick)."""
    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM phone_checks")  # 깨끗한 시작
            now = datetime.now()
            rows = []
            for n in numbers:
                # 실제 분포 흉내 — 30% spam, 70% clean
                spam = random.choice(["보이스피싱", "사기", "광고"]) if random.random() < 0.3 else None
                spam_count = random.randint(1, 800) if spam else 0
                rows.append((
                    n, spam, str(spam_count) if spam_count else None, spam_count,
                    "2024-05", None, 1, now,
                ))
            cur.executemany(
                "INSERT INTO phone_checks (number, spam, spam_count_raw, spam_count, "
                "registed_date, cyber_crime, success, last_checked_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
    finally:
        conn.close()


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def fake_apick_with_latency(*args, **kwargs):
    # 200ms 추정 외부 API + 응답 — 실제 호출 비용 시뮬레이션
    time.sleep(APICK_MOCK_LATENCY_MS / 1000.0)
    spam = random.choice(["보이스피싱", "사기", None, None, None])
    return FakeResp({"data": {
        "success": 1, "spam": spam,
        "spam_count": str(random.randint(0, 500)),
        "registed_date": "2024-12", "cyber_crime": None,
    }})


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


def simulate(hit_rate: float, warm_pool: list[str]) -> dict:
    """Return measurement dict for one hit_rate level."""
    seen = set(warm_pool)
    latencies: list[float] = []
    apick_calls = 0
    cache_hits = 0

    pc.APICK_KEY = "test-key"  # API 경로 활성화
    with patch.object(pc.requests, "post", side_effect=fake_apick_with_latency) as m:
        for _ in range(N_REQUESTS):
            if random.random() < hit_rate:
                n = random.choice(warm_pool)
            else:
                n = fresh_phone(seen)
            t0 = time.perf_counter()
            r = pc.check_spam_number(n, use_cache=True)
            latencies.append(time.perf_counter() - t0)
            if r.source == "cache":
                cache_hits += 1
            elif r.source == "api":
                apick_calls += 1

    p = percentiles(latencies)
    p["actual_hit_rate"] = round(cache_hits / N_REQUESTS, 3)
    p["apick_calls"] = apick_calls
    p["apick_call_pct"] = round(apick_calls / N_REQUESTS * 100, 1)
    return p


def ascii_bar(value: float, max_value: float, width: int = 30) -> str:
    n = max(0, min(width, int(round(value / max_value * width))))
    return "█" * n + "░" * (width - n)


def main():
    random.seed(23)

    out = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "n_warm": N_WARM,
        "n_requests_per_level": N_REQUESTS,
        "apick_mock_latency_ms": APICK_MOCK_LATENCY_MS,
        "by_hit_rate": {},
    }

    print(f"[warm] populating cache with {N_WARM} known numbers")
    seen = set()
    warm_pool = [fresh_phone(seen) for _ in range(N_WARM)]
    warm_cache(warm_pool)

    print(f"[run] {len(HIT_RATES)} hit rate levels × {N_REQUESTS} requests, "
          f"apick mock = {APICK_MOCK_LATENCY_MS}ms\n")

    print(f"  {'hit_rate':>10} | {'p50':>8} | {'p99':>8} | {'mean':>8} | "
          f"{'apick%':>7} | {'mean latency chart':<35}")
    print("  " + "-" * 100)

    for hr in HIT_RATES:
        p = simulate(hr, warm_pool)
        out["by_hit_rate"][f"{hr:.2f}"] = p
        max_mean = APICK_MOCK_LATENCY_MS  # for the bar scale
        bar = ascii_bar(p["mean_ms"], max_mean)
        print(f"  {hr:>9.0%} | {p['p50_ms']:>6.2f}ms | {p['p99_ms']:>6.2f}ms | "
              f"{p['mean_ms']:>6.2f}ms | {p['apick_call_pct']:>6.1f}% | {bar}")

    # 비용 환산
    n_daily = 1000
    print(f"\n  [추정] 일 {n_daily} 요청 가정 시 apick 호출 횟수:")
    for hr in HIT_RATES:
        calls = int(n_daily * (1 - hr))
        print(f"    hit_rate {hr:.0%} → {calls:>4} apick calls/day")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "m6_hit_rate.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n→ wrote {out_path}")


if __name__ == "__main__":
    main()
