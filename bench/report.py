"""Aggregate all measurement JSONs into a single human-readable report.

Reads bench/results/m{1..5}_*.json and writes bench/results/RESULTS.md.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from tabulate import tabulate

from config import RESULTS_DIR


def load(name: str) -> dict | None:
    path = os.path.join(RESULTS_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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
        "**중요**: 이 측정은 production 코드를 변경하지 않은 상태에서 수행되었다. "
        "발견된 결함은 별도 PR로 처리한다 (`bench/FINDINGS.md` 참고).",
        "",
        "---",
    ]
    for fn, sect in [
        ("m1_db_search.json",   section_m1),
        ("m2_phone_check.json", section_m2),
        ("m3_pii.json",         section_m3),
        ("m4_pipeline.json",    section_m4),
        ("m5_scaling.json",     section_m5),
    ]:
        d = load(fn)
        parts += sect(d) + ["", "---", ""]

    out_path = os.path.join(RESULTS_DIR, "RESULTS.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts).rstrip() + "\n")
    print(f"→ wrote {out_path}")


if __name__ == "__main__":
    main()
