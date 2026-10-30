"""Shared bits of the job_scraper management commands."""
from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

from ...core import db, log


class ScriptCommand(BaseCommand):
    """
    Base for the commands: an error is printed as "[<command>] failed: <message>" and the
    command exits with code 1.
    """
    script_name = 'script'
    requires_system_checks = []

    def execute(self, *args, **options):
        try:
            return super().execute(*args, **options)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            log.error(f'[{self.script_name}] failed:', str(e))
            try:
                db.end()
            except Exception:  # noqa: BLE001
                pass
            sys.exit(1)
