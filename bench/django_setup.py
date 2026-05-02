"""Bootstrap Django pointed at the bench MySQL.

Importing this module side-effect-configures Django so the rest of the bench
can `from similarity.services.phone_check import check_spam_number` etc. and
exercise the production code unchanged against the bench DB.

Why this is safe: settings.py reads DB_* via os.getenv with defaults. We
inject the bench credentials before django.setup() runs.
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SINCHON_APP = REPO_ROOT / "sinchonApp"
sys.path.insert(0, str(SINCHON_APP))

os.environ.setdefault("DB_ENGINE", "django.db.backends.mysql")
os.environ.setdefault("DB_HOST", os.getenv("BENCH_DB_HOST", "127.0.0.1"))
os.environ.setdefault("DB_PORT", os.getenv("BENCH_DB_PORT", "3307"))
os.environ.setdefault("DB_USER", os.getenv("BENCH_DB_USER", "root"))
os.environ.setdefault("DB_PASSWORD", os.getenv("BENCH_DB_PASSWORD", "benchpw"))
os.environ.setdefault("DB_NAME", os.getenv("BENCH_DB_NAME", "isScam_bench"))

# Avoid touching the production .env if one happens to exist.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sinchonApp.settings")

# OpenAI key is unused (we mock LLM in bench) but the module-level OpenAI
# client init fails without one. Provide a placeholder.
os.environ.setdefault("OPENAI_API_KEY", "sk-bench-placeholder")

# APICK key empty → check_spam_number takes the explicit "no key" branch on miss.
os.environ.setdefault("APICK_API_KEY", "")

import django  # noqa: E402

django.setup()
