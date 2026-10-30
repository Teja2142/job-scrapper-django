"""
All scrape settings. They come from .env (see env.example) and can be overridden on the daily_run
command line. A bad value raises ScrapeConfigError.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, TypeVar

from ..core import conf
from ..core.jscompat import is_finite, js_number, js_or, js_parse_int, js_string, js_trim
from .constants import SOURCES

LIMIT_SCOPES = ('per_board', 'per_role')
COUNT_MODES = ('inserted', 'all')

DEFAULTS = {
    'maxJobsPerRole': 50,
    'limitScope': 'per_role',
    'countMode': 'inserted',
    'freshnessDays': 2,
    'maxFetchMultiplier': 4,
    'keepUndated': True,
    'inactiveAfterDays': 45,
}

# Where a value came from, shown in the run log:
# 'cli' | 'env' | 'env (old name SCRAPE_MAX_JOBS_PER_QUERY)' | 'default'


@dataclass
class ScrapeConfig:
    maxJobsPerRole: int
    limitScope: str
    countMode: str
    # Per-board overrides of maxJobsPerRole (only boards listed in SCRAPE_BOARD_LIMITS).
    boardLimits: dict
    # Boards to run, in priority order.
    sources: list
    freshnessDays: float
    maxFetchMultiplier: float
    keepUndated: bool
    inactiveAfterDays: int
    origin: dict = field(default_factory=dict)


@dataclass
class ScrapeConfigOverrides:
    """Raw text values given on the command line (None = not given)."""
    max: str | None = None
    scope: str | None = None
    count: str | None = None
    boardLimits: str | None = None
    sources: str | None = None
    days: str | None = None


class ScrapeConfigError(Exception):
    pass


def _blank(v: str | None) -> bool:
    return v is None or js_trim(v) == ''


def positive_int(name: str, raw: str) -> int:
    n = js_number(js_trim(raw))
    if not (isinstance(n, int) and n >= 1):
        raise ScrapeConfigError(f'{name}="{raw}" is not valid -- use a whole number of 1 or more.')
    return n


def positive_number(name: str, raw: str):
    n = js_number(js_trim(raw))
    if not is_finite(n) or n <= 0:
        raise ScrapeConfigError(f'{name}="{raw}" is not valid -- use a number greater than 0.')
    return n


def one_of(name: str, raw: str, allowed) -> str:
    v = js_trim(raw).lower()
    if v not in allowed:
        raise ScrapeConfigError(f'{name}="{raw}" is not valid -- use one of: {", ".join(allowed)}.')
    return v


def known_board(name: str, raw: str) -> str:
    b = js_trim(raw).lower()
    if b not in SOURCES:
        raise ScrapeConfigError(f'{name}: unknown board "{js_trim(raw)}" -- known boards: {", ".join(SOURCES)}.')
    return b


def parse_sources(name: str, raw: str) -> list:
    """"linkedin, indeed,dice" -> ['linkedin','indeed','dice'] (validated, repeats removed, order kept)."""
    out: list = []
    for part in raw.split(','):
        if js_trim(part) == '':
            continue
        b = known_board(name, part)
        if b not in out:
            out.append(b)
    if len(out) == 0:
        raise ScrapeConfigError(f'{name}="{raw}" names no boards.')
    return out


def parse_board_limits(name: str, raw: str) -> dict:
    """"glassdoor:30, dice:20" -> { glassdoor: 30, dice: 20 } (validated)."""
    out: dict = {}
    for part in raw.split(','):
        if js_trim(part) == '':
            continue
        m = part.split(':')
        if len(m) != 2 or js_trim(m[0]) == '' or js_trim(m[1]) == '':
            raise ScrapeConfigError(f'{name}: "{js_trim(part)}" is not valid -- write board:number, e.g. glassdoor:30.')
        board = known_board(name, m[0])
        if board in out:
            raise ScrapeConfigError(f'{name}: board "{board}" is listed twice.')
        out[board] = positive_int(f'{name} ({board})', m[1])
    return out


T = TypeVar('T')


def load_scrape_config(cli: ScrapeConfigOverrides | None = None, env: Mapping | None = None) -> ScrapeConfig:
    """Reads and validates every setting: command line, then .env, then the default."""
    cli = cli or ScrapeConfigOverrides()
    env = conf.environ() if env is None else env
    origin: dict = {}

    # Jobs per role: the old name SCRAPE_MAX_JOBS_PER_QUERY is still read as a fallback.
    max_jobs = DEFAULTS['maxJobsPerRole']
    origin['maxJobsPerRole'] = 'default'
    if not _blank(cli.max):
        max_jobs = positive_int('--max', cli.max)
        origin['maxJobsPerRole'] = 'cli'
    elif not _blank(env.get('SCRAPE_MAX_JOBS_PER_ROLE')):
        max_jobs = positive_int('SCRAPE_MAX_JOBS_PER_ROLE', env.get('SCRAPE_MAX_JOBS_PER_ROLE'))
        origin['maxJobsPerRole'] = 'env'
    elif not _blank(env.get('SCRAPE_MAX_JOBS_PER_QUERY')):
        max_jobs = positive_int('SCRAPE_MAX_JOBS_PER_QUERY', env.get('SCRAPE_MAX_JOBS_PER_QUERY'))
        origin['maxJobsPerRole'] = 'env (old name SCRAPE_MAX_JOBS_PER_QUERY)'

    def pick(key: str, cli_raw: str | None, cli_name: str, env_name: str,
             parse: Callable[[str, str], T], default: T) -> T:
        if not _blank(cli_raw):
            origin[key] = 'cli'
            return parse(cli_name, cli_raw)
        e = env.get(env_name)
        if not _blank(e):
            origin[key] = 'env'
            return parse(env_name, e)
        origin[key] = 'default'
        return default

    limit_scope = pick('limitScope', cli.scope, '--scope', 'SCRAPE_LIMIT_SCOPE',
                       lambda n, r: one_of(n, r, LIMIT_SCOPES), DEFAULTS['limitScope'])
    count_mode = pick('countMode', cli.count, '--count', 'SCRAPE_COUNT_MODE',
                      lambda n, r: one_of(n, r, COUNT_MODES), DEFAULTS['countMode'])
    sources = pick('sources', cli.sources, '--sources', 'SCRAPE_SOURCES', parse_sources, list(SOURCES))
    board_limits = pick('boardLimits', cli.boardLimits, '--board-limits', 'SCRAPE_BOARD_LIMITS',
                        parse_board_limits, {})
    freshness_days = pick('freshnessDays', cli.days, '--days', 'SCRAPE_FRESHNESS_DAYS',
                          positive_number, DEFAULTS['freshnessDays'])
    max_fetch_multiplier = pick('maxFetchMultiplier', None, '', 'SCRAPE_MAX_FETCH_MULTIPLIER',
                                positive_number, DEFAULTS['maxFetchMultiplier'])
    keep_undated = pick('keepUndated', None, '', 'SCRAPE_KEEP_UNDATED',
                        lambda n, r: one_of(n, r, ('true', 'false')) == 'true', DEFAULTS['keepUndated'])
    inactive_after_days = pick('inactiveAfterDays', None, '', 'SCRAPE_INACTIVE_AFTER_DAYS',
                               positive_int, DEFAULTS['inactiveAfterDays'])

    # Printed in the run log in this order.
    ordered_origin = {k: origin[k] for k in (
        'maxJobsPerRole', 'limitScope', 'countMode', 'sources', 'boardLimits',
        'freshnessDays', 'maxFetchMultiplier', 'keepUndated', 'inactiveAfterDays')}
    return ScrapeConfig(
        maxJobsPerRole=max_jobs, limitScope=limit_scope, countMode=count_mode, boardLimits=board_limits,
        sources=sources, freshnessDays=freshness_days, maxFetchMultiplier=max_fetch_multiplier,
        keepUndated=keep_undated, inactiveAfterDays=inactive_after_days, origin=ordered_origin,
    )


def board_limit(cfg, board: str) -> int:
    """The limit a given board gets for one role: its SCRAPE_BOARD_LIMITS override, else the main limit."""
    v = cfg.boardLimits.get(board)
    return cfg.maxJobsPerRole if v is None else v


def unused_board_limits(cfg: ScrapeConfig) -> list:
    """Boards listed in SCRAPE_BOARD_LIMITS that are not running (a warning, not an error)."""
    return [b for b in cfg.boardLimits.keys() if b not in cfg.sources]


def parallel_boards() -> int:
    """SCRAPE_PARALLEL_BOARDS: how many board searches run at the same time (default 3)."""
    return max(1, js_or(js_parse_int(js_or(conf.env('SCRAPE_PARALLEL_BOARDS'), '3')), 3))


def shared_queue() -> bool:
    """
    SCRAPE_SHARED_QUEUE=true: in per_board scope all searches of all roles go into one queue, so the
    next role doesn't wait for the slowest board of the previous one.
    """
    return js_trim(conf.env('SCRAPE_SHARED_QUEUE') or '').lower() == 'true'


def describe_queue(cfg: ScrapeConfig) -> str:
    n = parallel_boards()
    if cfg.limitScope != 'per_board':
        return 'one board at a time (per_role fills boards in order)'
    if shared_queue():
        return f'{n} at a time, shared across all roles (SCRAPE_SHARED_QUEUE=true)'
    return f'{n} at a time, one role after another'


def describe_scrape_config(cfg: ScrapeConfig) -> str:
    """The active settings, printed at the start and end of every run."""
    o = cfg.origin

    def tag(k):
        return f'  [{o[k]}]'

    limits = (', '.join(f'{b}:{n}' for b, n in cfg.boardLimits.items())
              if cfg.boardLimits else '(none -- every board uses the main limit)')
    scope_text = (f'per_board  ({cfg.maxJobsPerRole} from EACH board for every role)'
                  if cfg.limitScope == 'per_board'
                  else f'per_role   ({cfg.maxJobsPerRole} in total per role, filled from boards in the order below)')
    count_text = ('inserted   (only brand-new jobs count toward the limit)'
                  if cfg.countMode == 'inserted'
                  else 'all        (new, already-saved and cross-board duplicate jobs all count)')
    fd = js_string(cfg.freshnessDays)
    return '\n'.join([
        'Active scrape settings:',
        f'  jobs per role ........ {cfg.maxJobsPerRole}{tag("maxJobsPerRole")}',
        f'  limit scope .......... {scope_text}{tag("limitScope")}',
        f'  count mode ........... {count_text}{tag("countMode")}',
        f'  board limits ......... {limits}{tag("boardLimits")}',
        f'  boards ({len(cfg.sources)}) ......... {", ".join(cfg.sources)}{tag("sources")}',
        f'  freshness ............ posted within {fd} day(s)'
        + (' (24 hours: boards asked for their last-24-hours filter)' if cfg.freshnessDays <= 1 else '')
        + tag('freshnessDays'),
        f'  fetch multiplier ..... {js_string(cfg.maxFetchMultiplier)}x the board\'s limit (raw-row cost ceiling){tag("maxFetchMultiplier")}',
        f'  keep undated jobs .... {"true" if cfg.keepUndated else "false"}{tag("keepUndated")}',
        f'  inactive after ....... {cfg.inactiveAfterDays} day(s) unseen{tag("inactiveAfterDays")}',
        f'  board searches ....... {describe_queue(cfg)}',
    ])
