"""
Shows what one board returns for one role. Nothing is saved.

  python manage.py peek_board --board monster --role data [--n 20] [--days 2]

Prints the rows (title, company, location and every date / status field), how many are fresh
enough, and one full raw row. Costs about `n` rows on Apify.
"""
import sys
from datetime import datetime, timezone

from ...core import conf, db, log
from ...config.constants import SOURCES
from ...core.jscompat import (date_ms, js_floor, js_number, js_string, json_stringify, jsre, now, parse_js_date, to_iso,
                         truthy)
from ...scraper import apify_client as apify
from ...scraper.job_scraper import build_input
from ._base import ScriptCommand

_WS = jsre(r'\s+')
_DATE_KEY = jsre(r'(date|posted|modified|updated|repost|status|expired)', 'i')


def _short(v, n=38):
    return '' if v is None else _WS.sub(' ', js_string(v))[:n]


def _posted_of(r: dict):
    v = None
    for k in ('postedDate', 'publishDateISO', 'publishDate', 'date_posted', 'datePosted'):
        if r.get(k) is not None:
            v = r.get(k)
            break
    return parse_js_date(v) if truthy(v) else None


def _utc_day_ms(d: datetime) -> int:
    d = d.astimezone(timezone.utc)
    return date_ms(datetime(d.year, d.month, d.day, tzinfo=timezone.utc))


class Command(ScriptCommand):
    help = "Shows what one board's actor returns for one role (nothing is saved)."
    script_name = 'peek'

    def add_arguments(self, parser):
        parser.add_argument('--board')
        parser.add_argument('--role')
        parser.add_argument('--n')
        parser.add_argument('--days')

    def handle(self, *args, **opts):
        board = opts.get('board')
        role = opts.get('role')
        n = js_number(opts.get('n') if opts.get('n') is not None else 20)
        days_raw = opts.get('days')
        if days_raw is None:
            days_raw = conf.env('SCRAPE_FRESHNESS_DAYS')
        days = js_number(days_raw if days_raw is not None else 2)
        if not board or not role or not (n > 0) or not (days > 0):
            log.error(f'Usage: python manage.py peek_board --board dice|monster|... --role "data" [--n 20] [--days 2]\n'
                      f'Known boards: {", ".join(SOURCES)}')
            sys.exit(1)

        try:
            cfg = build_input(board, role, n, days, 0)
        except Exception as e:  # e.g. the board's actor isn't set in .env
            log.error(f'[peek] {e}')
            db.end()
            sys.exit(1)
        if not cfg:
            log.error(f'Board "{board}" is not wired to an actor.')
            sys.exit(1)

        log.info(f'[peek] {board} / "{role}": asking {cfg.actorId} for {n} row(s)\n[peek] input: {json_stringify(cfg.input)}')
        res = apify.run_actor_detailed(cfg.actorId, cfg.input)
        items, empty_reason = res.items, res.emptyReason
        if len(items) == 0:
            log.info(f"[peek] no rows. {f'{empty_reason.kind}: {empty_reason.message}' if empty_reason else ''}")
            db.end()
            return

        start_of_today = _utc_day_ms(now())
        cutoff = start_of_today - days * 86400000

        # every short scalar field whose name looks like a date / status / repost flag
        all_keys = list(dict.fromkeys(k for r in items for k in r.keys()))
        date_keys = [k for k in all_keys if _DATE_KEY.search(k)
                     and any(r.get(k) is not None and not isinstance(r.get(k), (dict, list)) for r in items)]

        rows = []
        for r in items:
            posted = _posted_of(r)
            company = r.get('company') if r.get('company') is not None else r.get('companyName')
            where = r.get('location') if r.get('location') is not None else \
                ', '.join(js_string(x) for x in (r.get('city'), r.get('stateCode')) if truthy(x))
            row = {'title': _short(r.get('title')), 'company': _short(company, 24), 'where': _short(where, 24)}
            for k in date_keys:
                row[k] = _short(r.get(k), 24)
            row['ageDays'] = js_floor((start_of_today - _utc_day_ms(posted)) / 86400000) if posted else '?'
            rows.append(row)
        log.table(rows)

        dated = [d for d in (_posted_of(r) for r in items) if d is not None]
        fresh = len([d for d in dated if date_ms(d) >= cutoff])
        log.info(f'[peek] {len(items)} row(s); {len(dated)} with a posting date; {fresh} posted within the last '
                 f'{js_string(days)} day(s), {len(dated) - fresh} older.')
        if dated:
            t = [date_ms(d) for d in dated]
            log.info(f'[peek] posting dates run from {to_iso(min(dated, key=date_ms))[:10]} to {to_iso(max(dated, key=date_ms))[:10]}')
        no_company = len([r for r in items if not truthy(r.get('company') if r.get('company') is not None else r.get('companyName'))])
        if no_company:
            log.info(f'[peek] WARNING: {no_company} row(s) have no company (the parser skips those).')

        first = {}
        for k, v in items[0].items():
            first[k] = f'{v[:160]}... ({len(v)} chars)' if isinstance(v, str) and len(v) > 160 else v
        log.info('\n[peek] first raw row:')
        log.info(json_stringify(first, indent=2))
        db.end()
