# 남사칭-backend 성능 측정 결과
_generated: 2026-05-02T17:50:15_

전체 측정은 격리된 docker MySQL 8.0 (port 3307) + 합성 데이터에서 실행되었다.
재현: `cd bench && docker compose up -d && python seed.py --reset && for s in measure_*.py; do python $s; done && python report.py`

**before/after**: production 코드 fix 전후를 모두 보존한다. 
- `results/snapshot_before_fix/` — F1+F2+F3 fix 전 측정값 (silent bug 가 살아 있던 상태)
- `results/m{1..5}_*.json` — fix 후 측정값 (FULLTEXT 가 실제로 동작하는 상태)

---
## 핵심 변화 — fix 전후 비교

### M1 fetch_candidates_mysql — before vs after fix
| scenario                          |   before (ms) |   after (ms) | Δ     |
|-----------------------------------|---------------|--------------|-------|
| PROD fetch_candidates_mysql K=20  |          0.71 |         4.41 | ×6.21 |
| PROD fetch_candidates_mysql K=50  |          0.91 |         4.38 | ×4.81 |
| PROD fetch_candidates_mysql K=100 |          1.29 |         4.94 | ×3.83 |

### M3 compact_case_row summary 채워짐 여부
|                       | before fix   | after fix   |
|-----------------------|--------------|-------------|
| summary 가 빈 문자열 (bug) | True         | False       |

### M4 end-to-end (LLM mock) — 파이프라인 stage 비중
| metric               |   before fix |   after fix |
|----------------------|--------------|-------------|
| total p50 (ms)       |         1.96 |       34.64 |
| db_search avg (ms)   |         1.05 |       32.9  |
| compact_pii avg (ms) |         0.14 |        1.79 |
| phone_check avg (ms) |         0.47 |        1.14 |

> after fix 의 db_search/compact 가 더 무거워진 것은 'FULLTEXT 가 실제로 동작 + 
마스킹이 실제 본문에 적용' 되기 때문. 이전에는 둘 다 사실상 no-op 였음.

---
## 상세 (after fix)

## M1: `fetch_candidates_mysql` latency
- DB rows: **10000**, iters/K: **100**

| path                                                   | K   | p50 (ms)   | p99 (ms)   | rows   | note   |
|--------------------------------------------------------|-----|------------|------------|--------|--------|
| A1) production SQL `MATCH(title, body)`                | 20  | 0.17       | 0.22       | 0      | ❌ ERR  |
| A1) production SQL `MATCH(title, body)`                | 50  | 0.15       | 0.23       | 0      | ❌ ERR  |
| A1) production SQL `MATCH(title, body)`                | 100 | 0.14       | 0.25       | 0      | ❌ ERR  |
|                                                        |     |            |            |        |        |
| A2) corrected `MATCH(title, content)` + ngram FULLTEXT | 20  | 4.17       | 7.07       | 20     |        |
| A2) corrected `MATCH(title, content)` + ngram FULLTEXT | 50  | 4.69       | 31.78      | 50     |        |
| A2) corrected `MATCH(title, content)` + ngram FULLTEXT | 100 | 4.96       | 10.68      | 100    |        |
|                                                        |     |            |            |        |        |
| B)  fallback `ORDER BY created_at DESC`                | 20  | 0.48       | 1.11       | 20     |        |
| B)  fallback `ORDER BY created_at DESC`                | 50  | 0.77       | 2.62       | 50     |        |
| B)  fallback `ORDER BY created_at DESC`                | 100 | 1.11       | 2.78       | 100    |        |
|                                                        |     |            |            |        |        |
| PROD) actual `fetch_candidates_mysql()` via Django     | 20  | 4.41       | 6.21       | 20     |        |
| PROD) actual `fetch_candidates_mysql()` via Django     | 50  | 4.38       | 6.57       | 50     |        |
| PROD) actual `fetch_candidates_mysql()` via Django     | 100 | 4.94       | 8.22       | 100    |        |

---

## M2: `check_spam_number` latency
- iters: **100**, cached rows: **1000**

| scenario                                           |   p50 (ms) |   p90 (ms) |   p99 (ms) |   mean (ms) |
|----------------------------------------------------|------------|------------|------------|-------------|
| cache hit (DB SELECT only)                         |      0.438 |      0.486 |      0.545 |       0.444 |
| cache miss + no APICK key (early return)           |      0.003 |      0.004 |      0.005 |       0.003 |
| cache miss + mocked APICK (DB upsert + JSON parse) |      2.03  |      2.801 |      3.739 |       2.205 |

---

## M3: PII 마스킹 적용률 + `compact_case_row` 키 불일치

### `mask_all` coverage
| pattern   |   expected |   leaked |   leak rate |
|-----------|------------|----------|-------------|
| phone     |        685 |        0 |           0 |
| account   |        498 |        0 |           0 |
| url       |        500 |        0 |           0 |

- throughput: **3,388,321 chars/sec**
- p50 per doc: **12.83 μs**

### `compact_case_row` body/content key bug
- input keys returned by `db_search`: `['category', 'content', 'created_at', 'id', 'title']`
- compact output: `{"id": "42", "when": "2025-09-01T12:00:00", "title": "검찰청 사칭 사례", "summary": "안녕하세요 서울중앙지검입니다. ***-****-5678 로 연락 주세요. scam.kr/…", "like": 0}`
- summary 필드가 비어있음: **False**
- like 필드가 0으로 default: **True**

> compact_case_row reads row.get('body') and row.get('like_count') but db_search.fetch_candidates_mysql returns 'content' (not 'body') and never selects like_count. Result: LLM rerank receives an empty 'summary' field for every candidate.

---

## M4: end-to-end `AssessView` (LLM mocked)
- iters: **100**

### Total request latency (LLM mocked, returns synthetic JSON)
|   p50 (ms) |   p90 (ms) |   p99 (ms) |   max (ms) |   mean (ms) |
|------------|------------|------------|------------|-------------|
|      34.64 |      45.37 |      49.23 |      60.11 |       36.11 |

### Per-stage breakdown
| stage       |   avg (ms) | share of total   |
|-------------|------------|------------------|
| db_search   |      32.9  | 91.1%            |
| compact_pii |       1.79 | 5.0%             |
| phone_check |       1.14 | 3.2%             |

> Note: real production includes the GPT-4o call (~1–3s synchronous), which dominates user-facing latency. The numbers above isolate the non-LLM pipeline cost.

---

## M5: scaling — `fetch_candidates_mysql` latency vs DB size
- K: **20**, iters per size: **50**

|   N (rows) |   p50 (ms) |   p99 (ms) |   mean (ms) |
|------------|------------|------------|-------------|
|        100 |       0.53 |       0.65 |        0.54 |
|       1000 |       0.85 |       1    |        0.85 |
|      10000 |       4.49 |       7.2  |        4.72 |

> Production code currently always takes the fallback path (see M1). These numbers therefore measure `ORDER BY created_at DESC LIMIT K`.

---
