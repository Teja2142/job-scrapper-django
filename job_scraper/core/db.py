"""
Database access. query(sql, params) runs a statement from sql.py and returns `rows` (list of
dicts) and `rowcount`. The $1, $2 ... placeholders are converted for Django.

A connection idle for more than 30 seconds is replaced before the next query. A daily run waits
a long time on Apify, and hosted databases often drop idle connections.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from django.db import connections

from . import conf
from .jscompat import to_db_timestamp

IDLE_TIMEOUT_SECONDS = 30

_PLACEHOLDER_RE = re.compile(r'\$(\d+)')
_converted: dict[str, str] = {}
_local = threading.local()


@dataclass
class Result:
    rows: list[dict] = field(default_factory=list)
    rowcount: int = 0


def _convert(sql: str) -> str:
    """$1 -> %(p1)s, and a literal % -> %% (the driver's format escaping)."""
    out = _converted.get(sql)
    if out is None:
        out = _PLACEHOLDER_RE.sub(lambda m: f'%(p{m.group(1)})s', sql.replace('%', '%%'))
        _converted[sql] = out
    return out


def _prepare(value: Any) -> Any:
    """Converts parameter values (dates, lists ...) the way the original app stored them."""
    if isinstance(value, datetime):
        return to_db_timestamp(value)
    if isinstance(value, dict):
        return json.dumps(value, separators=(',', ':'), ensure_ascii=False)
    if isinstance(value, tuple):
        return list(value)
    return value


def connection():
    return connections[conf.db_alias()]


def _maybe_drop_idle(conn) -> None:
    last = getattr(_local, 'last_used', {}).get(conn.alias)
    if (last is not None and time.monotonic() - last > IDLE_TIMEOUT_SECONDS
            and not conn.in_atomic_block and conn.connection is not None):
        conn.close()


def _local_offset_sql() -> str:
    """The process's current UTC offset, as an interval Postgres accepts: '+05:30'."""
    minutes = int(datetime.now().astimezone().utcoffset().total_seconds() // 60)
    sign = '-' if minutes < 0 else '+'
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _use_local_time_zone(conn) -> None:
    """
    NOW() fills TIMESTAMP columns (first_seen_at, ended_at, created_at ...) with the clock time of
    the database session. Django sets the session to UTC, while our own timestamps (started_at,
    posting_date) are written in local time, so the session is switched to the local offset.
    Checked on every query because the offset can change (summer time) and connections get replaced.
    """
    conn.ensure_connection()
    offset = _local_offset_sql()
    raw = conn.connection
    if getattr(conn, '_job_scraper_tz', None) == (id(raw), offset):
        return
    with conn.cursor() as cur:
        cur.execute(f"SET TIME ZONE INTERVAL '{offset}' HOUR TO MINUTE")
    conn._job_scraper_tz = (id(raw), offset)


def query(sql: str, params: Sequence[Any] | None = None) -> Result:
    conn = connection()
    _maybe_drop_idle(conn)
    _use_local_time_zone(conn)
    args = {f'p{i + 1}': _prepare(v) for i, v in enumerate(params or [])}
    try:
        with conn.cursor() as cur:
            cur.execute(_convert(sql), args)
            rows: list[dict] = []
            if cur.description is not None:
                cols = [c[0] for c in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            return Result(rows=rows, rowcount=cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0)
    finally:
        if not hasattr(_local, 'last_used'):
            _local.last_used = {}
        _local.last_used[conn.alias] = time.monotonic()


def execute_script(sql: str) -> None:
    """Runs a multi-statement SQL script (schema.sql) with no parameters."""
    conn = connection()
    _maybe_drop_idle(conn)
    _use_local_time_zone(conn)
    with conn.cursor() as cur:
        cur.execute(sql)


def close_thread_connections() -> None:
    """Closes this thread's database connections (worker threads call it when they finish)."""
    connections.close_all()


def end() -> None:
    """Closes every connection this module opened."""
    connections.close_all()
