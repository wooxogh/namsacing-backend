# 남사칭-backend 성능 측정 결과
_generated: 2026-05-02T17:35:59_

전체 측정은 격리된 docker MySQL 8.0 (port 3307) + 합성 데이터에서 실행되었다.
재현: `cd bench && docker compose up -d && python seed.py --reset && for s in measure_*.py; do python $s; done && python report.py`

**중요**: 이 측정은 production 코드를 변경하지 않은 상태에서 수행되었다. 발견된 결함은 별도 PR로 처리한다 (`bench/FINDINGS.md` 참고).

---
## M1: `fetch_candidates_mysql` latency
- DB rows: **10000**, iters/K: **100**

| path                                                   | K   | p50 (ms)   | p99 (ms)   | rows   | note   |
|--------------------------------------------------------|-----|------------|------------|--------|--------|
| A1) production SQL `MATCH(title, body)`                | 20  | 0.15       | 0.29       | 0      | ❌ ERR  |
| A1) production SQL `MATCH(title, body)`                | 50  | 0.13       | 0.27       | 0      | ❌ ERR  |
| A1) production SQL `MATCH(title, body)`                | 100 | 0.14       | 0.29       | 0      | ❌ ERR  |
|                                                        |     |            |            |        |        |
| A2) corrected `MATCH(title, content)` + ngram FULLTEXT | 20  | 4.62       | 11.57      | 20     |        |
| A2) corrected `MATCH(title, content)` + ngram FULLTEXT | 50  | 5.06       | 12.08      | 50     |        |
| A2) corrected `MATCH(title, content)` + ngram FULLTEXT | 100 | 5.42       | 7.22       | 100    |        |
|                                                        |     |            |            |        |        |
| B)  fallback `ORDER BY created_at DESC`                | 20  | 0.48       | 1.09       | 20     |        |
| B)  fallback `ORDER BY created_at DESC`                | 50  | 0.75       | 1.67       | 50     |        |
| B)  fallback `ORDER BY created_at DESC`                | 100 | 1.07       | 1.69       | 100    |        |
|                                                        |     |            |            |        |        |
| PROD) actual `fetch_candidates_mysql()` via Django     | 20  | 0.71       | 1.06       | 20     |        |
| PROD) actual `fetch_candidates_mysql()` via Django     | 50  | 0.91       | 1.44       | 50     |        |
| PROD) actual `fetch_candidates_mysql()` via Django     | 100 | 1.29       | 2.64       | 100    |        |

---

## M2: `check_spam_number` latency
- iters: **100**, cached rows: **1000**

| scenario                                           |   p50 (ms) |   p90 (ms) |   p99 (ms) |   mean (ms) |
|----------------------------------------------------|------------|------------|------------|-------------|
| cache hit (DB SELECT only)                         |      0.434 |      0.685 |      0.798 |       0.515 |
| cache miss + no APICK key (early return)           |      0.003 |      0.004 |      0.004 |       0.003 |
| cache miss + mocked APICK (DB upsert + JSON parse) |      2.06  |      2.429 |      2.905 |       2.122 |

---

## M3: PII 마스킹 적용률 + `compact_case_row` 키 불일치

### `mask_all` coverage
| pattern   |   expected |   leaked |   leak rate |
|-----------|------------|----------|-------------|
| phone     |        685 |        0 |           0 |
| account   |        498 |        0 |           0 |
| url       |        500 |        0 |           0 |

- throughput: **9,717,775 chars/sec**
- p50 per doc: **10.85 μs**

### `compact_case_row` body/content key bug
- input keys returned by `db_search`: `['category', 'content', 'created_at', 'id', 'title']`
- compact output: `{"id": "42", "when": "2025-09-01T12:00:00", "title": "검찰청 사칭 사례", "summary": "", "like": 0}`
- summary 필드가 비어있음: **True**
- like 필드가 0으로 default: **True**

> compact_case_row reads row.get('body') and row.get('like_count') but db_search.fetch_candidates_mysql returns 'content' (not 'body') and never selects like_count. Result: LLM rerank receives an empty 'summary' field for every candidate.

---

## M4: end-to-end `AssessView` (LLM mocked)
- iters: **100**

### Total request latency (LLM mocked, returns synthetic JSON)
|   p50 (ms) |   p90 (ms) |   p99 (ms) |   max (ms) |   mean (ms) |
|------------|------------|------------|------------|-------------|
|       1.96 |       2.18 |       2.87 |       3.56 |         1.9 |

### Per-stage breakdown
| stage       |   avg (ms) | share of total   |
|-------------|------------|------------------|
| db_search   |       1.05 | 55.1%            |
| compact_pii |       0.14 | 7.3%             |
| phone_check |       0.47 | 24.8%            |

> Note: real production includes the GPT-4o call (~1–3s synchronous), which dominates user-facing latency. The numbers above isolate the non-LLM pipeline cost.

---

## M5: scaling — `fetch_candidates_mysql` latency vs DB size
- K: **20**, iters per size: **50**

|   N (rows) |   p50 (ms) |   p99 (ms) |   mean (ms) |
|------------|------------|------------|-------------|
|        100 |       0.69 |       2.86 |        0.75 |
|       1000 |       0.67 |       1.21 |        0.69 |
|      10000 |       0.7  |       1.33 |        0.7  |

> Production code currently always takes the fallback path (see M1). These numbers therefore measure `ORDER BY created_at DESC LIMIT K`.

---
