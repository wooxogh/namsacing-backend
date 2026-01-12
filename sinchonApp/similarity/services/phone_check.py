import os
import requests
from prometheus_client import Histogram, Counter
import time
from dataclasses import dataclass
from typing import Optional, Tuple
from django.utils import timezone
from similarity.models import PhoneCheck
from similarity.utils.phone import normalize_kr_number

APICK_BASE = os.getenv("APICK_BASE_URL", "https://apick.app")
APICK_KEY  = os.getenv("APICK_API_KEY", "")
APICK_TIMEOUT = float(os.getenv("APICK_TIMEOUT", "10"))

PHONE_CHECK_LATENCY = Histogram(
    "phone_check_latency_seconds",
    "Latency of phone spam check (cache or external API)",
    ["source"]
)

PHONE_CHECK_TOTAL = Counter(
    "phone_check_total",
    "Total number of phone spam checks",
    ["source", "status"]
)

@dataclass
class PhoneRisk:
    ok: bool
    number: str
    spam: Optional[str]
    spam_count: int
    registed_date: Optional[str]
    cyber_crime: Optional[str]
    success_code: int
    source: str  # "cache" | "api" | "none"

def _parse_spam_count(raw: str) -> int:
    if not raw:
        return 0
    if raw.endswith("+"):
        try:
            return int(raw[:-1])
        except:
            return 0
    try:
        return int(raw)
    except:
        # "1000+" 같은 변형, 숫자만 추출
        import re
        digs = "".join(re.findall(r"\d+", raw))
        return int(digs) if digs else 0

def check_spam_number(raw_number: str, use_cache: bool = True) -> PhoneRisk:
    start_time = time.time()
    number = normalize_kr_number(raw_number)

    # 1) 캐시 조회
    if use_cache:
        row = PhoneCheck.objects.filter(number=number).first()
        if row:
            elapsed = time.time() - start_time
            PHONE_CHECK_LATENCY.labels(source="cache").observe(elapsed)
            PHONE_CHECK_TOTAL.labels(source="cache", status="hit").inc()
            return PhoneRisk(
                ok=bool(row.success == 1),
                number=row.number,
                spam=row.spam,
                spam_count=row.spam_count,
                registed_date=row.registed_date,
                cyber_crime=row.cyber_crime,
                success_code=row.success,
                source="cache",
            )

    # 2) 외부 API 호출
    if not APICK_KEY:
        elapsed = time.time() - start_time
        PHONE_CHECK_LATENCY.labels(source="none").observe(elapsed)
        PHONE_CHECK_TOTAL.labels(source="none", status="skip").inc()
        return PhoneRisk(False, number, None, 0, None, None, 0, "none")

    url = f"{APICK_BASE}/rest/check_spam_number"
    headers = {"CL_AUTH_KEY": APICK_KEY}
    files = {"number": (None, number)}  # multipart/form-data

    try:
        resp = requests.post(url, headers=headers, files=files, timeout=APICK_TIMEOUT, verify=True)
        resp.raise_for_status()
        j = resp.json()
    except Exception as e:
        # 실패 캐시(옵션) 저장
        PhoneCheck.objects.update_or_create(
            number=number,
            defaults=dict(
                spam=None, spam_count_raw=None, spam_count=0,
                registed_date=None, cyber_crime=str(e), success=0,
                last_checked_at=timezone.now(),
            )
        )
        elapsed = time.time() - start_time
        PHONE_CHECK_LATENCY.labels(source="api").observe(elapsed)
        PHONE_CHECK_TOTAL.labels(source="api", status="error").inc()
        return PhoneRisk(False, number, None, 0, None, f"{e}", 0, "api")

    data = (j or {}).get("data") or {}
    success = int(data.get("success", 0))
    spam = data.get("spam")
    spam_count_raw = data.get("spam_count")
    spam_count = _parse_spam_count(spam_count_raw or "")
    registed_date = data.get("registed_date")
    cyber_crime = data.get("cyber_crime")

    # 3) 캐시에 저장/업데이트
    PhoneCheck.objects.update_or_create(
        number=number,
        defaults=dict(
            spam=spam,
            spam_count_raw=spam_count_raw,
            spam_count=spam_count,
            registed_date=registed_date,
            cyber_crime=cyber_crime,
            success=success,
            last_checked_at=timezone.now(),
        )
    )

    elapsed = time.time() - start_time
    PHONE_CHECK_LATENCY.labels(source="api").observe(elapsed)
    PHONE_CHECK_TOTAL.labels(source="api", status="success").inc()

    return PhoneRisk(
        ok=bool(success == 1),
        number=number,
        spam=spam,
        spam_count=spam_count,
        registed_date=registed_date,
        cyber_crime=cyber_crime,
        success_code=success,
        source="api",
    )
