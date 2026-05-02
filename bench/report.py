"""Aggregate all measurement JSONs into a single human-readable report.

Reads bench/results/m{1..5}_*.json and writes bench/results/RESULTS.md.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from tabulate import tabulate

from config import RESULTS_DIR


def load(name: str, subdir: str = "") -> dict | None:
    path = os.path.join(RESULTS_DIR, subdir, name) if subdir else os.path.join(RESULTS_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def diff_table(name: str, before: dict | None, after: dict | None,
               extract: callable) -> list[str]:
    """Generate a 'before vs after fix' comparison row.

    extract(d: dict) → list[(label, p50_ms)] for that measurement.
    Returns markdown table lines.
    """
    if not before and not after:
        return []
    rows_before = dict(extract(before)) if before else {}
    rows_after  = dict(extract(after))  if after  else {}
    keys = list({*rows_before, *rows_after})
    rows = []
    for k in keys:
        b = rows_before.get(k, "—")
        a = rows_after.get(k, "—")
        delta = ""
        if isinstance(b, (int, float)) and isinstance(a, (int, float)) and b > 0:
            ratio = a / b
            delta = f"×{ratio:.2f}" if ratio >= 1.0 else f"−{(1-ratio)*100:.0f}%"
        rows.append([k, b, a, delta])
    return [
        f"### {name} — before vs after fix",
        md_table(rows, ["scenario", "before (ms)", "after (ms)", "Δ"]),
        "",
    ]


def md_table(rows, headers):
    return tabulate(rows, headers=headers, tablefmt="github")


def section_m1(d: dict) -> list[str]:
    if not d:
        return ["## M1: DB 후보 검색 latency\n_no data_"]

    out = ["## M1: `fetch_candidates_mysql` latency",
           f"- DB rows: **{d['db_rows']}**, iters/K: **{d['iters_per_k']}**",
           ""]

    rows = []
    for path_key, label in [
        ("path_A1_prod_fulltext",       "A1) production SQL `MATCH(title, body)`"),
        ("path_A2_corrected_fulltext",  "A2) corrected `MATCH(title, content)` + ngram FULLTEXT"),
        ("path_B_fallback",             "B)  fallback `ORDER BY created_at DESC`"),
        ("path_PROD_fn",                "PROD) actual `fetch_candidates_mysql()` via Django"),
    ]:
        path = d.get(path_key, {})
        by_k = path.get("by_k", {})
        for k, m in sorted(by_k.items(), key=lambda kv: int(kv[0])):
            err = "❌ ERR" if m.get("first_error") else ""
            rows.append([
                label, k, m.get("p50_ms", "—"), m.get("p99_ms", "—"),
                m.get("rows_returned_avg", "—"), err,
            ])
        if not by_k:
            continue
        rows.append(["", "", "", "", "", ""])  # spacer

    out.append(md_table(rows[:-1], ["path", "K", "p50 (ms)", "p99 (ms)", "rows", "note"]))

    # Errors detail
    a1 = d.get("path_A1_prod_fulltext", {}).get("by_k", {}).get(20, {})
    if a1.get("first_error"):
        out += ["", "### A1 first error sample", "```", a1["first_error"], "```"]

    return out


def section_m2(d: dict) -> list[str]:
    if not d:
        return ["## M2: phone_check latency\n_no data_"]
    out = ["## M2: `check_spam_number` latency",
           f"- iters: **{d['iters']}**, cached rows: **{d['phone_check_rows']}**",
           ""]
    rows = []
    for k, label in [
        ("cache_hit",              "cache hit (DB SELECT only)"),
        ("cache_miss_no_key",      "cache miss + no APICK key (early return)"),
        ("cache_miss_mocked_api",  "cache miss + mocked APICK (DB upsert + JSON parse)"),
    ]:
        m = d[k]
        rows.append([label, m["p50_ms"], m["p90_ms"], m["p99_ms"], m["mean_ms"]])
    out.append(md_table(rows, ["scenario", "p50 (ms)", "p90 (ms)", "p99 (ms)", "mean (ms)"]))
    return out


def section_m3(d: dict) -> list[str]:
    if not d:
        return ["## M3: PII masking\n_no data_"]
    out = ["## M3: PII 마스킹 적용률 + `compact_case_row` 키 불일치"]
    m = d["mask_all"]
    out += [
        "",
        "### `mask_all` coverage",
        md_table([
            ["phone",   m["expected_pii"]["phone"],   m["leaked_pii_after_mask"]["phone"],   m["leak_rate"]["phone"]],
            ["account", m["expected_pii"]["account"], m["leaked_pii_after_mask"]["account"], m["leak_rate"]["account"]],
            ["url",     m["expected_pii"]["url"],     m["leaked_pii_after_mask"]["url"],     m["leak_rate"]["url"]],
        ], ["pattern", "expected", "leaked", "leak rate"]),
        "",
        f"- throughput: **{m['throughput_chars_per_sec']:,} chars/sec**",
        f"- p50 per doc: **{m['p50_us_per_doc']} μs**",
        "",
        "### `compact_case_row` body/content key bug",
    ]
    bug = d["compact_case_row_bug"]
    out += [
        f"- input keys returned by `db_search`: `{bug['input_keys']}`",
        f"- compact output: `{json.dumps(bug['compact_output'], ensure_ascii=False)}`",
        f"- summary 필드가 비어있음: **{bug['summary_is_empty']}**",
        f"- like 필드가 0으로 default: **{bug['like_defaulted_to_zero']}**",
        "",
        "> " + bug["evidence"],
    ]
    return out


def section_m4(d: dict) -> list[str]:
    if not d:
        return ["## M4: pipeline\n_no data_"]
    t = d["total_request"]
    out = [
        "## M4: end-to-end `AssessView` (LLM mocked)",
        f"- iters: **{d['iters']}**",
        "",
        "### Total request latency (LLM mocked, returns synthetic JSON)",
        md_table([[t["p50_ms"], t["p90_ms"], t["p99_ms"], t["max_ms"], t["mean_ms"]]],
                 ["p50 (ms)", "p90 (ms)", "p99 (ms)", "max (ms)", "mean (ms)"]),
        "",
        "### Per-stage breakdown",
    ]
    rows = []
    for stage, ms in d["per_stage_avg_ms"].items():
        share = d["per_stage_share_pct"][stage]
        rows.append([stage, ms, f"{share}%"])
    out.append(md_table(rows, ["stage", "avg (ms)", "share of total"]))
    out += [
        "",
        "> Note: real production includes the GPT-4o call (~1–3s synchronous), "
        "which dominates user-facing latency. The numbers above isolate the "
        "non-LLM pipeline cost.",
    ]
    return out


def section_m6(d: dict) -> list[str]:
    if not d:
        return ["## M6: hit rate vs effective latency\n_no data_"]
    out = [
        "## M6: cache hit rate vs effective latency / apick 호출 절감",
        f"- 시나리오: {d['n_warm']}개 알려진 번호 사전 적재, 각 요청은 hit_rate 확률로 알려진 번호 선택",
        f"- mocked apick latency: **{d['apick_mock_latency_ms']}ms** (실제 외부 API 추정)",
        f"- 요청 수 per level: **{d['n_requests_per_level']}**",
        "",
    ]
    rows = []
    for hr_str, m in sorted(d["by_hit_rate"].items(), key=lambda kv: float(kv[0])):
        hr = float(hr_str)
        rows.append([
            f"{hr:.0%}",
            m["p50_ms"], m["p90_ms"], m["p99_ms"], m["mean_ms"],
            f"{m['apick_call_pct']}%",
        ])
    out.append(md_table(rows, [
        "hit rate", "p50 (ms)", "p90 (ms)", "p99 (ms)", "mean (ms)", "apick 호출 비중",
    ]))
    out += [
        "",
        "**해석:**",
        "- hit rate ≥ 50% 부터 p50 가 단일 ms 영역으로 진입 (≤ 3.5ms)",
        "- p99 는 hit rate 95% 까지도 ~apick latency 와 비슷 — 한 건의 miss 가 tail 지배",
        "- mean latency 는 (1−hit) × apick + hit × cache 로 거의 선형 감소",
        "- apick 호출량은 (1−hit_rate) 와 정확히 일치 → 일 1k 요청에서 hit 80% 면 200회/day, hit 99% 면 10회/day",
        "",
        "**도메인 가정:** 사기 번호는 동일 번호로 다수 피해자 발생 → 시간 누적시 hit rate 우상향. ",
        "초기에는 hit rate 낮아도 사용량 증가에 따라 자연스럽게 hit rate ≥ 80% 영역으로 수렴.",
    ]
    return out


def section_m5(d: dict) -> list[str]:
    if not d:
        return ["## M5: scaling\n_no data_"]
    out = [
        "## M5: scaling — `fetch_candidates_mysql` latency vs DB size",
        f"- K: **{d['k']}**, iters per size: **{d['iters_per_size']}**",
        "",
    ]
    rows = []
    for size, m in sorted(d["by_size"].items(), key=lambda kv: int(kv[0])):
        rows.append([size, m["p50_ms"], m["p99_ms"], m["mean_ms"]])
    out.append(md_table(rows, ["N (rows)", "p50 (ms)", "p99 (ms)", "mean (ms)"]))
    out += [
        "",
        "> Production code currently always takes the fallback path "
        "(see M1). These numbers therefore measure `ORDER BY created_at DESC LIMIT K`.",
    ]
    return out


def main():
    parts = [
        "# 남사칭-backend 성능 측정 결과",
        f"_generated: {datetime.now().isoformat(timespec='seconds')}_",
        "",
        "전체 측정은 격리된 docker MySQL 8.0 (port 3307) + 합성 데이터에서 실행되었다.",
        "재현: `cd bench && docker compose up -d && python seed.py --reset && "
        "for s in measure_*.py; do python $s; done && python report.py`",
        "",
        "**before/after**: production 코드 fix 전후를 모두 보존한다. ",
        "- `results/snapshot_before_fix/` — F1+F2+F3 fix 전 측정값 (silent bug 가 살아 있던 상태)",
        "- `results/m{1..5}_*.json` — fix 후 측정값 (FULLTEXT 가 실제로 동작하는 상태)",
        "",
        "---",
    ]

    # before vs after 요약 (가장 중요한 변화만)
    before_m1 = load("m1_db_search.json", "snapshot_before_fix")
    after_m1  = load("m1_db_search.json")

    def extract_m1(d):
        return [
            ("PROD fetch_candidates_mysql K=20", d["path_PROD_fn"]["by_k"]["20"]["p50_ms"]),
            ("PROD fetch_candidates_mysql K=50", d["path_PROD_fn"]["by_k"]["50"]["p50_ms"]),
            ("PROD fetch_candidates_mysql K=100", d["path_PROD_fn"]["by_k"]["100"]["p50_ms"]),
        ]

    parts += ["## 핵심 변화 — fix 전후 비교", ""]
    parts += diff_table("M1 fetch_candidates_mysql", before_m1, after_m1, extract_m1)

    before_m3 = load("m3_pii.json", "snapshot_before_fix")
    after_m3  = load("m3_pii.json")
    if before_m3 and after_m3:
        parts += [
            "### M3 compact_case_row summary 채워짐 여부",
            md_table([
                ["summary 가 빈 문자열 (bug)",
                 before_m3["compact_case_row_bug"]["summary_is_empty"],
                 after_m3["compact_case_row_bug"]["summary_is_empty"]],
            ], ["", "before fix", "after fix"]),
            "",
        ]

    before_m4 = load("m4_pipeline.json", "snapshot_before_fix")
    after_m4  = load("m4_pipeline.json")
    if before_m4 and after_m4:
        parts += [
            "### M4 end-to-end (LLM mock) — 파이프라인 stage 비중",
            md_table([
                ["total p50 (ms)",
                 before_m4["total_request"]["p50_ms"],
                 after_m4["total_request"]["p50_ms"]],
                ["db_search avg (ms)",
                 before_m4["per_stage_avg_ms"].get("db_search", "—"),
                 after_m4["per_stage_avg_ms"].get("db_search", "—")],
                ["compact_pii avg (ms)",
                 before_m4["per_stage_avg_ms"].get("compact_pii", "—"),
                 after_m4["per_stage_avg_ms"].get("compact_pii", "—")],
                ["phone_check avg (ms)",
                 before_m4["per_stage_avg_ms"].get("phone_check", "—"),
                 after_m4["per_stage_avg_ms"].get("phone_check", "—")],
            ], ["metric", "before fix", "after fix"]),
            "",
            "> after fix 의 db_search/compact 가 더 무거워진 것은 'FULLTEXT 가 실제로 동작 + ",
            "마스킹이 실제 본문에 적용' 되기 때문. 이전에는 둘 다 사실상 no-op 였음.",
            "",
        ]

    parts += ["---", "## 상세 (after fix)", ""]
    for fn, sect in [
        ("m1_db_search.json",   section_m1),
        ("m2_phone_check.json", section_m2),
        ("m3_pii.json",         section_m3),
        ("m4_pipeline.json",    section_m4),
        ("m5_scaling.json",     section_m5),
        ("m6_hit_rate.json",    section_m6),
    ]:
        d = load(fn)
        parts += sect(d) + ["", "---", ""]

    out_path = os.path.join(RESULTS_DIR, "RESULTS.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts).rstrip() + "\n")
    print(f"→ wrote {out_path}")


if __name__ == "__main__":
    main()
