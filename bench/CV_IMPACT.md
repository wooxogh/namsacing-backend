# 측정 결과 → CV 문장 정합성 분석

`cv.md` (career-ops 리포지토리, 우태호 본인) 의 남사칭 섹션 문장을 한 줄씩 측정 결과와 대조한 표. 각 행은 (1) 현재 CV 문장, (2) 측정으로 본 진위, (3) 권장 행동을 담는다.

| # | 현재 CV 문장 (요약) | 측정 결과 | 진위 | 권장 |
|---|---------------------|-----------|------|------|
| 1 | "MySQL FULLTEXT INDEX 와 최신순 정렬 Fallback 활용한 후보 검색 평균 ~90ms" | (BEFORE FIX) production SQL 100% 에러 → 항상 fallback p50 0.5ms 만 실행. (AFTER F1+F2) PROD fetch_candidates_mysql 가 실제 FULLTEXT 경로 실행, **p50 4.41ms (K=20) / 4.38ms (K=50) / 4.94ms (K=100) @ N=10k**, ngram parser. CV 의 90ms 는 어느 경로에도 부합 안 함. | ✅ FIXED | "FULLTEXT (ngram parser) 후보 검색 K=20 기준 **p50 4.4ms / p99 6.2ms** (10k rows, MySQL 8 단일 노드)" 로 갱신 |
| 2 | "후보 검색(K=50) → PII 마스킹 → K=20 Trim → LLM 재랭킹" 파이프라인 설계 | (BEFORE F3) PII 마스킹 단계 입력 항상 빈 문자열, LLM 은 제목 60자만 봄. (AFTER F3) compact_case_row 가 'content' 키 읽도록 수정, M3 검증에서 summary_is_empty: false 확인 → 본문이 실제로 마스킹되어 LLM 에 전달됨. | ✅ FIXED | 그대로 유지. "본문에서 PII 마스킹 후 LLM 에 전달" 표현 정확. |
| 3 | "GPT-4o 파싱 실패를 API 경계에서 차단" | M4 + 코드 리뷰: views.py:47-50 에서 `json.loads` 실패 시 502 반환 ✓ | ✅ TRUE | 그대로. |
| 4 | "정규식 전화번호 추출 및 외부 API 결과 DB 캐싱 (캐시 히트 시 ~10ms)" | M2: cache hit p50 = **0.43ms**, p99 = 0.80ms. CV 의 ~10ms 는 보수적 추정. | ✅ TRUE (보수적) | "캐시 히트 시 sub-millisecond (p50 0.4ms)" 로 강화 가능. 또는 그대로 두고 면접에서 "측정해보니 더 빨랐다" 카드로 사용. |
| 5 | "텍스트와 번호 신호를 55:45 가중치로 합산" | views.py:120 에서 `0.55 * sim_top + 0.45 * contact_freq` 확인 ✓ | ✅ TRUE | 그대로. |
| 6 | "독립 장애 격리 확보" (남사칭 아님 — GIFPT 섹션) | 본 측정 범위 밖. | — | — |

---

## CV 권장 수정 (남사칭 블록만)

**현재:**
> **[제약을 고려한 인프라 최적화]** 24시간 해커톤 제약을 고려, 구축 비용이 높은 Vector DB 대신 **MySQL FULLTEXT INDEX와 최신순 정렬 Fallback**을 활용한 후보 검색 구현. 평균 ~90ms 응답으로 추가 인프라 없이 정밀도 확보.

**측정 후 권장 (F1+F2 수정 머지 완료):**
> **[제약을 고려한 인프라 최적화]** 해커톤 제약 하에 Vector DB 대신 **MySQL FULLTEXT INDEX (ngram parser) + 최신순 fallback** 의 2단 검색 채택. K=20 후보 추출 **p50 4.4ms / p99 6.2ms** (10k 게시글, 단일 노드, MySQL 8, 100 iter). 측정 스크립트와 결과는 `bench/` 디렉토리에서 재현 가능.

**왜 이 변경이 더 강한가:**
- 구체적 수치 + 측정 조건 (N, K, 인프라) 명시 → 실측 신호
- 재현 가능한 벤치마크 디렉토리 링크 → 검증 가능 신호
- "ngram parser" 같은 기술적 디테일 → 한국어 FULLTEXT 까지 고민함을 드러냄
- 주니어들이 거의 안 쓰는 패턴 (수치 출처 명시 + 측정 가능)

---

## 면접 시 활용 시나리오

**Q. "왜 90ms 라고 했어요? 어떻게 측정했나요?"**
- 현재 CV 그대로 두면: 답변 곤란 (실제 90ms 가 어디서 왔는지 불명)
- 수정 후: "10k rows 더미 데이터에 K=20 으로 100회 측정해서 p50 5ms 였습니다. bench/ 디렉토리에 재현 스크립트 있고요. 처음에는 제 코드에 SQL 컬럼 오타가 있어서 FULLTEXT 가 한 번도 안 돌고 fallback 만 돌고 있었는데, 4개월 뒤에 다시 보고 발견해서 고쳤습니다." → **결함 발견 + 수정 + 재측정** 한 사이클 전체가 답변

**Q. "Vector DB 안 쓴 이유?"**
- 답변: "24시간 해커톤이라 인프라 추가 비용 (Pinecone/Weaviate 셋업) 이 KFP 대비 too high. MySQL 은 이미 있으니 ngram FULLTEXT 로 정밀도 충분히 나왔고, fallback 경로까지 두니까 인덱스 미구성 상황에서도 동작. 측정해보니 K=20 에서 5ms 라 LLM 호출 비용이 압도적이고 검색 latency 는 무의미한 수준이었음."

**Q. "PII 마스킹은 왜 했나요?"**
- 답변: "LLM 이 응답에 전화/계좌/URL 을 echo back 하면 사용자에게 다른 사람의 PII 가 노출됨. mask_all 함수로 K=20 후보 모두 정규식 마스킹 후 GPT-4o 에 전달. 측정상 1683건 PII 패턴에서 leak rate 0%. 마스킹 비용은 doc 당 11μs 라 무시 가능."
