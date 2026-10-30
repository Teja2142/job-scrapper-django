"""
Runs the visa matcher on jobs already saved, without scraping. Only unmatched jobs by default;
--rematch-all matches every job again (use it after loading new visa data).
"""
from ...core import db, log
from ...core.jscompat import js_number, to_fixed
from ...matcher.match_jobs_to_visa import match_jobs_to_visa
from ...core.sql import SQL
from ._base import ScriptCommand


class Command(ScriptCommand):
    help = 'Re-runs the visa matcher on the jobs already saved (no scraping).'
    script_name = 'run-match'

    def add_arguments(self, parser):
        parser.add_argument('--rematch-all', dest='rematch_all', action='store_true',
                            help='re-match every job, not only unmatched ones')

    def handle(self, *args, **opts):
        rematch_all = bool(opts.get('rematch_all'))
        log.info(f"=== Re-running matcher against existing us_jobs{' (--rematch-all)' if rematch_all else ''} ===\n")

        match_jobs_to_visa(rematch_all=rematch_all)

        total = js_number(db.query(SQL.jobs.countAll).rows[0]['count'])
        matched = js_number(db.query(SQL.jobs.countMatched).rows[0]['count'])

        log.info('\n=== Summary ===')
        log.info(f'Total jobs: {total}')
        log.info(f"Matched: {matched} ({to_fixed((matched / total) * 100, 1) if total > 0 else 0}%)")
        db.end()
