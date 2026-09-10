"""SQLite storage. This is the actual product: the record nobody else keeps.

Two layers:

  observations -- every raw ETA we ever saw, so results can be re-derived later
                  if the reconstruction below turns out to be wrong.
  arrivals     -- derived: our best estimate of when a bus really turned up.

`tracking` holds the collector's per-service state so a restart does not
invent a phantom arrival.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Dict, Iterable, List, Optional, Tuple

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id         INTEGER PRIMARY KEY,
    stop_code  TEXT    NOT NULL,
    service    TEXT    NOT NULL,
    slot       INTEGER NOT NULL,
    eta_epoch  INTEGER NOT NULL,
    load       TEXT,
    feature    TEXT,
    bus_type   TEXT,
    monitored  INTEGER,
    polled_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS obs_lookup ON observations (stop_code, service, polled_at);
CREATE INDEX IF NOT EXISTS obs_age    ON observations (polled_at);

CREATE TABLE IF NOT EXISTS arrivals (
    id            INTEGER PRIMARY KEY,
    stop_code     TEXT    NOT NULL,
    service       TEXT    NOT NULL,
    arrived_at    INTEGER NOT NULL,
    first_eta     INTEGER,
    first_seen_at INTEGER,
    obs_count     INTEGER,
    error_s       INTEGER,
    daytype       TEXT,
    hour          INTEGER,
    created_at    INTEGER NOT NULL,
    UNIQUE (stop_code, service, arrived_at)
);
CREATE INDEX IF NOT EXISTS arr_lookup ON arrivals (stop_code, service, arrived_at);
CREATE INDEX IF NOT EXISTS arr_bucket ON arrivals (stop_code, service, daytype, hour);

CREATE TABLE IF NOT EXISTS tracking (
    stop_code     TEXT NOT NULL,
    service       TEXT NOT NULL,
    next_eta      INTEGER,
    first_eta     INTEGER,
    first_seen_at INTEGER,
    obs_count     INTEGER DEFAULT 0,
    last_seen_at  INTEGER,
    PRIMARY KEY (stop_code, service)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


# --- writes ----------------------------------------------------------------
def record_observations(conn: sqlite3.Connection, rows: Iterable[tuple]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO observations "
        "(stop_code, service, slot, eta_epoch, load, feature, bus_type, monitored, polled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def record_arrival(conn: sqlite3.Connection, **kw) -> bool:
    """Insert a reconstructed arrival. Returns False if already known."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO arrivals "
        "(stop_code, service, arrived_at, first_eta, first_seen_at, obs_count, error_s,"
        " daytype, hour, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            kw["stop_code"], kw["service"], kw["arrived_at"], kw.get("first_eta"),
            kw.get("first_seen_at"), kw.get("obs_count"), kw.get("error_s"),
            kw.get("daytype"), kw.get("hour"), int(time.time()),
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def get_tracking(conn: sqlite3.Connection) -> Dict[Tuple[str, str], sqlite3.Row]:
    return {(r["stop_code"], r["service"]): r for r in conn.execute("SELECT * FROM tracking")}


def set_tracking(conn: sqlite3.Connection, stop_code: str, service: str, **kw) -> None:
    conn.execute(
        "INSERT INTO tracking (stop_code, service, next_eta, first_eta, first_seen_at,"
        " obs_count, last_seen_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(stop_code, service) DO UPDATE SET "
        " next_eta=excluded.next_eta, first_eta=excluded.first_eta,"
        " first_seen_at=excluded.first_seen_at, obs_count=excluded.obs_count,"
        " last_seen_at=excluded.last_seen_at",
        (stop_code, service, kw.get("next_eta"), kw.get("first_eta"),
         kw.get("first_seen_at"), kw.get("obs_count", 0), kw.get("last_seen_at")),
    )
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def prune(conn: sqlite3.Connection, retention_days: Optional[int] = None) -> int:
    days = config.RAW_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = int(time.time()) - days * 86400
    cur = conn.execute("DELETE FROM observations WHERE polled_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


# --- reads -----------------------------------------------------------------
def arrivals_for(conn: sqlite3.Connection, stop_code: str, service: str) -> List[sqlite3.Row]:
    return list(conn.execute(
        "SELECT arrived_at, error_s, daytype, hour FROM arrivals "
        "WHERE stop_code=? AND service=? ORDER BY arrived_at",
        (stop_code, service),
    ))


def coverage(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) n, MIN(arrived_at) first, MAX(arrived_at) last FROM arrivals"
    ).fetchone()
    obs = conn.execute("SELECT COUNT(*) n FROM observations").fetchone()
    return {
        "arrivals": row["n"] or 0,
        "observations": obs["n"] or 0,
        "first_arrival": row["first"],
        "last_arrival": row["last"],
        "last_poll": int(get_meta(conn, "last_poll_at", 0) or 0),
        "last_poll_ok": get_meta(conn, "last_poll_ok", "") == "1",
        "last_poll_error": get_meta(conn, "last_poll_error", "") or None,
    }
