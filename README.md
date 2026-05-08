# 남사칭 — 사칭 사기 예방 백엔드

> **신촌톤 7팀 (2025-09, 24h 해커톤).** 백엔드 4명 / 프론트 3명 / 기획 1명.
> 게시글 본문 유사도와 전화번호 spam 신호를 합쳐 사칭 신고를 평가하는 LLM 기반 백엔드.
> 해커톤 종료 4개월 후, 자기 코드를 다시 읽다 결함 4건을 발견 → 측정 인프라(`bench/`)를 구축해 검증·수정한 사이클이 함께 정리되어 있습니다.

[![bench](https://img.shields.io/badge/bench-reproducible-success)](bench/)
[![Django](https://img.shields.io/badge/Django-5-092E20?logo=django&logoColor=white)]()
[![MySQL](https://img.shields.io/badge/MySQL_8-FULLTEXT_ngram-4479A1?logo=mysql&logoColor=white)]()
[![iter](https://img.shields.io/badge/measurements-100_iter-blue)]()

---

## 핵심 결과 (재현 가능)

모든 수치는 [`bench/`](bench/) 디렉토리에서 `docker compose up -d && python report.py`로 재현 가능합니다.

| 항목 | 수치 | 측정 조건 |
|------|------|-----------|
| **MySQL FULLTEXT 후보 검색** | **p50 4.4ms / p99 6.2ms** (K=20) | 10k rows · MySQL 8 단일 노드 · ngram parser · 100 iter |
| **외부 API 캐시 hit** | **p50 0.43ms / p99 0.80ms** | 1k cached numbers · 100 iter |
| **Hit rate 80% 시 effective mean** | **47.7ms** | 500 req × 8 hit-rate buckets · 200ms apick mock |
| **PII 마스킹 leak rate** | **0%** (1683 합성 PII) | phone / account / url 정규식 3종 · doc 당 11μs |
| **LLM 호출 (참고)** | 1-3s | GPT-4o · 후보 K=20 압축 후 |

→ **함의:** 검색 latency가 LLM 호출의 0.5% 미만이라 Vector DB 추가 가치 낮음. 해커톤 제약 하에서 MySQL FULLTEXT (ngram) + fallback의 2단 구조가 적정 결정이었음을 측정으로 사후 검증.

---

## 이 프로젝트가 보여주는 것

1. **제약 하 결정** — 24h 해커톤 + Vector DB 셋업 비용 vs MySQL FULLTEXT (ngram) → 측정으로 결정 정당화
2. **결함의 정직한 발견과 검증** — 해커톤 종료 4개월 후 자기 코드 정독 중 SQL 컬럼 오타 (F1) + dict key mismatch (F3) + dependency 누락 (F4) 발견. 일단 production 코드는 손대지 않고 `bench/`에서 측정으로 silent degradation 입증, 별도 PR로 수정. 보고서: [`bench/FINDINGS.md`](bench/FINDINGS.md)
3. **CV 수치의 정합성 강제** — [`bench/CV_IMPACT.md`](bench/CV_IMPACT.md)가 CV의 남사칭 문장을 한 줄씩 측정 결과로 검증. "평균 ~90ms" 같은 추정치는 실측치로 교정 (p50 4.4ms).

---

## 시스템 아키텍처

```
[FE]
  │
  ▼
[Django REST API] ──── /assess (게시글 평가)
        │
        ├─ fetch_candidates_mysql (K=50)
        │     └─ MySQL FULLTEXT (ngram) ─ fallback (최신순)
        │
        ├─ compact_case_row × K=50
        │     └─ PII 마스킹 (phone / account / url)
        │
        ├─ K=20 trim (LLM context 비용 제어)
        │
        ├─ GPT-4o 재랭킹 ─ JSON 파싱 실패 시 502 차단
        │
        └─ risk_score = 0.55 × sim_top + 0.45 × contact_freq
              └─ apick spam 매칭 시 high 격상

[/assess 호출과 별도]
[Phone Spam] ── DB cache hit (sub-ms) → miss 시 apick API 호출 → upsert
```

### 핵심 결정과 trade-off

| 결정 | 선택 | 대안 | 근거 |
|------|------|------|------|
| 후보 검색 | MySQL FULLTEXT + fallback | Vector DB (Pinecone/Weaviate) | 셋업 비용 vs 측정 결과 latency 4ms — Vector DB 가치 낮음 |
| FULLTEXT parser | ngram (`ngram_token_size=2`) | 기본 parser | 한국어 토큰화 — 영어 기준 default는 한국어에서 거의 무용 |
| LLM 입력 압축 | PII 마스킹 + K=20 trim | 원문 그대로 | (a) 응답 echo-back 시 PII 누설 차단 (b) context cost 제어 |
| 점수 합성 | 0.55 × sim + 0.45 × spam | 단일 신호 | 짧은 글 텍스트 신뢰도 낮음 → 번호 신호 거의 절반 가중 |
| JWT | Stateless · 60min TTL · no blacklist | DB-backed sessions | 매 요청 DB 왕복 0회 — trade-off는 비활성화 1시간 지연 (도메인 OK) |

---

## 발견된 결함 (post-hoc, 4개월 뒤)

`bench/FINDINGS.md`에서 production 코드 일절 미수정 상태로 측정만으로 검증한 결함들:

| ID | 결함 | 검증 | 수정 |
|----|------|------|------|
| F1 | `db_search.py` SQL의 `body` 컬럼 (실제 모델은 `content`) → FULLTEXT 100% 에러 → 항상 fallback만 실행 | M1: 100% 케이스 `Unknown column 'body'` | SQL 수정 + alias |
| F2 | `wasscam_post`에 FULLTEXT INDEX 마이그레이션 자체 부재 | 0001_initial.py 전수 검사 | raw migration 추가 (`WITH PARSER ngram`) |
| F3 | `compact_case_row`가 `row.get("body")`를 읽는데 dict 키는 `content` → LLM에 항상 빈 summary 전달 | M3: summary_is_empty 100% | 키 통일 |
| F4 | `prometheus_client` import는 있지만 `requirements.txt`에 없음 → 부팅 자체 불가 | requirements.txt 전수 검사 | dependency 1줄 추가 |

P0 우선순위·증거·수정 방향 모두 [`bench/FINDINGS.md`](bench/FINDINGS.md)에 기록.

---

## 기술 스택

| Layer | Stack | Notes |
|------|-------|------|
| Backend | Django 5 · DRF | API · Serializer · Permission · Pagination |
| Auth | simplejwt | 60min ACCESS · no blacklist (검증된 trade-off) |
| Storage | AWS S3 · boto3 | Presigned PUT / GET |
| DB | MySQL 8 | FULLTEXT INDEX · ngram parser · `ngram_token_size=2` |
| Infra | Nginx · Docker Compose | 로컬·배포 환경 일관성 |
| Web | django-cors-headers | 프론트엔드 오리진 화이트리스트 |
| Observability | prometheus_client | Histogram (latency) · Counter (fallback rate) |

---

## 폴더 구조

```
sinchonApp/
├── isscam/         # 사칭 분석 도메인
├── similarity/     # 유사도 + 검색 + 재랭킹
│   ├── services/db_search.py    # FULLTEXT + fallback
│   └── utils/compact.py         # PII 마스킹 + LLM 입력 압축
├── wasscam/        # 게시글 / 신고
├── storage/        # S3
├── user/           # JWT
└── sinchonApp/     # settings · urls

bench/              # post-hoc 측정 인프라 (production 코드 미수정)
├── measure_db_search.py    # M1, M5 (FULLTEXT vs fallback latency)
├── measure_phone_check.py  # M2 (캐시 hit/miss)
├── measure_pii.py          # M3 (PII leak rate)
├── measure_pipeline.py     # M4 (E2E 단계별 비중)
├── seed.py                 # 1k / 10k row 시드
├── FINDINGS.md             # F1~F5 결함 보고
├── CV_IMPACT.md            # 측정 결과 vs CV 문장 정합성
└── results/                # RESULTS.md (재현 가능)
```

---

## 재현 (`bench/`)

```bash
cd bench
docker compose up -d                  # MySQL 8 (port 3307)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python seed.py --size 10000           # 1k / 10k 사이즈 sweep 가능
python measure_db_search.py           # M1, M5
python measure_phone_check.py         # M2
python measure_pii.py                 # M3
python measure_pipeline.py            # M4
python report.py                      # results/RESULTS.md 생성
```

자세한 측정 정의: [`bench/README.md`](bench/README.md)

---

## 팀 (신촌톤 7팀)

| 이름 | 학교 | 포지션 |
|------|------|--------|
| 강문정 | 연세 | 기획·디자인 |
| 우태호 | 연세 | 백엔드 (검색 / 재랭킹 / PII / spam 캐시 / `bench/` 인프라) |
| 백세빈 | 연세 | 백엔드 |
| 김연우 | 이화 | 백엔드 |
| 신지민 | 이화 | 백엔드 |
| 황영준 | 홍익 | 프론트엔드 |
| 장창엽 | 서강 | 프론트엔드 |
| 이윤서 | 홍익 | 프론트엔드 |

---

## 연관 문서

- [`bench/FINDINGS.md`](bench/FINDINGS.md) — F1~F5 결함 보고 (post-hoc self-discovery)
- [`bench/CV_IMPACT.md`](bench/CV_IMPACT.md) — 측정 결과 vs CV 문장 정합성 분석
- [`bench/README.md`](bench/README.md) — 측정 항목 정의 (M1~M6)
