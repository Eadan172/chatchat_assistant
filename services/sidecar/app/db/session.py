"""SQLite engine helpers and Alembic upgrade on startup."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

SIDECAR_ROOT = Path(__file__).resolve().parents[2]


def database_url(data_dir: str | None = None) -> str:
    root = Path(data_dir or os.getenv("DATA_DIR") or ".")
    db_path = root / "data" / "app.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def make_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    return engine


def init_db(data_dir: str | None = None) -> str:
    """Apply Alembic migrations and enable WAL. Returns the database URL."""
    url = database_url(data_dir)
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(SIDECAR_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(SIDECAR_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    make_engine(url).dispose()
    return url
