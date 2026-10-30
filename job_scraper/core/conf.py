"""
Settings. Everything comes from environment variables (APIFY_API_TOKEN, SCRAPE_MAX_JOBS_PER_ROLE ...).
A .env file in the folder you run manage.py from is loaded first; values already in the
environment win. JOB_SCRAPER_ENV_FILE=<path> loads another file, JOB_SCRAPER_ENV_FILE="" none.

The database is one of the project's DATABASES entries, named by JOB_SCRAPER_DB_ALIAS
(default "default"). It must be PostgreSQL.
"""
from __future__ import annotations

import os

_loaded = False


def load_env_file() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = os.environ.get('JOB_SCRAPER_ENV_FILE')
    if path is None:
        path = os.path.join(os.getcwd(), '.env')
    if not path or not os.path.isfile(path):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv not installed: rely on the real environment
        return
    load_dotenv(path, override=False)


load_env_file()


def env(name: str) -> str | None:
    """The environment variable, or None when not set."""
    return os.environ.get(name)


def environ() -> dict:
    return os.environ


def db_alias() -> str:
    try:
        from django.conf import settings
        alias = getattr(settings, 'JOB_SCRAPER_DB_ALIAS', None)
        if alias:
            return alias
    except Exception:  # settings not configured
        pass
    return os.environ.get('JOB_SCRAPER_DB_ALIAS') or 'default'


def data_dir() -> str:
    """DATA_DIR: where the government files are read from (default ./data)."""
    return os.environ.get('DATA_DIR') or './data'
