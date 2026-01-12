# similarity/services/llm.py
import os
from dataclasses import dataclass
from typing import Optional
from openai import OpenAI
from prometheus_client import Histogram, Counter
import time

_client: Optional[OpenAI] = None  # 전역 캐시, 처음 호출 때만 생성

LLM_LATENCY = Histogram(
    "llm_latency_seconds",
    "Latency of LLM API calls",
    ["model", "status"]
)

LLM_CALL_TOTAL = Counter(
    "llm_call_total",
    "Total number of LLM calls",
    ["status"]
)

def _get_client() -> OpenAI:
    global _client
    if _client is not None:
        return _client

    # .env가 로드되었다고 가정하되, 혹시 몰라 한 번 더 시도
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        # 마이그레이션/테스트 단계에서 key 없을 수 있음 → 여기서 바로 예외 던지지 말고
        # call_llm 호출 시점에만 에러가 나도록 둡니다.
        raise RuntimeError("OPENAI_API_KEY is not set")

    _client = OpenAI(api_key=key)
    return _client

@dataclass
class LLMResult:
    ok: bool
    text: str

def call_llm(system: str, user: str) -> LLMResult:
    start_time = time.time()
    try:
        client = _get_client()  # 최초 생성 또는 재사용
        rsp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start_time
        LLM_LATENCY.labels(model="gpt-4o", status="success").observe(elapsed)
        LLM_CALL_TOTAL.labels(status="success").inc()
        return LLMResult(True, rsp.choices[0].message.content)
    except Exception as e:
        elapsed = time.time() - start_time
        LLM_LATENCY.labels(model="gpt-4o", status="error").observe(elapsed)
        LLM_CALL_TOTAL.labels(status="error").inc()
        return LLMResult(False, f"ERROR: {e}")