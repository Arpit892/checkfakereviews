"""
storage.py
Caches finished analyses and tracks background jobs.

Backends:
    * SQLite (default) — zero setup, but Render's free tier has an EPHEMERAL
      filesystem: the disk is wiped on every deploy AND every cold start after
      the service spins down. Fine for short-lived caching, useless for history.
    * Postgres — set DATABASE_URL (Neon, Supabase, Railway all have free tiers)
      and install psycopg[binary]. This is the "host the database somewhere
      online" piece, and it's what makes the cache actually survive restarts.

Caching matters more than it sounds: a cache hit skips the scrape entirely,
which means fewer requests to Amazon/Flipkart, which means fewer chances to
get your IP blocked.
"""

import json
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from typing import Optional

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
# /tmp exists on Render/Linux but not on a normal Windows installation.
# The home worker and local development should work without requiring callers
# to discover and set SQLITE_PATH themselves.
SQLITE_PATH = os.environ.get(
    "SQLITE_PATH", os.path.join(tempfile.gettempdir(), "cfr_cache.db")
)
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", str(6 * 3600)))

_lock = threading.Lock()
_pg = None


def _using_postgres() -> bool:
    return bool(DATABASE_URL) and _pg is not None


def _pg_connect():
    global _pg
    if not DATABASE_URL:
        return None
    try:
        import psycopg
        from psycopg_pool import ConnectionPool

        _pg = ConnectionPool(DATABASE_URL, min_size=1, max_size=4, kwargs={"autocommit": True})
        return _pg
    except Exception as e:  # noqa: BLE001
        print(f"[storage] Postgres unavailable ({e}); falling back to SQLite.")
        _pg = None
        return None


def init():
    _pg_connect()
    if _using_postgres():
        with _pg.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    cache_key   TEXT PRIMARY KEY,
                    product_id  TEXT,
                    site        TEXT,
                    payload     JSONB NOT NULL,
                    created_at  DOUBLE PRECISION NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id     TEXT PRIMARY KEY,
                    status     TEXT NOT NULL,
                    url        TEXT,
                    payload    JSONB,
                    created_at DOUBLE PRECISION NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL
                )
                """
            )
    else:
        with _lock, sqlite3.connect(SQLITE_PATH) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    cache_key TEXT PRIMARY KEY,
                    product_id TEXT, site TEXT,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL, url TEXT, payload TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                )
                """
            )


def backend_name() -> str:
    return "postgres" if _using_postgres() else f"sqlite:{SQLITE_PATH}"


# ----------------------------- cache --------------------------------------

def get_cached(cache_key: str) -> Optional[dict]:
    cutoff = time.time() - CACHE_TTL_SECONDS
    try:
        if _using_postgres():
            with _pg.connection() as conn:
                row = conn.execute(
                    "SELECT payload, created_at FROM analyses WHERE cache_key=%s AND created_at>%s",
                    (cache_key, cutoff),
                ).fetchone()
                if not row:
                    return None
                payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                payload["cached_at"] = row[1]
                return payload
        with _lock, sqlite3.connect(SQLITE_PATH) as conn:
            row = conn.execute(
                "SELECT payload, created_at FROM analyses WHERE cache_key=? AND created_at>?",
                (cache_key, cutoff),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        payload["cached_at"] = row[1]
        return payload
    except Exception as e:  # noqa: BLE001
        print(f"[storage] cache read failed: {e}")
        return None


def put_cached(cache_key: str, product_id: str, site: str, payload: dict) -> None:
    try:
        blob = json.dumps(payload)
        now = time.time()
        if _using_postgres():
            with _pg.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO analyses (cache_key, product_id, site, payload, created_at)
                    VALUES (%s,%s,%s,%s::jsonb,%s)
                    ON CONFLICT (cache_key) DO UPDATE
                      SET payload=EXCLUDED.payload, created_at=EXCLUDED.created_at
                    """,
                    (cache_key, product_id, site, blob, now),
                )
            return
        with _lock, sqlite3.connect(SQLITE_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO analyses VALUES (?,?,?,?,?)",
                (cache_key, product_id, site, blob, now),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[storage] cache write failed: {e}")


# ------------------------------ jobs --------------------------------------

def create_job(url: str) -> str:
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    try:
        if _using_postgres():
            with _pg.connection() as conn:
                conn.execute(
                    "INSERT INTO jobs (job_id,status,url,payload,created_at,updated_at) "
                    "VALUES (%s,'queued',%s,NULL,%s,%s)",
                    (job_id, url, now, now),
                )
        else:
            with _lock, sqlite3.connect(SQLITE_PATH) as conn:
                conn.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                    (job_id, "queued", url, None, now, now),
                )
    except Exception as e:  # noqa: BLE001
        print(f"[storage] job create failed: {e}")
    return job_id


def claim_next_job(max_age_seconds: int = 900) -> Optional[dict]:
    """
    Atomically hand the oldest queued job to a worker. Jobs older than
    max_age_seconds are skipped so a stale queue doesn't get replayed after
    the service wakes from sleep.
    """
    cutoff = time.time() - max_age_seconds
    now = time.time()
    try:
        if _using_postgres():
            with _pg.connection() as conn:
                conn.execute("UPDATE jobs SET status='queued' WHERE status='running' AND updated_at < %s", (now - 300,))
                row = conn.execute(
                    """
                    UPDATE jobs SET status='running', updated_at=%s
                    WHERE job_id = (
                        SELECT job_id FROM jobs
                        WHERE status='queued' AND created_at > %s
                        ORDER BY created_at LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING job_id, url
                    """,
                    (now, cutoff),
                ).fetchone()
                return {"job_id": row[0], "url": row[1]} if row else None

        with _lock, sqlite3.connect(SQLITE_PATH) as conn:
            conn.execute("UPDATE jobs SET status='queued' WHERE status='running' AND updated_at < ?", (now - 300,))
            row = conn.execute(
                "SELECT job_id,url FROM jobs WHERE status='queued' AND created_at>? "
                "ORDER BY created_at LIMIT 1",
                (cutoff,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE jobs SET status='running', updated_at=? WHERE job_id=? AND status='queued'",
                (now, row[0]),
            )
            if conn.execute('SELECT changes()').fetchone()[0] == 0:
                return None
        return {"job_id": row[0], "url": row[1]}
    except Exception as e:  # noqa: BLE001
        print(f"[storage] job claim failed: {e}")
        return None


def update_job(job_id: str, status: str, payload: Optional[dict] = None) -> None:
    blob = json.dumps(payload) if payload is not None else None
    now = time.time()
    try:
        if _using_postgres():
            with _pg.connection() as conn:
                conn.execute(
                    "UPDATE jobs SET status=%s, payload=%s::jsonb, updated_at=%s WHERE job_id=%s",
                    (status, blob, now, job_id),
                )
            return
        with _lock, sqlite3.connect(SQLITE_PATH) as conn:
            conn.execute(
                "UPDATE jobs SET status=?, payload=?, updated_at=? WHERE job_id=?",
                (status, blob, now, job_id),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[storage] job update failed: {e}")


def get_job(job_id: str) -> Optional[dict]:
    try:
        if _using_postgres():
            with _pg.connection() as conn:
                row = conn.execute(
                    "SELECT job_id,status,url,payload FROM jobs WHERE job_id=%s", (job_id,)
                ).fetchone()
        else:
            with _lock, sqlite3.connect(SQLITE_PATH) as conn:
                row = conn.execute(
                    "SELECT job_id,status,url,payload FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
        if not row:
            return None
        payload = row[3]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return {"job_id": row[0], "status": row[1], "url": row[2], "result": payload}
    except Exception as e:  # noqa: BLE001
        print(f"[storage] job read failed: {e}")
        return None


# ----------------------------- price alerts --------------------------------
# One row per (product, Telegram chat). chat_id stays NULL until the visitor
# taps the Telegram link and the bot receives "/start <link_code>".

import secrets as _secrets

ALERT_COLUMNS = "alert_id,link_code,chat_id,url,site,product_name,target_price,last_price,last_checked,active,created_at"


def _q(sql: str, params=(), fetch: str = ""):
    """Run SQL on Postgres or SQLite. Write SQL with '?' placeholders."""
    if _using_postgres():
        with _pg.connection() as conn:
            cur = conn.execute(sql.replace("?", "%s"), params)
            return cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else None
    with _lock, sqlite3.connect(SQLITE_PATH) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else None


def init_alerts():
    _q("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            alert_id TEXT PRIMARY KEY, link_code TEXT UNIQUE NOT NULL, chat_id TEXT,
            url TEXT NOT NULL, site TEXT, product_name TEXT,
            target_price DOUBLE PRECISION, last_price DOUBLE PRECISION,
            last_checked DOUBLE PRECISION NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1, created_at DOUBLE PRECISION NOT NULL
        )""")


def _alert_row(row) -> Optional[dict]:
    if not row:
        return None
    d = dict(zip(ALERT_COLUMNS.split(","), row))
    d["active"] = bool(d["active"])
    return d


def create_alert(url, site, target_price=None, product_name="", current_price=None) -> dict:
    alert_id = uuid.uuid4().hex[:16]
    code = _secrets.token_hex(8)
    _q(f"INSERT INTO price_alerts ({ALERT_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
       (alert_id, code, None, url, site, product_name, target_price, current_price, 0, 1, time.time()))
    return get_alert(alert_id)


def get_alert(alert_id: str) -> Optional[dict]:
    return _alert_row(_q(f"SELECT {ALERT_COLUMNS} FROM price_alerts WHERE alert_id=?", (alert_id,), "one"))


def link_alert(code: str, chat_id) -> Optional[dict]:
    a = _alert_row(_q(f"SELECT {ALERT_COLUMNS} FROM price_alerts WHERE link_code=?", (code,), "one"))
    if not a or (a["chat_id"] and a["chat_id"] != str(chat_id)):
        return None  # unknown code, or already claimed by another Telegram account
    _q("UPDATE price_alerts SET chat_id=?, active=1 WHERE alert_id=?", (str(chat_id), a["alert_id"]))
    return get_alert(a["alert_id"])


def due_alerts(every_seconds: int, limit: int = 50) -> list:
    rows = _q(f"SELECT {ALERT_COLUMNS} FROM price_alerts WHERE active=1 AND chat_id IS NOT NULL "
              "AND last_checked < ? ORDER BY last_checked LIMIT ?", (time.time() - every_seconds, limit), "all")
    return [_alert_row(r) for r in rows or []]


def record_price(alert_id: str, price: Optional[float], product_name: str = "") -> None:
    if price is None:
        _q("UPDATE price_alerts SET last_checked=? WHERE alert_id=?", (time.time(), alert_id))
    else:
        _q("UPDATE price_alerts SET last_price=?, last_checked=?, "
           "product_name=COALESCE(NULLIF(?, ''), product_name) WHERE alert_id=?",
           (price, time.time(), product_name, alert_id))


def chat_alerts(chat_id) -> list:
    rows = _q(f"SELECT {ALERT_COLUMNS} FROM price_alerts WHERE chat_id=? AND active=1 ORDER BY created_at",
              (str(chat_id),), "all")
    return [_alert_row(r) for r in rows or []]


def stop_chat(chat_id) -> int:
    n = len(chat_alerts(chat_id))
    _q("UPDATE price_alerts SET active=0 WHERE chat_id=?", (str(chat_id),))
    return n
