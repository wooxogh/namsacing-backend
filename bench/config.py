"""Shared bench configuration. Reads from env, defaults to local docker-compose."""
import os

DB = dict(
    host=os.getenv("BENCH_DB_HOST", "127.0.0.1"),
    port=int(os.getenv("BENCH_DB_PORT", "3307")),
    user=os.getenv("BENCH_DB_USER", "root"),
    password=os.getenv("BENCH_DB_PASSWORD", "benchpw"),
    database=os.getenv("BENCH_DB_NAME", "isScam_bench"),
    charset="utf8mb4",
    cursorclass=__import__("pymysql.cursors", fromlist=["DictCursor"]).DictCursor,
    autocommit=True,
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SINCHON_APP = os.path.join(REPO_ROOT, "sinchonApp")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
SQL_DIR = os.path.join(os.path.dirname(__file__), "sql")
