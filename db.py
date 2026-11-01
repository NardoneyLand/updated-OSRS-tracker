"""
db.py — SQLite database for price history, profit alerts, watchlist, and news cache.
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

        CREATE TABLE IF NOT EXISTS profit_alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id    INTEGER NOT NULL,
            item_name  TEXT    NOT NULL,
            buy        INTEGER,
            sell       INTEGER,
            net        INTEGER,
            qty        INTEGER,
            profit     INTEGER,
            roi        REAL,
            timestamp  INTEGER NOT NULL,
            dismissed  INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_alert_item
            ON profit_alerts (item_id, timestamp);

        CREATE TABLE IF NOT EXISTS watchlist (
            item_id    INTEGER PRIMARY KEY,
            item_name  TEXT    NOT NULL,
            added_ts   INTEGER NOT NULL
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

    # Migration: alerts now track live prices and expire (see
    # sync_profit_alerts). updated = when the prices were last confirmed.
    cols = [r[1] for r in c.execute("PRAGMA table_info(profit_alerts)")]
    if "expired" not in cols:
        c.execute("ALTER TABLE profit_alerts "
                  "ADD COLUMN expired INTEGER NOT NULL DEFAULT 0")
    if "updated" not in cols:
        c.execute("ALTER TABLE profit_alerts ADD COLUMN updated INTEGER")
    if "traded_at" not in cols:
        c.execute("ALTER TABLE profit_alerts ADD COLUMN traded_at INTEGER")

    # Migration: the old "moved 10% since your last search" alerts were
    # replaced by profit alerts (profit_alerts).
    c.execute("DROP TABLE IF EXISTS alerts")

    # Migration: one row per (item, API step, timestamp) so concurrent
    # backfills can't store the same point twice. Live snapshots
    # (source_step NULL) are exempt. Remove any existing duplicates first.
    has_unique = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_price_unique'").fetchone()
    if not has_unique:
        c.execute(
            "DELETE FROM price_history WHERE source_step IS NOT NULL "
            "AND id NOT IN (SELECT MIN(id) FROM price_history "
            "               WHERE source_step IS NOT NULL "
            "               GROUP BY item_id, source_step, timestamp)")
        c.execute(
            "CREATE UNIQUE INDEX idx_price_unique "
            "ON price_history (item_id, source_step, timestamp) "
            "WHERE source_step IS NOT NULL")

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
    # OR IGNORE + the unique index make this safe if another backfill for
    # the same item/step inserted the same points in the meantime.
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO price_history "
        "(item_id, item_name, high_price, low_price, timestamp, source_step) "
        "VALUES (?, ?, ?, ?, ?, ?)", new_rows)
    added = conn.total_changes - before
    conn.commit()
    conn.close()
    return added


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


# ── Profit alerts ─────────────────────────────────────────────────────────────

ALERT_RETENTION = 7 * 86400   # keep alert history for 7 days


def sync_profit_alerts(rows: List[dict], cooldown: int,
                       ts: Optional[int] = None) -> Tuple[int, int]:
    """
    Make the alerts match the flips that qualify right now (analyzer.find_flips()
    rows). Returns (new alerts, alerts that just expired).

      - Qualifies, and the item last alerted over `cooldown` ago: a new alert.
      - Qualifies, within the cooldown: its alert gets the live numbers, and
        comes back quietly if it had expired. One the user dismissed stays
        hidden until the cooldown ends.
      - Doesn't qualify: its alert expires.

    One transaction, so a check every minute stays cheap however many flips
    qualify.
    """
    now = int(ts or time.time())
    conn = get_connection()
    latest = {r["item_id"]: r for r in conn.execute(
        "SELECT a.id, a.item_id, a.timestamp, a.dismissed, a.expired "
        "FROM profit_alerts a "
        "JOIN (SELECT MAX(id) AS mid FROM profit_alerts GROUP BY item_id) c "
        "  ON a.id = c.mid")}

    new = 0
    for row in rows:
        prev = latest.get(row["item_id"])
        live = (row["buy"], row["sell"], row["net"], row["qty"], row["profit"],
                row["roi"], row.get("traded_at"), now)
        if prev is None or now - prev["timestamp"] >= cooldown:
            conn.execute(
                "INSERT INTO profit_alerts (buy, sell, net, qty, profit, roi, "
                "traded_at, updated, item_id, item_name, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                live + (row["item_id"], row["name"], now))
            new += 1
        elif not prev["dismissed"]:
            conn.execute(
                "UPDATE profit_alerts SET buy = ?, sell = ?, net = ?, qty = ?, "
                "profit = ?, roi = ?, traded_at = ?, updated = ?, expired = 0 "
                "WHERE id = ?",
                live + (prev["id"],))

    qualifying = {row["item_id"] for row in rows}
    gone = [(item_id,) for item_id, prev in latest.items()
            if item_id not in qualifying
            and not prev["dismissed"] and not prev["expired"]]
    conn.executemany("UPDATE profit_alerts SET expired = 1 "
                     "WHERE item_id = ? AND dismissed = 0", gone)
    conn.commit()
    conn.close()
    return new, len(gone)


def expire_stale_alerts(max_age: int) -> None:
    """Expire alerts whose prices haven't been confirmed for max_age seconds,
    such as those left from the last session or when checks keep failing."""
    conn = get_connection()
    conn.execute(
        "UPDATE profit_alerts SET expired = 1 WHERE expired = 0 "
        "AND dismissed = 0 AND COALESCE(updated, timestamp) < ?",
        (int(time.time()) - max_age,))
    conn.commit()
    conn.close()


def get_active_alerts() -> List[dict]:
    """Latest live alert per item (newest first), with a hit count."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT a.item_id, a.item_name, a.buy, a.sell, a.net, a.qty, "
        "       a.profit, a.roi, a.timestamp, a.traded_at, "
        "       COALESCE(a.updated, a.timestamp) AS updated, c.n "
        "FROM profit_alerts a "
        "JOIN (SELECT item_id, MAX(id) AS mid, COUNT(*) AS n "
        "      FROM profit_alerts WHERE dismissed = 0 GROUP BY item_id) c "
        "  ON a.id = c.mid "
        "WHERE a.expired = 0 "
        "ORDER BY a.timestamp DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def dismiss_alerts(item_id: Optional[int] = None):
    """Dismiss one item's alerts, or all alerts if item_id is None."""
    conn = get_connection()
    if item_id is None:
        conn.execute("UPDATE profit_alerts SET dismissed = 1")
    else:
        conn.execute("UPDATE profit_alerts SET dismissed = 1 WHERE item_id = ?",
                     (item_id,))
    conn.commit()
    conn.close()


def purge_old_alerts(max_age: int = ALERT_RETENTION):
    conn = get_connection()
    conn.execute("DELETE FROM profit_alerts WHERE timestamp < ?",
                 (int(time.time()) - max_age,))
    conn.commit()
    conn.close()


# ── Watchlist ─────────────────────────────────────────────────────────────────

def watchlist_add(item_id: int, item_name: str):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (item_id, item_name, added_ts) "
        "VALUES (?, ?, ?)", (item_id, item_name, int(time.time())))
    conn.commit()
    conn.close()


def watchlist_remove(item_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM watchlist WHERE item_id = ?", (item_id,))
    conn.commit()
    conn.close()


def watchlist_contains(item_id: int) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM watchlist WHERE item_id = ?",
                       (item_id,)).fetchone()
    conn.close()
    return row is not None


def watchlist_items() -> List[Tuple]:
    """(item_id, item_name, added_ts), oldest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT item_id, item_name, added_ts FROM watchlist "
        "ORDER BY added_ts").fetchall()
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
