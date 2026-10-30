"""
Maps a brand name used in job postings to the company's name in the visa data:
  python manage.py add_alias "CGI Group" "CGI Technologies and Solutions Inc" "checked on myvisajobs.com"

Both names are normalized. The target must already exist in company_visa_history, so a typo
fails instead of creating an alias that never matches.
"""
import sys

from ...core import db, log
from ...matcher.normalize_company import normalize_company
from ...core.sql import SQL
from ._base import ScriptCommand


class Command(ScriptCommand):
    help = 'Adds a hand-checked brand name -> government filing name mapping (company_aliases).'
    script_name = 'add-alias'

    def add_arguments(self, parser):
        parser.add_argument('args', nargs='*')

    def handle(self, *args, **opts):
        alias_raw = args[0] if len(args) > 0 else None
        target_raw = args[1] if len(args) > 1 else None
        notes = args[2] if len(args) > 2 else None

        if not alias_raw or not target_raw:
            log.error('Usage: python manage.py add_alias "<brand name>" "<real company name>" ["notes"]')
            sys.exit(1)

        alias_norm = normalize_company(alias_raw)
        target_norm = normalize_company(target_raw)

        if not alias_norm or not target_norm:
            log.error(f'Normalization produced an empty string -- check your input. alias="{alias_norm}" target="{target_norm}"')
            sys.exit(1)

        target_check = db.query(SQL.visaHistory.findForAliasTarget, [target_norm]).rows

        if len(target_check) == 0:
            log.error(
                f'No company found in company_visa_history with normalized name "{target_norm}" '
                f'(from input "{target_raw}"). Double check the exact company name -- '
                f"try: SELECT company_name FROM company_visa_history WHERE company_name ILIKE '%{target_raw.split(' ')[0]}%';"
            )
            sys.exit(1)

        target = target_check[0]
        log.info(f'Target found: "{target["company_name"]}" (h1b_total_filings={_s(target["h1b_total_filings"])}, '
                 f'h1b_approvals={_s(target["h1b_approvals"])}, grade={_s(target["visa_grade"])})')

        res = db.query(SQL.aliases.upsert, [alias_norm, target_norm, notes or None])

        log.info(f'Alias added (id={res.rows[0]["id"]}): "{alias_norm}" -> "{target_norm}"')
        db.end()


def _s(v):
    return 'null' if v is None else v
