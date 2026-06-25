"""Run dbt against Supabase, deriving the connection from DATABASE_URL (.env).

Keeps DATABASE_URL as the single secret source: parses it into the DBT_PG_* env
vars profiles.yml expects, then invokes dbt via the dbtRunner API. The Modal dbt
heartbeat reuses run() directly.

Usage:
    python3 dbt/run_dbt.py                      # dbt build (run + test)
    python3 dbt/run_dbt.py run --select marts   # any dbt args
"""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PROJECT_DIR = ROOT / "dbt"


def _set_pg_env() -> None:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set in .env")
    u = urlparse(url)
    os.environ.setdefault("DBT_PG_HOST", u.hostname or "")
    os.environ.setdefault("DBT_PG_PORT", str(u.port or 5432))
    os.environ.setdefault("DBT_PG_USER", unquote(u.username or ""))
    os.environ.setdefault("DBT_PG_PASSWORD", unquote(u.password or ""))
    os.environ.setdefault("DBT_PG_DBNAME", (u.path or "/postgres").lstrip("/") or "postgres")
    os.environ["DBT_PROFILES_DIR"] = str(PROJECT_DIR)


def run(args: list[str] | None = None):
    """Invoke dbt with the given args; returns the dbtRunner result."""
    _set_pg_env()
    from dbt.cli.main import dbtRunner

    args = list(args) if args else ["build"]
    if "--project-dir" not in args:
        args += ["--project-dir", str(PROJECT_DIR)]
    return dbtRunner().invoke(args)


if __name__ == "__main__":
    res = run(sys.argv[1:])
    sys.exit(0 if res.success else 1)
