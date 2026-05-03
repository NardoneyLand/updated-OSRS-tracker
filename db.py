"""
db.py — SQLite database for price history, alerts, and news cache.
"""

import sqlite3
import time
from typing import Optional, List, Tuple

DB_NAME = "osrs_tracker.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_db():
    """Create all tables and indexes if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS price_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     INTEGER NOT NULL,
            item_name   TEXT    NOT NULL,
            high_price  INTEGER,
            low_price   INTEGER,
            timestamp   INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id      INTEGER NOT NULL,
            item_name    TEXT    NOT NULL,
            alert_type   TEXT    NOT NULL,
            old_price    INTEGER,
            new_price    INTEGER,
            pct_change   REAL,
            timestamp    INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS news_cache (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            title     TEXT,
            url       TEXT UNIQUE,
            content   TEXT,
            source    TEXT,
            timestamp INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_price_item
            ON price_history (item_id, timestamp);
    """)

    conn.commit()
    conn.close()


# ── Price history ─────────────────────────────────────────────────────────────

def record_price(item_id: int, item_name: str, high: int, low: int):
    conn = get_connection()
    conn.execute(
        "INSERT INTO price_history (item_id, item_name, high_price, low_price, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (item_id, item_name, high, low, int(time.time()))
    )
    conn.commit()
    conn.close()


def get_price_history(item_id: int, limit: int = 50) -> List[Tuple]:
    """Return rows (high, low, timestamp) newest-first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT high_price, low_price, timestamp "
        "FROM price_history WHERE item_id = ? "
        "ORDER BY timestamp DESC LIMIT ?",
        (item_id, limit)
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def get_last_recorded_price(item_id: int) -> Optional[Tuple]:
    conn = get_connection()
    row = conn.execute(
        "SELECT high_price, low_price, timestamp "
        "FROM price_history WHERE item_id = ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (item_id,)
    ).fetchone()
    conn.close()
    return tuple(row) if row else None


# ── Alerts ────────────────────────────────────────────────────────────────────

def record_alert(item_id: int, item_name: str, alert_type: str,
                 old_price: int, new_price: int, pct_change: float):
    conn = get_connection()
    conn.execute(
        "INSERT INTO alerts "
        "(item_id, item_name, alert_type, old_price, new_price, pct_change, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item_id, item_name, alert_type, old_price, new_price, pct_change, int(time.time()))
    )
    conn.commit()
    conn.close()


def get_recent_alerts(limit: int = 10) -> List[Tuple]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT item_name, alert_type, old_price, new_price, pct_change, timestamp "
        "FROM alerts ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


# ── News cache ────────────────────────────────────────────────────────────────

def cache_news(title: str, url: str, content: str, source: str):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO news_cache (title, url, content, source, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, url, content, source, int(time.time()))
    )
    conn.commit()
    conn.close()


def get_recent_news(limit: int = 5) -> List[Tuple]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT title, url, source, timestamp "
        "FROM news_cache ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]
