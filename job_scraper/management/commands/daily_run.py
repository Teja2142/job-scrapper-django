"""
The daily run: read settings and roles, scrape, run the visa matcher, mark old jobs inactive.

  python manage.py daily_run                   roles.txt, settings from .env
  python manage.py daily_run --check-config    print the settings and run size, scrape nothing
  python manage.py daily_run --each-board      test every board on its own (3 jobs each unless --max)

Command-line values override .env for this run only (see USAGE).
"""
from __future__ import annotations

import re
import sys

from ...core import db, log
from ...config.constants import SOURCES
from ...config.scrape_config import (COUNT_MODES, LIMIT_SCOPES, ScrapeConfigError, ScrapeConfigOverrides, board_limit,
                                     describe_scrape_config, load_scrape_config, unused_board_limits)
from ...jobs.mark_inactive import mark_stale_jobs_inactive
from ...jobs.role_scrape import run_role_scrape
from ...core.jscompat import js_ceil, js_string, jsre, locale_int, now, to_iso
from ...matcher.match_jobs_to_visa import match_jobs_to_visa
from ...core.roles import distinct_roles, read_roles_file
from ...scraper.job_scraper import PARALLEL_BOARDS, SHARED_QUEUE, run_with_limit, scrape_source, search_boards
from ._base import ScriptCommand

EACH_BOARD_DEFAULT_MAX = 3

USAGE = (
    'Usage: python manage.py daily_run [--file roles.txt|roles.json] [--max N] [--days N]\n'
    f'         [--scope {"|".join(LIMIT_SCOPES)}] [--count {"|".join(COUNT_MODES)}]\n'
    '         [--sources linkedin,indeed] [--board-limits glassdoor:30,dice:20]\n'
    '         [--no-match] [--no-inactive] [--check-config] [--each-board]\n'
    f'Known boards: {", ".join(SOURCES)}'
)


def counted_word(cfg) -> str:
    """What the limit counts, for log lines: "new" (SCRAPE_COUNT_MODE=inserted) or "counted" (all)."""
    return 'new' if cfg.countMode == 'inserted' else 'counted'


def fail(message: str):
    log.error(f'[daily-run] {message}\n\n{USAGE}')
    sys.exit(1)


def _argparse_error(message: str):
    """Wording for argparse errors."""
    m = re.match(r'unrecognized arguments: (\S+)', message)
    if m:
        fail(f'Unknown option "{m.group(1)}".')
    m = re.match(r'argument (--[\w-]+)(?:/\S+)?: expected one argument', message)
    if m:
        fail(f'Option "{m.group(1)}" needs a value.')
    fail(message[:1].upper() + message[1:] + '.')


def print_estimate(cfg, role_count: int, roles_file: str) -> None:
    def n(x):
        return locale_int(js_ceil(x))

    boards = len(cfg.sources)
    mult = cfg.maxFetchMultiplier
    counted = counted_word(cfg)
    overrides = ', '.join(f'{b} {cfg.boardLimits[b]}' for b in cfg.sources if b in cfg.boardLimits)
    # Worst case every board is asked for its full limit (per_role: up to what is still missing).
    asked_per_role = sum(
        board_limit(cfg, b) if cfg.limitScope == 'per_board' else min(board_limit(cfg, b), cfg.maxJobsPerRole)
        for b in cfg.sources)

    lines = ['', 'Run size (estimate -- nothing has been scraped yet):']
    lines.append(f'  Roles ................ {role_count} distinct role(s) in {roles_file}')
    lines.append(f'  Boards ............... {boards}')

    if cfg.limitScope == 'per_board':
        per_role_cap = asked_per_role
        lines.append(
            f'  Max per role ......... {n(per_role_cap)} {counted} job(s): {cfg.maxJobsPerRole} per board, except {overrides}'
            if overrides else
            f'  Max per role ......... {cfg.maxJobsPerRole} per board x {boards} boards = {n(per_role_cap)} {counted} job(s)')
        lines.append(f'  Max for the run ...... {n(per_role_cap)} x {role_count} role(s) = {n(per_role_cap * role_count)} {counted} job(s)')
        lines.append(f'  Board searches ....... {role_count} role(s) x {boards} boards = {n(role_count * boards)} (every board, every role)')
    else:
        per_role_cap = cfg.maxJobsPerRole
        lines.append(f'  Max per role ......... {n(per_role_cap)} {counted} job(s) in total, taken from the boards in the order above')
        lines.append(f'  Max for the run ...... {n(per_role_cap)} x {role_count} role(s) = {n(per_role_cap * role_count)} {counted} job(s)')
        lines.append(f'  Board searches ....... up to {role_count} role(s) x {boards} boards = {n(role_count * boards)} '
                     f"(boards are skipped once a role's {per_role_cap} are filled)")
    if cfg.countMode == 'inserted':
        lines.append('                         + extra searches when a board has to page deeper past jobs already saved')
    lines.append(f'  Billed-row ceiling ... {n(asked_per_role)} asked per role x {js_string(mult)} (fetch multiplier) x {role_count} role(s) = '
                 f'about {n(asked_per_role * mult * role_count)} rows, worst case')
    lines.append('                         Apify bills every row an actor returns, not only new ones: a board may return')
    lines.append(f'                         up to {js_string(mult)}x its limit ({n(cfg.maxJobsPerRole * mult)} rows for a limit of {cfg.maxJobsPerRole}).')
    lines.append('  Real numbers are usually much lower: a board that runs out of fresh jobs stops early,')
    lines.append('  and only the rows it actually returned are billed.')
    log.info('\n'.join(lines))


def board_note(r, mx: int) -> str:
    """One line saying why a board gave what it gave."""
    why = jsre(r'\s+').sub(' ', (r.emptyReason.message if r.emptyReason else None) or r.error or '')[:110]
    if r.failed:
        return f"{'BOARD ERROR' if r.emptyReason and r.emptyReason.kind == 'board-error' else 'ERROR'}: {why}"
    if r.found == 0:
        return f'nothing matched -- {why or "no reason given"}'
    if r.accepted == 0 and r.skippedStale > 0 and r.updated == 0 and r.duplicate == 0:
        return f'all older than the window{f" (posted {r.staleRange})" if r.staleRange else ""}'
    if r.accepted == 0 and r.updated > 0:
        return 'all already saved (nothing new)'
    if r.accepted < mx:
        return 'fewer than asked -- board ran out'
    return 'ok'


def status_row(role: str, board: str, r, mx: int) -> dict:
    """A table row per role x board (used by the daily summary and --each-board)."""
    return {
        'role': role, 'board': board, 'counted': r.accepted, 'limit': mx, 'fetched': r.found, 'known': r.updated,
        'dup': r.duplicate, 'stale': r.skippedStale, 'off-title': r.skippedTitleMismatch, 'non-us': r.skippedNonUs,
        'pages': r.pages, 'status': 'FAILED' if r.failed else 'ok', 'cost $': cost_cell(r), 'note': board_note(r, mx),
    }


def cost_cell(r) -> str:
    return log.Plain(f'{r.apifyCostUsd:.4f}' if r.apifyCostUsd is not None else '-')


def cost_line(results: list) -> str:
    """'Apify cost for this run: $1.8423 (112 actor runs)' over a list of board search results."""
    runs = sum(r.apifyRuns for r in results)
    known = [r.apifyCostUsd for r in results if r.apifyCostUsd is not None]
    missing = sum(r.apifyRunsWithoutCost for r in results)
    if runs == 0:
        return 'Apify cost for this run: $0.0000 (no actor runs)'
    text = f'Apify cost for this run: ${sum(known):.4f} ({runs} actor run{"" if runs == 1 else "s"})'
    if missing:
        text += f' -- Apify reported no cost for {missing} run(s), not included'
    return text


def print_summary(cfg, summary, role_count: int) -> None:
    counted = counted_word(cfg)
    log.info(f'\n=== Summary ({counted} jobs / limit) ===')

    board_totals: dict = {}
    failures: list = []
    for r in summary.results:
        parts = []
        for b in r.capBySource.keys():
            got = r.bySource.get(b) or 0
            lim = r.capBySource[b]
            t = board_totals.setdefault(b, {'got': 0, 'limit': 0, 'fetched': 0, 'cost': None})
            t['got'] += got
            t['limit'] += lim
            d = r.details.get(b)
            if d is not None:
                t['fetched'] += d.found
                if d.apifyCostUsd is not None:
                    t['cost'] = (t['cost'] or 0.0) + d.apifyCostUsd
            parts.append(f"{b}={got}/{lim}{'(FAILED)' if b in r.failedSources else ''}")
        if r.failedSources:
            failures.append(f'{r.role}: {", ".join(r.failedSources)}')
        log.info(f"  {r.role}: {r.total}/{r.cap}{'' if r.capReached else ' (limit not reached)'}")
        if parts:
            log.info(f'      {"  ".join(parts)}')

    log.info('\nPer board, all roles:')
    for b in cfg.sources:
        t = board_totals.get(b)
        if t:
            cost = f"${t['cost']:.4f}" if t['cost'] is not None else '-'
            log.info(f"  {b.ljust(15)} {(str(t['got']) + '/' + str(t['limit'])).ljust(9)} "
                     f"fetched {str(t['fetched']).ljust(6)} cost {cost}")
        else:
            log.info(f'  {b.ljust(15)} not called (role limits already filled)')
    if failures:
        log.info('\nBoard calls that FAILED (see the log above for the reason):')
        for f in failures:
            log.info(f'  {f}')
    rows = [status_row(r.role, b, r.details[b], r.capBySource.get(b) or 0)
            for r in summary.results for b in r.details.keys()]
    if rows:
        log.info('\n=== Board status ===')
        log.table(rows)

    log.info(f'\nTotal: {summary.totalJobs} {counted} job(s) for {role_count} distinct role(s)')
    log.info(cost_line([d for r in summary.results for d in r.details.values()]))
    log.info(f'\n{describe_scrape_config(cfg)}')


def each_board(cfg, roles: list, mx: int, run_matcher: bool) -> None:
    """Calls every board for every role on its own and prints one table of what each returned."""
    log.info(f'\n[each-board] {len(cfg.sources)} board(s) x {len(roles)} role(s), up to {mx} job(s) each, '
             f'{PARALLEL_BOARDS} board(s) at a time{" (shared queue)" if SHARED_QUEUE else ""}\n')
    rows: list = []
    all_results: list = []
    if SHARED_QUEUE:
        pairs = [(role, board) for role in roles for board in cfg.sources]
        results = search_boards(pairs, cfg, lambda board: mx)
        all_results += results
        for (role, board), res in zip(pairs, results):
            rows.append(status_row(role, board, res, mx))
    else:
        for role in roles:
            results = run_with_limit(cfg.sources, PARALLEL_BOARDS, lambda board, _i: scrape_source(board, role, mx, cfg))
            all_results += results
            for i, board in enumerate(cfg.sources):
                rows.append(status_row(role, board, results[i], mx))

    log.info('\n=== Board test results ===')
    log.table(rows)

    def boards_where(test):
        return ', '.join(dict.fromkeys(r['board'] for r in rows if test(r))) or 'none'

    log.info(f"Gave jobs:        {boards_where(lambda r: r['counted'] > 0)}")
    log.info(f"FAILED:           {boards_where(lambda r: r['status'] == 'FAILED')}")
    log.info(f"Nothing matched:  {boards_where(lambda r: r['status'] == 'ok' and r['fetched'] == 0)}")
    log.info(cost_line(all_results))

    if run_matcher:
        match_jobs_to_visa()


class Command(ScriptCommand):
    help = 'Daily scrape: every role in the roles file across the configured boards, then the visa matcher.'
    script_name = 'daily-run'

    def create_parser(self, prog_name, subcommand, **kwargs):
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        # Unknown or misspelled options stop the run with exit code 1.
        parser.error = _argparse_error
        return parser

    def add_arguments(self, parser):
        parser.add_argument('--file', help='roles file (default roles.txt): one role per line, or a JSON array')
        parser.add_argument('--max', help='jobs per role (SCRAPE_MAX_JOBS_PER_ROLE)')
        parser.add_argument('--days', help='posted within N days (SCRAPE_FRESHNESS_DAYS)')
        parser.add_argument('--scope', help=f'{"|".join(LIMIT_SCOPES)} (SCRAPE_LIMIT_SCOPE)')
        parser.add_argument('--count', help=f'{"|".join(COUNT_MODES)} (SCRAPE_COUNT_MODE)')
        parser.add_argument('--sources', help='boards to run, comma-separated (SCRAPE_SOURCES)')
        parser.add_argument('--board-limits', dest='board_limits', help='board:number pairs (SCRAPE_BOARD_LIMITS)')
        parser.add_argument('--no-match', dest='no_match', action='store_true', help="don't run the visa matcher")
        parser.add_argument('--no-inactive', dest='no_inactive', action='store_true', help="don't mark stale jobs inactive")
        parser.add_argument('--check-config', dest='check_config', action='store_true',
                            help='print the settings and run size; scrape nothing')
        parser.add_argument('--each-board', dest='each_board', action='store_true',
                            help='test every board on its own (3 jobs each unless --max)')

    def handle(self, *args, **opts):
        try:
            cfg = load_scrape_config(ScrapeConfigOverrides(
                max=opts.get('max'), days=opts.get('days'), scope=opts.get('scope'), count=opts.get('count'),
                sources=opts.get('sources'), boardLimits=opts.get('board_limits'),
            ))
        except ScrapeConfigError as e:
            fail(f'Bad setting: {e}')

        log.info(f'[daily-run] start {to_iso(now())}')
        log.info(describe_scrape_config(cfg))
        unused = unused_board_limits(cfg)
        if unused:
            log.warn(f'[daily-run] note: SCRAPE_BOARD_LIMITS lists board(s) that are not running: {", ".join(unused)}')

        roles_file = opts.get('file') if opts.get('file') is not None else 'roles.txt'
        log.info(f'\n[daily-run] reading roles from {roles_file}')
        res = distinct_roles(read_roles_file(roles_file))
        if len(res.roles) == 0:
            fail(f'No usable roles found in {roles_file} -- nothing was scraped.')
        if res.duplicates:
            log.info(f'[daily-run] duplicate role(s) skipped: {", ".join(chr(34) + d + chr(34) for d in res.duplicates)}')
        if res.ignored:
            log.info(f'[daily-run] {res.ignored} blank/invalid role entr(ies) ignored')

        run_matcher = not opts.get('no_match')
        if opts.get('check_config'):
            # Never scrapes, whatever other flags are given (including --each-board).
            print_estimate(cfg, len(res.roles), roles_file)
            log.info('\n[daily-run] --check-config: settings are valid; nothing was scraped.')
        elif opts.get('each_board'):
            mx = cfg.maxJobsPerRole if cfg.origin['maxJobsPerRole'] == 'cli' else EACH_BOARD_DEFAULT_MAX
            each_board(cfg, res.roles, mx, run_matcher)
        else:
            print_estimate(cfg, len(res.roles), roles_file)
            summary = run_role_scrape(res.roles, cfg, run_matcher)
            if not opts.get('no_inactive'):
                mark_stale_jobs_inactive(cfg.inactiveAfterDays)
            print_summary(cfg, summary, len(res.roles))

        log.info(f'\n[daily-run] done {to_iso(now())}')
        db.end()
