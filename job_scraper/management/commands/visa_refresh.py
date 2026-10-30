"""Loads the government visa files from data/ (run when new quarterly files are out)."""
from ...core import db, log
from ...jobs.quarterly_visa_refresh import run_quarterly_visa_refresh
from ...core.sql import SQL
from ._base import ScriptCommand


class Command(ScriptCommand):
    help = ('Loads the government files in DATA_DIR (lca, perm, uscis-hub, everify) into '
            'company_visa_history and rescores every company.')
    script_name = 'visa-refresh'

    def handle(self, *args, **opts):
        results = run_quarterly_visa_refresh()

        log.info('\n=== Visa refresh complete ===')
        log.info('LCA:', results['lca'] if results['lca'] is not None else 'skipped (no file found)')
        log.info('PERM:', results['perm'] if results['perm'] is not None else 'skipped (no file found)')
        log.info('USCIS Hub:', results['uscisHub'] if results['uscisHub'] is not None else 'skipped (no file found)')
        log.info('E-Verify:', results['everify'] if results['everify'] is not None else 'skipped (no file found)')
        log.info('Rescored:', results['rescored'])

        count = db.query(SQL.visaHistory.countAll).rows[0]['count']
        log.info(f'\ncompany_visa_history now has {count} companies.')
        db.end()
