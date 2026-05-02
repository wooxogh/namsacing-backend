"""M3: PII masking coverage of similarity.utils.pii.mask_all.

Generates a synthetic corpus with known PII counts, runs the production
mask_all function, and measures:
  - leakage rate (PII patterns surviving after masking)
  - throughput (chars/sec)
  - the bug in compact_case_row: it reads row['body'] but db_search returns
    row['content'] → empty summary regardless of input

Output: bench/results/m3_pii.json
"""
from __future__ import annotations

import json
import os
import random
import re
import statistics
import time
from datetime import datetime

import django_setup  # noqa: F401

from similarity.utils.pii import mask_all, PHONE_RE, ACCOUNT_RE
from similarity.utils.compact import compact_case_row
from config import RESULTS_DIR


URL_RE = re.compile(r"https?://[^\s)]+")


def synth_doc(n_phone: int, n_account: int, n_url: int) -> tuple[str, dict[str, int]]:
    """Return (text, expected_counts)."""
    parts = ["고객님 안내드립니다."]
    for _ in range(n_phone):
        parts.append(f"문의 010-{random.randint(1000,9999)}-{random.randint(0,9999):04d} 로 주세요.")
    for _ in range(n_account):
        parts.append(f"입금 계좌 {random.randint(100,999)}-{random.randint(10,999999)}-{random.randint(10,999999)}.")
    for _ in range(n_url):
        parts.append(f"링크 https://scam-{random.randint(1,99)}.kr/promo/{random.randint(100,999)} 클릭.")
    parts.append("주의 부탁드립니다.")
    random.shuffle(parts)
    return " ".join(parts), {"phone": n_phone, "account": n_account, "url": n_url}


def count_pii(text: str) -> dict[str, int]:
    return {
        "phone": len(PHONE_RE.findall(text)),
        "account": len(ACCOUNT_RE.findall(text)),
        "url": len(URL_RE.findall(text)),
    }


def main():
    random.seed(13)
    n_docs = int(os.getenv("BENCH_DOCS", "500"))
    out = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "n_docs": n_docs,
    }

    # 1) Coverage: do PII patterns survive masking?
    leaks = {"phone": 0, "account": 0, "url": 0}
    expected = {"phone": 0, "account": 0, "url": 0}
    masked_chars = 0
    raw_chars = 0
    durations = []
    for _ in range(n_docs):
        text, exp = synth_doc(
            n_phone=random.randint(0, 3),
            n_account=random.randint(0, 2),
            n_url=random.randint(0, 2),
        )
        raw_chars += len(text)
        for k in expected:
            expected[k] += exp[k]
        t0 = time.perf_counter()
        masked = mask_all(text)
        durations.append(time.perf_counter() - t0)
        masked_chars += len(masked)
        post = count_pii(masked)
        for k in leaks:
            leaks[k] += post[k]

    out["mask_all"] = {
        "expected_pii": expected,
        "leaked_pii_after_mask": leaks,
        "leak_rate": {
            k: round(leaks[k] / expected[k], 4) if expected[k] else None
            for k in expected
        },
        "throughput_chars_per_sec": round(raw_chars / sum(durations)) if durations else 0,
        "p50_us_per_doc": round(statistics.median(durations) * 1_000_000, 2),
    }

    # 2) compact_case_row bug: production db_search returns row keys
    #    {id, category, title, content, created_at} — no 'body', no 'like_count'.
    #    compact_case_row reads row['body'] and row['like_count'].
    sample_row = {
        "id": 42, "category": "보이스피싱", "title": "검찰청 사칭 사례",
        "content": "안녕하세요 서울중앙지검입니다. 010-1234-5678 로 연락 주세요. https://scam.kr/auth.",
        "created_at": "2025-09-01T12:00:00",
    }
    compacted = compact_case_row(sample_row)
    out["compact_case_row_bug"] = {
        "input_keys": sorted(sample_row.keys()),
        "compact_output": compacted,
        "summary_is_empty": compacted["summary"] == "",
        "like_defaulted_to_zero": compacted["like"] == 0,
        "evidence": (
            "compact_case_row reads row.get('body') and row.get('like_count') "
            "but db_search.fetch_candidates_mysql returns 'content' (not 'body') "
            "and never selects like_count. Result: LLM rerank receives an "
            "empty 'summary' field for every candidate."
        ),
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "m3_pii.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:1500])
    print(f"\n→ wrote {out_path}")


if __name__ == "__main__":
    main()
