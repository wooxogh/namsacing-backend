"""Seed bench MySQL with synthetic posts and phone_check cache rows.

Why synthetic: namsacing-backend has no production data dump. We generate
data that exercises the same code paths the production AssessView would —
Korean fraud-themed text containing PII (phone, account, URL), spread across
the 5 categories defined in wasscam/models.py CATEGORY_MAP.

Run:
    python seed.py --posts 1000 --phones 500 [--reset]
"""
from __future__ import annotations

import argparse
import os
import random
from datetime import datetime, timedelta

import pymysql

from config import DB, SQL_DIR


CATEGORIES = ["보이스피싱", "종교", "사기", "마약", "기타"]

# Templates per category. Each will get a random PII payload appended.
TEMPLATES = {
    "보이스피싱": [
        "안녕하세요 {agency} {role}입니다. 본인 명의로 {item}이(가) 발송되어 확인 차 연락드립니다. 의심되시면 {phone}으로 회신 부탁드립니다.",
        "고객님 계좌가 범죄에 연루되어 즉시 안전계좌 {account}으로 이체가 필요합니다. 담당 검사 {phone}.",
        "{agency}입니다. 명의도용 신고 접수되었으니 본인 확인을 위해 {url} 링크 접속 후 인증해주세요.",
    ],
    "종교": [
        "주님의 사랑을 함께 나누고 싶습니다. 카톡 ID 또는 전화 {phone}으로 연락 주시면 좋은 모임 안내해드릴게요.",
        "심리 상담 무료로 진행합니다. {url} 신청 후 {phone}으로 인증번호 받으세요.",
        "성경 공부 함께해요. {agency}에서 운영하는 청년 모임입니다. 위치는 {url}.",
    ],
    "사기": [
        "중고나라에서 {item} 거래합니다. 선입금 {account}으로 부탁드립니다. 입금 확인 후 발송. 문의 {phone}.",
        "해외 직구 대행 {url}. 결제는 {account} 계좌이체만 가능합니다. 빠른 답장 {phone}.",
        "{role}이고 급하게 {item} 처분합니다. 지금 입금 가능하시면 {phone} 연락 주세요. 계좌 {account}.",
    ],
    "마약": [
        "{item} 구해드립니다. 텔레그램 {url} 추가 후 메시지 주세요. 시세 협의 가능. 연락 {phone}.",
        "고품질 {item} 안전하게 거래. 입금만 {account}, 직거래 X. 문의 {phone}.",
        "신뢰할 수 있는 분 찾습니다. {item} 정기 거래 가능. {url} 참고. {phone}.",
    ],
    "기타": [
        "최근 {agency} 사칭으로 보이는 메시지를 받았어요. 공유합니다. 피해 신고 {phone}.",
        "친구가 {role} 사칭한 사람한테 {account}으로 입금했어요. 비슷한 사례 {url}.",
        "이상한 링크 받아서 신고합니다. {url} 접속하지 마세요. 출처 추적 {phone}.",
    ],
}

AGENCIES = ["서울중앙지검", "검찰청", "경찰서", "택배회사", "건강보험공단", "신천지", "여의도교회"]
ROLES = ["검사", "수사관", "팀장", "전도사", "선교사", "직원", "지점장"]
ITEMS = [
    "택배", "대출", "보험금", "환급금",
    "아이폰 15", "맥북 프로",
    "필로폰", "아이스", "케타민", "헤로인", "코카인",
]


def random_phone() -> str:
    """010 prefix, KR mobile."""
    return f"010-{random.randint(1000, 9999)}-{random.randint(0, 9999):04d}"


def random_account() -> str:
    """Bank-account-ish digit string."""
    a = random.randint(100, 999)
    b = random.randint(10, 999999)
    c = random.randint(10, 999999)
    return f"{a}-{b}-{c}"


def random_url() -> str:
    domains = ["bit.ly", "tinyurl.com", "naver.me", "scam-site.kr", "free-money.com"]
    paths = ["promo", "verify", "auth", "claim", "join"]
    return f"https://{random.choice(domains)}/{random.choice(paths)}/{random.randint(10000, 99999)}"


def render(category: str) -> tuple[str, str]:
    """Return (title, content) with PII embedded."""
    template = random.choice(TEMPLATES[category])
    body = template.format(
        agency=random.choice(AGENCIES),
        role=random.choice(ROLES),
        item=random.choice(ITEMS),
        phone=random_phone(),
        account=random_account(),
        url=random_url(),
    )
    # Pad body so length distribution is realistic (50–800 chars).
    pad_target = random.randint(50, 800)
    while len(body) < pad_target:
        body += " " + random.choice(["조심하세요", "공유합니다", "주의 바랍니다", "다른 분들도 비슷한 일 겪으셨나요",
                                       "신고 어디로 하나요", "정말 황당하네요", "댓글 부탁드려요"])
    title = body.split()[0:6]
    return " ".join(title)[:80], body


def reset_tables(cur):
    cur.execute("DROP TABLE IF EXISTS wasscam_post")
    cur.execute("DROP TABLE IF EXISTS phone_checks")


def apply_schema(cur):
    schema_path = os.path.join(SQL_DIR, "schema.sql")
    with open(schema_path, encoding="utf-8") as f:
        sql = f.read()
    # Strip comments and split on ; (naive but fine for our schema).
    statements = []
    buf = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        if stripped.endswith(";"):
            statements.append(" ".join(buf))
            buf = []
    for stmt in statements:
        cur.execute(stmt)


def seed_posts(cur, n: int):
    rows = []
    base_time = datetime.now() - timedelta(days=180)
    for i in range(n):
        cat = random.choice(CATEGORIES)
        title, content = render(cat)
        created_at = base_time + timedelta(seconds=random.randint(0, 180 * 86400))
        rows.append((title, cat, content, created_at))
    cur.executemany(
        "INSERT INTO wasscam_post (title, category, content, created_at, author_id) "
        "VALUES (%s, %s, %s, %s, 1)",
        rows,
    )
    print(f"  seeded {n} posts")


def seed_phones(cur, n: int):
    rows = []
    spam_labels = [None, "보이스피싱", "사기", "광고", "스팸"]
    weights =     [0.45,  0.20,        0.15,  0.15,   0.05]
    now = datetime.now()
    seen = set()
    while len(rows) < n:
        num = "010" + f"{random.randint(0, 99999999):08d}"
        if num in seen:
            continue
        seen.add(num)
        spam = random.choices(spam_labels, weights=weights)[0]
        spam_count = 0 if spam is None else random.randint(1, 1500)
        success = 1 if spam else random.choice([0, 1])
        rows.append((
            num, spam,
            f"{spam_count}+" if spam_count >= 1000 else (str(spam_count) if spam_count else None),
            spam_count, "2024-01", None, success, now,
        ))
    cur.executemany(
        "INSERT INTO phone_checks (number, spam, spam_count_raw, spam_count, "
        "registed_date, cyber_crime, success, last_checked_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        rows,
    )
    print(f"  seeded {n} phone_check rows")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--posts", type=int, default=1000)
    p.add_argument("--phones", type=int, default=500)
    p.add_argument("--reset", action="store_true",
                   help="Drop and recreate tables before seeding")
    args = p.parse_args()

    random.seed(42)  # reproducible

    conn = pymysql.connect(**DB)
    try:
        with conn.cursor() as cur:
            if args.reset:
                print("[reset] dropping tables")
                reset_tables(cur)
            print("[schema] applying")
            apply_schema(cur)
            print(f"[seed] inserting {args.posts} posts")
            seed_posts(cur, args.posts)
            print(f"[seed] inserting {args.phones} phone_check rows")
            seed_phones(cur, args.phones)
    finally:
        conn.close()
    print("done.")


if __name__ == "__main__":
    main()
