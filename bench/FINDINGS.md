# 남사칭-backend 측정 기반 결함 보고

bench/ 측정을 진행하면서 발견된 production 결함과 그 증거. **이 문서는 production 코드를 일절 수정하지 않은 상태에서 작성되었다.** 수정은 별도 PR에서 진행한다.

각 항목은 (1) 무엇이 잘못되었는가, (2) 어디에 있는가, (3) 어떻게 검증했는가, (4) 측정 결과 의미, (5) 수정 방향을 포함한다.

---

## F1. FULLTEXT 경로가 production 에서 한 번도 실행되지 않음

### 무엇이
`similarity/services/db_search.py:31` 의 SQL 이 `MATCH(title, body) AGAINST (...)` 를 호출. 그러나:
- `wasscam/models.py:21` 모델 컬럼명은 `content`
- `wasscam/migrations/0001_initial.py` 도 `content`
- 같은 SQL의 SELECT 절은 정상적으로 `content` 사용

→ MySQL이 매 호출마다 `Unknown column 'body' in 'order clause'` 류의 에러를 던지고, `try/except` 가 잡아서 fallback 경로 (`ORDER BY created_at DESC`) 로 빠진다.

### 증거 (M1)
- bench MySQL 에 schema 동일하게 적재 후 production SQL 실행
- 100% 케이스에서 에러:

```
pymysql.err.OperationalError: (1054, "Unknown column 'body' in 'order clause'")
```

- corrected SQL (`MATCH(title, content)`) 은 정상 동작 (p50 4.6ms @ N=10k, K=20)
- fallback 경로만 정상 동작 (p50 0.5ms @ N=10k, K=20)
- 따라서 production 에서 측정되는 `db_search_latency_seconds` 히스토그램은 **fallback 경로의 latency** 이고, `db_search_fallback_total` 카운터는 **모든 요청에서 증가**한다

### 의미
- CV 문구 "FULLTEXT INDEX 평균 ~90ms" 는 현재 코드 기준 사실이 아니다 (FULLTEXT 가 한 번도 실행 안 됨).
- 실제 production 사용자에게 보이는 결과는 단순한 "최신순 20건". 즉, 검색어와 무관하게 카테고리 내 최근 글만 LLM 에 넘김.
- 정확도가 떨어졌어도 사용자가 알아차리기 어려운 silent degradation.

### 수정
1. SQL 의 `body` → `content` 로 교정
2. `wasscam/migrations/0002_*.py` 추가하여 `ALTER TABLE wasscam_post ADD FULLTEXT(title, content) WITH PARSER ngram` (한국어 토큰화)
3. MySQL 설정에서 `innodb_ft_min_token_size=1`, `ngram_token_size=2` 적용 (bench docker-compose 참고)

---

## F2. FULLTEXT INDEX 자체가 마이그레이션에 없음

### 무엇이
F1 을 수정해도 `wasscam_post` 테이블에 FULLTEXT INDEX 가 정의된 적 없다. Django `models.Index` 는 일반 인덱스만 지원하므로 raw migration 으로 별도 추가 필요.

### 증거
`wasscam/migrations/0001_initial.py` 전문 확인 → `FULLTEXT` 키워드 등장 0회.

### 수정
F1 의 마이그레이션 추가 단계와 동일.

---

## F3. `compact_case_row` 가 항상 빈 summary 를 반환

### 무엇이
`similarity/utils/compact.py:13`
```python
body = mask_all(row.get("body") or "")
```
그러나 `db_search.fetch_candidates_mysql` 가 반환하는 dict 의 키는 `{id, category, title, content, created_at}`. `body` 키 없음 → 항상 빈 문자열.

같은 함수 line 27 의 `row.get("like_count") or 0` 도 동일 (db_search 가 like_count 를 select 하지 않음) → 항상 0.

### 증거 (M3)
실제 production 이 반환할 형태의 row 를 만들어 `compact_case_row` 호출:
```json
{"id":"42","when":"2025-09-01T12:00:00","title":"검찰청 사칭 사례","summary":"","like":0}
```
즉 LLM 재랭킹 단계에서 후보 사례 50건 (또는 20건 trim 후) 의 summary 가 모두 `""`. LLM 은 사실상 **제목 60자만 보고 재랭킹**한다.

### 의미
- "K=50 → PII 마스킹 → K=20 → LLM 재랭킹" 파이프라인 구조는 맞지만, "PII 마스킹된 본문이 LLM 에 전달된다"는 함의는 거짓.
- LLM 정확도가 사실상 제목 매칭에 가까운 수준으로 떨어져 있을 가능성.

### 수정 (택 1)
- `compact_case_row(row)` 가 `row.get("content")` 도 함께 fallback 으로 읽도록
- `db_search.fetch_candidates_mysql` SELECT 절을 `SELECT id, category, title, content AS body, ...` 로 alias 부여
- 더 좋은 방법: 두 곳을 동일한 단어 (`content` 권장) 로 통일

---

## F4. `prometheus_client` import error 로 부팅 자체 불가

### 무엇이
`db_search.py`, `db_read.py`, `phone_check.py`, `llm.py` 모두 module top-level 에서 `from prometheus_client import Histogram, Counter` 호출. 그러나 `requirements.txt` 에 `prometheus_client` 없음.

```
$ grep prometheus sinchonApp/requirements.txt
(no output)
```

따라서 `pip install -r requirements.txt` 후 `python manage.py runserver` 실행 시 `ModuleNotFoundError: No module named 'prometheus_client'`.

### 증거
- requirements.txt 전수 검사: 0 hits
- `git log --oneline` 에서 `60b5969 [TEST] 성능 측정 프로메테우스 도입` (2026-01-12) 만 있음 — 의존성 추가 없이 import 만 추가됨
- `urls.py` 어디에도 `/metrics` 엔드포인트 등록 없음 → 메트릭이 수집되어도 외부에서 scrape 불가

### 의미
- 4개월간 메인 브랜치가 사실상 부팅 불가 상태로 방치됨 (해커톤 종료 후라 아무도 실행 안 함)
- 수집되는 메트릭이 어디로도 노출되지 않는 dead code

### 수정
1. `requirements.txt` 에 `prometheus-client==0.21.1` 추가
2. `urls.py` 에 `from prometheus_client import make_wsgi_app` 기반 `/metrics` 엔드포인트 등록 (또는 `django-prometheus` 채택)
3. (선택) `MIDDLEWARE` 에 `django-prometheus` 추가하여 request-level 메트릭 자동 수집

---

## F5. JWT TTL 60분 + cache 미사용 = 비활성화 반영 지연

### 무엇이 (확인된 design trade-off, 결함 아님)
`settings.py:86` `ACCESS_TOKEN_LIFETIME=60min`, `ROTATE_REFRESH_TOKENS=False`, `BLACKLIST_AFTER_ROTATION=False`.

이 부분은 CV 의 "JWT Stateless 인증으로 DB 병목 제거 (trade-off: 비활성화 반영이 토큰 TTL 1시간까지 지연)" 표현과 정확히 일치한다. **수정 대상 아니라 검증된 의도.**

### 증거
- `simplejwt` 의 BLACKLIST app 미설치 (INSTALLED_APPS 확인) → 토큰 무효화 기전 없음
- TTL = 1시간 → 지연 한도 1시간 ✓

### 의미
이 항목은 CV 그대로 두면 됨. 면접에서 물어보면 위 trade-off 그대로 설명하면 자연스럽다.

---

## 수정 우선순위 권고

| 우선순위 | 항목 | 이유 |
|---------|------|------|
| P0 | F4 (prometheus dep + /metrics) | 부팅 자체 불가. 다른 모든 측정의 전제. |
| P0 | F1 + F2 (SQL + INDEX) | CV 수치의 핵심 근거. 함께 묶어 1 PR. |
| P1 | F3 (compact key) | LLM 정확도에 직접 영향. PR 작은 편. |
| —  | F5 (JWT) | 의도된 trade-off. 변경 불필요. |

---

## 수정 후 재측정 절차

1. fix branch 에서 F1·F2·F3·F4 적용
2. `bench/seed.py --reset` (스키마는 fix 후의 마이그레이션 기반으로 재생성)
3. `for s in measure_*.py; do python $s; done`
4. `python report.py` → before/after diff
5. 결과 차이를 CV 의 수치 근거로 사용 (구체 숫자는 측정값 그대로)
