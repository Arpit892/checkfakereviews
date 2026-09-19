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
import threading
import time
import uuid
from typing import Optional

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SQLITE_PATH = os.environ.get("SQLITE_PATH", "/tmp/cfr_cache.db")
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
            if conn.total_changes == 0:
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
