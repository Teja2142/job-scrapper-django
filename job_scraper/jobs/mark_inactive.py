from __future__ import annotations

from ..core import db, log
from ..core.sql import SQL


def mark_stale_jobs_inactive(days: int) -> int:
    """
    Sets is_active = FALSE on jobs no scrape has seen for `days` days (by last_scraped_at).
    Nothing is deleted; a job that is scraped again becomes active again.
    """
    res = db.query(SQL.jobs.markStaleInactive, [days])
    log.info(f'[markInactive] {res.rowcount} job(s) not seen in {days} day(s) marked inactive')
    return res.rowcount
