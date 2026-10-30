"""Read-only checks on the saved data."""
from ...core import db, log
from ...core.sql import SQL
from ._base import ScriptCommand


def _run(label: str, sql: str) -> None:
    log.info(f'\n--- {label} ---')
    res = db.query(sql)
    log.table(res.rows)


class Command(ScriptCommand):
    help = 'Prints data-quality checks: totals, sources, duplicates, locations, visa coverage, recent runs.'
    script_name = 'verify-data'

    def handle(self, *args, **opts):
        _run('1. Total jobs', SQL.verify.totalJobs)
        _run('2. Jobs per source', SQL.verify.jobsPerSource)
        _run('3. Duplicate rate', SQL.verify.duplicateRate)
        _run('4. Location parsing quality', SQL.verify.locationQuality)
        _run('5. Visa match coverage', SQL.verify.visaMatchCoverage)
        _run('6. Visa grade distribution', SQL.verify.visaGradeDistribution)
        _run('7. Company visa history size', SQL.verify.visaHistorySize)
        _run('8. Sample: "H1B-friendly data engineer jobs in CA"', SQL.verify.sampleFriendlyJobs)

        log.info('\n--- 9. Query performance check (should be < 500ms) ---')
        perf = db.query(SQL.verify.explainSearchPerformance)
        for r in perf.rows:
            log.info(r['QUERY PLAN'])

        _run('10. Recent scraping log', SQL.verify.recentScrapingLog)
        db.end()
