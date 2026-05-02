# bench/ — 성능 측정 및 결정 검증

CV에 기재된 결정·수치(MySQL FULLTEXT 후보 검색, 전화번호 캐싱, 가중치 합산 파이프라인)가 실제로 코드에서 재현되는지 검증하기 위한 격리된 측정 환경.

**기존 `sinchonApp/` 코드는 일절 수정하지 않는다.** 모든 변경은 `bench/` 안에서만 일어난다. 측정 결과로 차이가 발견되면 별도 PR에서 수정한다.

## 측정 항목

| ID | 측정 대상 | 검증할 CV 주장 |
|----|-----------|----------------|
| M1 | `fetch_candidates_mysql` 의 두 경로 (FULLTEXT vs 최신순 fallback) latency | "FULLTEXT INDEX 평균 ~90ms" |
| M2 | `check_spam_number` 캐시 hit / miss latency | "캐시 히트 시 ~10ms" |
| M3 | `compact_case_row` PII 마스킹 적용률 | "PII 마스킹 후 LLM 입력" |
| M4 | end-to-end `AssessView` 파이프라인 단계별 비중 (LLM mock) | "K=50 → K=20 → LLM 재랭킹" |
| M5 | DB 사이즈별 latency (100 / 1k / 10k row) | scalability 근거 |

## 실행 방법

```bash
cd bench
docker compose up -d              # MySQL 8 컨테이너 (port 3307)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python seed.py --size 1000        # 더미 게시글 + 전화번호 캐시 시드
python measure_db_search.py       # M1, M5
python measure_phone_check.py     # M2
python measure_pii.py             # M3
python measure_pipeline.py        # M4
python report.py                  # results/RESULTS.md 생성
```

## 결과

→ `results/RESULTS.md` 참고 (재실행 가능)
