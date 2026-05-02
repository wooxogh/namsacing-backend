"""M4: end-to-end AssessView with mocked LLM.

Calls the production AssessView through Django's RequestFactory, mocking
only the OpenAI call so the test is deterministic and offline. Measures
total latency AND per-stage breakdown by patching each service entry point
with a timing wrapper.

Output: bench/results/m4_pipeline.json
"""
from __future__ import annotations

import json
import os
import random
import statistics
import time
from datetime import datetime
from unittest.mock import patch

import django_setup  # noqa: F401

from django.test import RequestFactory

from similarity import views as views_mod
from similarity.views import AssessView
from similarity.services import db_search, phone_check
from similarity.services import llm as llm_mod
from similarity.utils import compact as compact_mod
from config import RESULTS_DIR


PAYLOADS = [
    {
        "category": "보이스피싱",
        "title": "검찰청 직원이라며 안전계좌 이체 요구",
        "body": "오늘 오전에 서울중앙지검이라고 하면서 명의도용 사건에 연루되었으니 "
                "안전계좌 110-1234-567890 으로 옮기라고 했어요. 010-5555-1234 로 다시 전화 달랍니다.",
        "contacts": [{"value": "010-5555-1234"}],
    },
    {
        "category": "사기",
        "title": "중고나라 선입금 후 잠적",
        "body": "맥북 거래하기로 하고 100-22-3344 계좌로 80만원 보냈는데 "
                "https://scam-shop.kr/p/123 사이트에서 계속 핑계만 대요.",
        "contacts": [],
    },
    {
        "category": "마약",
        "title": "텔레그램으로 필로폰 거래 권유",
        "body": "모르는 번호 010-9999-1111 에서 텔레그램 추가 후 https://t.me/x999 채널 권유. "
                "필로폰 정기 거래 가능하다며 가격 상담.",
        "contacts": [{"value": "010-9999-1111"}],
    },
]


class StageTimer:
    """Context-manager-style accumulator: per-stage cumulative seconds."""

    def __init__(self):
        self.totals: dict[str, float] = {}
        self.calls: dict[str, int] = {}

    def reset(self):
        self.totals.clear()
        self.calls.clear()

    def wrap(self, name, fn):
        def wrapped(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                self.totals[name] = self.totals.get(name, 0) + (time.perf_counter() - t0)
                self.calls[name] = self.calls.get(name, 0) + 1
        return wrapped


def fake_llm_response(*args, **kwargs):
    """Return a syntactically valid LLM JSON without real API call.

    Picks 3 ids 1..50 (matches K=50 candidate fetch) and rates them.
    """
    ids = random.sample(range(1, 51), k=3)
    return llm_mod.LLMResult(True, json.dumps({
        "ranked": [
            {"id": str(i), "similarity": round(random.uniform(0.4, 0.95), 2),
             "scam_likelihood": round(random.uniform(0.3, 0.95), 2),
             "reasons": ["핵심어A", "핵심어B"],
             "matched_methods": ["수법1"], "actions": ["대응1", "대응2"]}
            for i in ids
        ],
        "overall": {"risk_level": "high", "top_ids": [str(i) for i in ids]},
    }))


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


def main():
    random.seed(17)
    iters = int(os.getenv("BENCH_ITERS", "100"))
    rf = RequestFactory()
    view = AssessView.as_view()

    timer = StageTimer()

    out = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "iters": iters,
    }

    # views.py uses `from X import Y` so we must patch the bound name in views_mod,
    # not the source module.
    with patch.object(views_mod, "call_llm", side_effect=fake_llm_response), \
         patch.object(views_mod, "fetch_candidates_mysql",
                      side_effect=timer.wrap("db_search", db_search.fetch_candidates_mysql)), \
         patch.object(views_mod, "check_spam_number",
                      side_effect=timer.wrap("phone_check", phone_check.check_spam_number)), \
         patch.object(views_mod, "compact_case_row",
                      side_effect=timer.wrap("compact_pii", compact_mod.compact_case_row)):

        # Warmup
        for _ in range(5):
            payload = random.choice(PAYLOADS)
            req = rf.post("/assess", data=json.dumps(payload),
                          content_type="application/json")
            view(req)

        timer.reset()
        totals = []
        per_stage_totals: dict[str, list[float]] = {}
        for _ in range(iters):
            payload = random.choice(PAYLOADS)
            req = rf.post("/assess", data=json.dumps(payload),
                          content_type="application/json")
            t0 = time.perf_counter()
            resp = view(req)
            totals.append(time.perf_counter() - t0)
            assert resp.status_code == 200, resp.data
            for stage, secs in timer.totals.items():
                per_stage_totals.setdefault(stage, []).append(secs)
                # reset for the next iteration so stage totals are per-call
            timer.reset()

    out["total_request"] = percentiles(totals)
    out["per_stage_avg_ms"] = {
        s: round(statistics.mean(v) * 1000, 2) for s, v in per_stage_totals.items()
    }
    out["per_stage_share_pct"] = {
        s: round(statistics.mean(v) / statistics.mean(totals) * 100, 1)
        for s, v in per_stage_totals.items()
    }

    print(f"[total ] p50={out['total_request']['p50_ms']}ms  "
          f"p99={out['total_request']['p99_ms']}ms  n={out['total_request']['n']}")
    for s, v in out["per_stage_avg_ms"].items():
        share = out["per_stage_share_pct"][s]
        print(f"  - {s:<12} avg={v:>6}ms  ({share:>5}% of total)")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "m4_pipeline.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n→ wrote {out_path}")


if __name__ == "__main__":
    main()
