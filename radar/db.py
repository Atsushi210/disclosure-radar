"""SQLite 保存層（Phase 1）。Phase 3 で Firestore に移す。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "radar.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS disclosures (
  doc_id TEXT PRIMARY KEY,
  published_at TEXT NOT NULL,
  code TEXT NOT NULL,
  company_name TEXT NOT NULL,
  title TEXT NOT NULL,
  pdf_url TEXT NOT NULL,
  xbrl_url TEXT,
  markets TEXT,
  source TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_disc_pub ON disclosures(published_at);
CREATE INDEX IF NOT EXISTS idx_disc_code ON disclosures(code);

CREATE TABLE IF NOT EXISTS events (
  doc_id TEXT NOT NULL REFERENCES disclosures(doc_id),
  type TEXT NOT NULL,
  label TEXT NOT NULL,
  direction TEXT NOT NULL,
  importance INTEGER NOT NULL,
  sales_rate REAL,
  profit_rate REAL,
  notified_at TEXT,
  PRIMARY KEY (doc_id, type)
);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
"""


def init_db(path: Path | None = None) -> None:
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    p = path or DB_PATH
    init_db(p)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
