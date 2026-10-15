"""Runtime configuration, read from environment variables or a .env file."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _normalise_url(url: str) -> str:
    """Accept the plain postgresql:// / postgres:// strings that hosted providers
    (Neon, Supabase, Render...) hand out, and point them at the psycopg 3 driver."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = _normalise_url(os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/capex",
))
SQL_DIR = ROOT / "sql"
REPORTS_DIR = ROOT / "reports"

DEFAULT_SEED = int(os.getenv("CAPEX_SEED", "42"))
DEFAULT_SIMS = int(os.getenv("CAPEX_SIMS", "5000"))
PLAN_YEARS = int(os.getenv("CAPEX_PLAN_YEARS", "5"))  # optimisation window
