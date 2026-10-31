"""
db.py — SQLite database for price history, alerts, and news cache.
"""

import sqlite3
import time
from typing import Optional, List, Tuple

import paths

DB_NAME = paths.data_file("osrs_tracker.db")


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

    # Migration: tag rows with the API timestep they came from
    # (NULL = a live snapshot recorded by a search).
    cols = [r[1] for r in c.execute("PRAGMA table_info(price_history)")]
    if "source_step" not in cols:
        c.execute("ALTER TABLE price_history ADD COLUMN source_step TEXT")

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


def backfill_price_history(item_id: int, item_name: str, points: list,
                           step: str) -> int:
    """
    Insert historical API timeseries points (avgHighPrice/avgLowPrice/timestamp)
    for the given timestep ('5m' | '1h' | '6h' | '24h') that we don't already
    have. Returns the number of rows added.
    """
    rows = []
    for pt in points:
        ts = pt.get("timestamp")
        high, low = pt.get("avgHighPrice"), pt.get("avgLowPrice")
        if ts is None or (high is None and low is None):
            continue
        rows.append((item_id, item_name, high or low, low or high, int(ts), step))
    if not rows:
        return 0

    conn = get_connection()
    # Rows saved before the source_step column existed are NULL; those were
    # all 1h backfills or live snapshots, so count them as '1h' for dedup.
    existing = {r[0] for r in conn.execute(
        "SELECT timestamp FROM price_history WHERE item_id = ? "
        "AND (source_step = ? OR (? = '1h' AND source_step IS NULL))",
        (item_id, step, step))}
    new_rows = [r for r in rows if r[4] not in existing]
    conn.executemany(
        "INSERT INTO price_history "
        "(item_id, item_name, high_price, low_price, timestamp, source_step) "
        "VALUES (?, ?, ?, ?, ?, ?)", new_rows)
    conn.commit()
    conn.close()
    return len(new_rows)


def get_price_history(item_id: int, limit: int = 50) -> List[Tuple]:
    """Return rows (high, low, timestamp) newest-first (live + 1h points)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT high_price, low_price, timestamp "
        "FROM price_history WHERE item_id = ? "
        "AND (source_step IS NULL OR source_step = '1h') "
        "ORDER BY timestamp DESC LIMIT ?",
        (item_id, limit)
    ).fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def get_price_range(item_id: int, since_ts: int, step: str,
                    include_live: bool) -> List[Tuple]:
    """
    Return rows (high, low, timestamp) oldest-first, at or after since_ts, for
    one API timestep (plus live/legacy snapshots if include_live).
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT high_price, low_price, timestamp "
        "FROM price_history WHERE item_id = ? AND timestamp >= ? "
        "AND (source_step = ? OR (? AND source_step IS NULL)) "
        "ORDER BY timestamp ASC",
        (item_id, since_ts, step, 1 if include_live else 0)
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
