"""
Creates the tables by running schema.sql, and adds any new columns to existing ones.
Safe to run again: nothing is deleted or overwritten.
"""
import os

from ...core import db, log
from ._base import ScriptCommand


class Command(ScriptCommand):
    help = 'Creates / verifies the job_scraper tables (runs schema.sql).'
    script_name = 'setup-db'

    def handle(self, *args, **opts):
        schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'schema.sql')
        with open(schema_path, encoding='utf-8') as f:
            sql = f.read()

        log.info('[setup-db] running schema.sql...')
        db.execute_script(sql)
        log.info('[setup-db] done. Tables created/verified.')
        db.end()
