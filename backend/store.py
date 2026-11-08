"""Optional Tiger Data store (TimescaleDB on PostgreSQL) for time-series data.

Set DATABASE_URL to your Tiger Cloud service URL (postgres://…) and PyroWatch keeps:
  risk_predictions  every live risk computation, per area (hypertable on recorded_at)
  fire_detections   every satellite fire detection it has fetched (hypertable on time)
  ground_readings   every reading from a PyroWatch Beacon ground station (hypertable on time)

That powers the "risk over time" chart in each area's panel, and lets /api/validate check a
*recorded* prediction from the database instead of a hindcast. It works on plain PostgreSQL too
(no hypertables or time_bucket, same results), so you can develop against a local Postgres.
Without DATABASE_URL every function here is a no-op.
"""
import logging
import threading
import time
from datetime import date

from .config import DATABASE_URL

log = logging.getLogger("pyrowatch.store")
_lock = threading.Lock()
_state = {"conn": None, "ready": None, "timescale": False, "error": None, "failed_at": 0.0}
RETRY_AFTER_S = 60

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS risk_predictions (
         recorded_at     timestamptz NOT NULL,
         as_of           date        NOT NULL,
         cell_id         text        NOT NULL,
         risk            real        NOT NULL,
         category        text        NOT NULL,
         ml_prob         real,
         gust_kmh        real,
         rh_min_pct      real,
         days_since_rain real,
         thunderstorm_score real,
         blizzard_score  real,
         PRIMARY KEY (cell_id, recorded_at))""",
    """CREATE TABLE IF NOT EXISTS fire_detections (
         time        timestamptz      NOT NULL,
         lat         double precision NOT NULL,
         lon         double precision NOT NULL,
         cell_id     text,
         satellite   text             NOT NULL,
         instrument  text,
         confidence  text,
         frp         real,
         source      text,
         PRIMARY KEY (time, lat, lon, satellite))""",
    """CREATE TABLE IF NOT EXISTS ground_readings (
         time        timestamptz      NOT NULL,
         station_id  text             NOT NULL,
         lat         double precision NOT NULL,
         lon         double precision NOT NULL,
         temp_c      real,
         rh_pct      real,
         smoke_raw   real,
         PRIMARY KEY (station_id, time))""",
    "CREATE INDEX IF NOT EXISTS risk_predictions_asof ON risk_predictions (as_of, recorded_at)",
    "CREATE INDEX IF NOT EXISTS fire_detections_cell ON fire_detections (cell_id, time DESC)",
]
MIGRATIONS = [
    # CREATE TABLE IF NOT EXISTS does not add columns to existing PostgreSQL/TimescaleDB
    # installations. Nullable columns keep older rows valid and make this migration repeatable.
    "ALTER TABLE risk_predictions ADD COLUMN IF NOT EXISTS thunderstorm_score real",
    "ALTER TABLE risk_predictions ADD COLUMN IF NOT EXISTS blizzard_score real",
]
HYPERTABLES = [
    "SELECT create_hypertable('risk_predictions', 'recorded_at', if_not_exists => TRUE, migrate_data => TRUE)",
    "SELECT create_hypertable('fire_detections', 'time', if_not_exists => TRUE, migrate_data => TRUE)",
    "SELECT create_hypertable('ground_readings', 'time', if_not_exists => TRUE, migrate_data => TRUE)",
]


def _connect():
    """Open (or reuse) the connection and create the schema once. Returns None when disabled."""
    if not DATABASE_URL:
        return None
    if _state["ready"] is False:
        if time.time() - _state["failed_at"] < RETRY_AFTER_S:
            return None
        _state["ready"] = None  # try again
    conn = _state["conn"]
    if conn is not None and not conn.closed:
        return conn
    try:
        import psycopg

        conn = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=8, client_encoding="UTF8")
        if _state["ready"] is None:
            for sql in SCHEMA:
                conn.execute(sql)
            for sql in MIGRATIONS:
                conn.execute(sql)
            try:
                conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
                for sql in HYPERTABLES:
                    conn.execute(sql)
                _state["timescale"] = True
            except Exception as exc:  # noqa: BLE001 - plain Postgres works too
                log.info("TimescaleDB not available (%s); using plain PostgreSQL tables", type(exc).__name__)
                _state["timescale"] = False
            _state["ready"] = True
            log.info("Tiger Data store ready (timescale=%s)", _state["timescale"])
        _state["conn"] = conn
        return conn
    except Exception as exc:  # noqa: BLE001
        _state.update(ready=False, failed_at=time.time(), error=f"{type(exc).__name__}: {str(exc)[:200]}")
        log.warning("database disabled: %s", _state["error"])
        return None


def status():
    with _lock:
        _connect()
    return {"enabled": bool(DATABASE_URL), "ready": bool(_state["ready"]), "timescale": _state["timescale"], "error": _state["error"]}


def _run(fn):
    with _lock:
        conn = _connect()
        if conn is None:
            return None
        try:
            return fn(conn)
        except Exception as exc:  # noqa: BLE001
            log.warning("database call failed: %s", exc)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            _state["conn"] = None
            return None


def in_background(fn, *args):
    """Fire-and-forget write, so API responses never wait on the database."""
    if DATABASE_URL:
        threading.Thread(target=fn, args=args, daemon=True).start()


def record_prediction(result):
    rows = [
        (result["generated_at"], result["as_of"], c["id"], c["risk"], c["category"], c.get("ml_prob"),
         c["raw"].get("gust_kmh"), c["raw"].get("rh_min_pct"), c["raw"].get("days_since_rain"),
         (c.get("hazards", {}).get("thunderstorm") or {}).get("score"),
         (c.get("hazards", {}).get("blizzard") or {}).get("score"))
        for c in result["cells"]
    ]

    def write(conn):
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO risk_predictions "
                "(recorded_at, as_of, cell_id, risk, category, ml_prob, gust_kmh, rh_min_pct, days_since_rain, "
                "thunderstorm_score, blizzard_score) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", rows)
        return len(rows)

    return _run(write)


def record_detections(dets):
    rows = [(d["time_utc"], d["lat"], d["lon"], d.get("cell_id"), d["satellite"], d["instrument"],
             d["confidence"], d["frp"], d["source"]) for d in dets]
    if not rows:
        return 0

    def write(conn):
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO fire_detections VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", rows)
        return len(rows)

    return _run(write)


def record_ground_reading(station_id, lat, lon, reading):
    return _run(lambda conn: conn.execute(
        "INSERT INTO ground_readings VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
        (reading["time_utc"], station_id, lat, lon, reading["temp_c"], reading["rh_pct"], reading["smoke_raw"])))


def recorded_prediction(as_of: date):
    """The earliest prediction recorded for `as_of`, as {cell_id: risk}, or None."""

    def read(conn):
        row = conn.execute(
            "SELECT min(recorded_at) FROM risk_predictions WHERE as_of = %s", (as_of,)).fetchone()
        if not row or row[0] is None:
            return None
        cells = conn.execute(
            "SELECT cell_id, risk, category, ml_prob FROM risk_predictions WHERE as_of = %s AND recorded_at = %s",
            (as_of, row[0])).fetchall()
        return {
            "as_of": as_of.isoformat(),
            "recorded_at": row[0].isoformat(),
            "cells": {cid: {"risk": r, "category": cat, "ml_prob": p} for cid, r, cat, p in cells},
        }

    return _run(read)


def cell_history(cell_id, days=14):
    """Hourly risk series and daily detection counts for one area."""
    bucket = "time_bucket(%s::interval, {col})" if _state["timescale"] else "date_trunc(%s, {col})"
    hour, day = ("1 hour", "1 day") if _state["timescale"] else ("hour", "day")

    def read(conn):
        risk = conn.execute(
            f"SELECT {bucket.format(col='recorded_at')} AS t, avg(risk), max(risk) FROM risk_predictions "
            "WHERE cell_id = %s AND recorded_at > now() - make_interval(days => %s) GROUP BY t ORDER BY t",
            (hour, cell_id, days)).fetchall()
        fires = conn.execute(
            f"SELECT {bucket.format(col='time')} AS t, count(*), max(frp) FROM fire_detections "
            "WHERE cell_id = %s AND time > now() - make_interval(days => %s) GROUP BY t ORDER BY t",
            (day, cell_id, days)).fetchall()
        return {
            "risk": [{"t": t.isoformat(), "avg": round(a, 1), "max": round(m, 1)} for t, a, m in risk],
            "fires": [{"t": t.isoformat(), "count": n, "max_frp": round(f or 0, 1)} for t, n, f in fires],
        }

    out = _run(read)
    return {"enabled": out is not None, "timescale": _state["timescale"], "days": days, **(out or {"risk": [], "fires": []})}
