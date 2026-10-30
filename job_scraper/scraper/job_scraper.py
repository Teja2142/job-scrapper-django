"""Searches the boards (through their Apify actors) and saves the jobs to us_jobs."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from ..core import conf, db, log
from ..config.constants import DEDICATED_SITES, SUPPORTED_SITES
from ..config.scrape_config import board_limit, parallel_boards, shared_queue
from ..core.jscompat import (date_ms, js_ceil, js_floor, js_number, js_or, js_parse_int, js_round, js_string, js_trim,
                        jsre, now, now_ms, to_iso, truthy)
from ..core.sql import SQL
from . import apify_client as apify
from .apify_client import EmptyReason
from .job_parser import NormalizedJob, parse_job
from .location_parser import state_code_from_text
from .parser_utils import actor_title_keywords, title_matches_role

# Actor ids come from .env only. A missing one stops just that board, not the whole run.
ACTOR_ENV = {
    'multi': 'APIFY_MULTI_JOBBOARD_ACTOR',  # linkedin, indeed, glassdoor, muse, remote boards...
    'dice': 'APIFY_DICE_ACTOR',
    'monster': 'APIFY_MONSTER_ACTOR',
    'careerbuilder': 'APIFY_CAREERBUILDER_ACTOR',
    'ziprecruiter': 'APIFY_ZIPRECRUITER_ACTOR',
    'jobright': 'APIFY_JOBRIGHT_ACTOR',
}


class ActorNotSetError(Exception):
    """Raised when a board's actor isn't set in .env."""

    def __init__(self, board: str, env_name: str):
        self.board = board
        self.envName = env_name
        super().__init__(
            f'No Apify actor set for {board}. Please add {env_name}=<actor id> to your .env file '
            f'(e.g. {env_name}=owner/actor-name -- see .env.example).'
        )


_warned_actor_not_set: set = set()
_warned_lock = threading.Lock()


def actor_for(board: str, env_name: str) -> str:
    """The actor id from .env for this board; raises ActorNotSetError if it's missing or blank."""
    actor = js_trim(conf.env(env_name) or '')
    if not actor:
        raise ActorNotSetError(board, env_name)
    return actor


def env_flag(name: str, default_value: bool) -> bool:
    """true/false setting from .env; anything but "false" counts as true when the default is true."""
    v = js_trim(conf.env(name) or '').lower()
    if v == '':
        return default_value
    return v != 'false' if default_value else v == 'true'


_monster_location = conf.env('MONSTER_LOCATION')
MONSTER_LOCATION = '' if _monster_location is None else _monster_location  # empty = all of the US

# The Muse only understands "City, ST" (it rejects "United States"), so it is searched in a
# list of cities in one actor run. Every city also returns The Muse's worldwide remote pool;
# the actor dedupes those across cities.
MUSE_LOCATIONS = [js_trim(s) for s in js_or(conf.env('MUSE_LOCATIONS'), 'New York, NY;San Francisco, CA;Austin, TX').split(';')]
MUSE_LOCATIONS = [s for s in MUSE_LOCATIONS if s]
# How many Muse jobs the actor looks at per city. The title filter runs after this and drops
# most of them, so this is not the number of rows you get back.
MUSE_SCAN_PER_CITY = max(1, js_or(js_parse_int(js_or(conf.env('MUSE_SCAN_PER_CITY'), '20')), 20))
# Drop Muse jobs with no US city in their location (remote-only jobs are often outside the US).
MUSE_US_ONLY = env_flag('MUSE_US_ONLY', True)
# ZipRecruiter: search the role as an exact phrase ("embedded software engineer" in quotes).
# Without quotes most of the (billed) rows were unrelated jobs.
ZIPRECRUITER_EXACT_PHRASE = env_flag('ZIPRECRUITER_EXACT_PHRASE', True)
# ZipRecruiter also lists some foreign jobs (Toronto, ON): keep US locations only.
ZIPRECRUITER_US_ONLY = env_flag('ZIPRECRUITER_US_ONLY', True)

# Multi-board actor (LinkedIn, Indeed, Glassdoor, Muse, ...): ask the actor to keep only jobs whose
# title contains the role's distinctive word(s), so off-title rows are dropped before they are billed.
MULTI_REQUIRE_TITLE = env_flag('MULTI_REQUIRE_TITLE', True)
# With the title filter on, the actor looks at this many times the jobs still needed per call.
# Rows it filters out are not billed (actor log: "Nothing was billed"), so looking further ahead
# costs nothing extra and saves actor runs. Capped by the fetch budget like any page.
MULTI_SCAN_FACTOR = max(1, js_or(js_number(js_or(conf.env('MULTI_SCAN_FACTOR'), '3')), 3))

# A board that returns nothing because it was blocked or broke is run once more (a new run usually
# gets a new proxy exit). 0 = no retry.
_retry_raw = conf.env('SCRAPE_RETRY_FAILED')
RETRY_FAILED = max(0, js_or(js_parse_int('1' if _retry_raw is None else _retry_raw), 0))

# Boards searched with one call per role. Muse already covers several cities in one call.
# ZipRecruiter and CareerBuilder can't start from row N, so a second call would read (and bill)
# the first rows again. One bigger call is cheaper.
SINGLE_CALL_SOURCES = {'muse', 'ziprecruiter', 'careerbuilder'}

# Boards that are single-call but ask only for the role's limit (not the whole fetch budget).
LIMIT_ONLY_SOURCES = {'careerbuilder'}

# Boards whose rows are checked for a US location (setting per board).
US_ONLY_SOURCES = set((['muse'] if MUSE_US_ONLY else []) + (['ziprecruiter'] if ZIPRECRUITER_US_ONLY else []))

# Boards whose search matches loosely, so the title is checked against the role too
# (title_matches_role: its distinctive words, or every word with TITLE_MATCH_MODE=all).
# The multi-board sites are included because the actor's own title filter matches ANY keyword.
# Dice and Monster search titles closely enough on their own.
TITLE_CHECK_SOURCES = {'careerbuilder', 'ziprecruiter', 'jobright', *SUPPORTED_SITES}

_US_WORDS = jsre(r'\b(united states|usa)\b', 'i')
_US_SUFFIX = jsre(r',\s*US$', 'i')
_LAST_PART = jsre(r',\s*([^,]+)$')


def has_us_location(raw) -> bool:
    """True if at least one ";"-separated part of the location is "City, ST" in the US (or names the US)."""
    if not truthy(raw):
        return False
    if not isinstance(raw, str):
        raise TypeError('raw.split is not a function')
    for part in raw.split(';'):
        p = js_trim(part)
        if _US_WORDS.search(p):
            return True
        if _US_SUFFIX.search(p):  # "Remote, US"
            return True
        m = _LAST_PART.search(p)
        if m and state_code_from_text(m.group(1)) is not None:
            return True
    return False


@dataclass
class ActorCall:
    actorId: str
    input: dict
    # Rows asked for, counted from the top of the board's results.
    requested: int
    # Rows at the top already seen on an earlier page (actors with no `offset`: Dice, Monster, ...).
    skipFirst: int


def actor_supports_offset(source: str) -> bool:
    """Only the multi-board actor can skip rows itself (`offset`)."""
    return source in SUPPORTED_SITES


def dice_posted_within(freshness_days):
    if freshness_days <= 1:
        return 'ONE'
    if freshness_days <= 3:
        return 'THREE'
    if freshness_days <= 7:
        return 'SEVEN'
    return None


# The actor's options are 24h, 3d, 7d, 14d, 30d. '3d' has been checked live; '24h' is its
# 24-hour option (SCRAPE_FRESHNESS_DAYS=1).
def jobright_date_posted(freshness_days) -> str:
    if freshness_days <= 1:
        return '24h'
    if freshness_days <= 3:
        return '3d'
    if freshness_days <= 7:
        return '7d'
    return '30d'


_SLUG_NON = jsre(r'[^a-z0-9]+')
_SLUG_EDGES = jsre(r'^-+|-+$')
_HAS_WS = jsre(r'\s')


def careerbuilder_start_url(query: str, freshness_days) -> str:
    """
    CareerBuilder is searched through a search-page URL, because the actor's keyword and date
    fields don't filter properly. Use "today" for 1 day ("last+1+days" breaks the page).
    Don't add "et=": it limits the search to one job type.
    """
    slug = _SLUG_EDGES.sub('', _SLUG_NON.sub('-', js_trim(query).lower()))
    recency = 'today' if freshness_days <= 1 else f'last+{js_round(freshness_days)}+days'
    return f'https://www.careerbuilder.com/jobs-{slug}?recency={recency}'


def build_input(source: str, query: str, page_size: int, freshness_days, offset: int = 0) -> ActorCall | None:
    """The actor call for one page of one board's search. None if the board has no actor."""
    known = source in SUPPORTED_SITES or source in DEDICATED_SITES
    if not known:
        return None

    if source == 'muse':
        # maxResults is how many jobs the actor looks at per city BEFORE requireTitleKeywords drops
        # off-title ones (only delivered rows are pushed to the dataset). With the title filter off it
        # is also the delivered count, so then the row budget is split across the cities instead.
        per_city = MUSE_SCAN_PER_CITY if MULTI_REQUIRE_TITLE else max(1, js_floor(page_size / len(MUSE_LOCATIONS)))
        inp = {'searchTerm': query}
        if MUSE_LOCATIONS:  # (JSON leaves out an undefined value)
            inp['location'] = MUSE_LOCATIONS[0]
        inp['locations'] = MUSE_LOCATIONS
        inp['sites'] = [source]
        # With the actor's default proxy The Muse answers HTTP 403.
        inp['proxyConfiguration'] = {'useApifyProxy': False}
        if MULTI_REQUIRE_TITLE:
            inp['requireTitleKeywords'] = actor_title_keywords(query)
        inp['maxResults'] = per_city
        inp['hoursOld'] = max(1, js_round(freshness_days * 24))
        inp['maxAgeDays'] = freshness_days
        return ActorCall(actorId=actor_for(source, ACTOR_ENV['multi']), requested=per_city * len(MUSE_LOCATIONS),
                         skipFirst=0, input=inp)

    if actor_supports_offset(source):
        inp = {
            'searchTerm': query,
            'location': 'United States',
            'sites': [source],
        }
        if MULTI_REQUIRE_TITLE:
            inp['requireTitleKeywords'] = actor_title_keywords(query)
        inp['maxResults'] = page_size
        inp['hoursOld'] = max(1, js_round(freshness_days * 24))  # filtered by the board, before the limit
        inp['maxAgeDays'] = freshness_days                       # filtered by the actor, for boards that ignore hoursOld
        if offset > 0:
            inp['offset'] = offset
        return ActorCall(actorId=actor_for(source, ACTOR_ENV['multi']), requested=page_size, skipFirst=0, input=inp)

    # No `offset`: ask again from the top and skip what was already seen.
    requested = offset + page_size

    if source == 'dice':
        within = dice_posted_within(freshness_days)
        inp = {
            'searchQueries': [query],
            'maxResultsPerQuery': min(requested, 500),
        }
        if within:
            inp['postedWithinDays'] = within
        inp['fetchDescription'] = True
        return ActorCall(actorId=actor_for(source, ACTOR_ENV['dice']), requested=requested, skipFirst=offset, input=inp)

    if source == 'careerbuilder':
        start_url = careerbuilder_start_url(query, freshness_days)
        # url mode keeps our date filter; search mode ignores it and returns jobs weeks old.
        # Needs a residential US proxy.
        return ActorCall(
            actorId=actor_for(source, ACTOR_ENV['careerbuilder']),
            requested=requested,
            skipFirst=offset,
            input={
                'mode': 'url',
                'startUrls': [{'url': start_url}],
                'fetchDetails': True,  # full description (not billed extra at the time of testing)
                'maxItems': requested,
                'proxyConfiguration': {
                    'useApifyProxy': True,
                    'apifyProxyGroups': ['RESIDENTIAL'],
                    'apifyProxyCountry': 'US',
                },
            },
        )

    if source == 'ziprecruiter':
        return ActorCall(
            actorId=actor_for(source, ACTOR_ENV['ziprecruiter']),
            requested=requested,
            skipFirst=offset,
            input={
                'searchJobKeyword': f'"{js_trim(query)}"' if ZIPRECRUITER_EXACT_PHRASE and _HAS_WS.search(js_trim(query)) else query,
                'searchPostedWithinDays': max(1, js_round(freshness_days)),
                'searchRemoteOnly': False,
                'searchEasyApply': False,
                'searchEmploymentType': 'all',
                'searchStartPage': 1,
                'maxItems': requested,
                'externalApplyOnly': False,
                'enrichEmails': False,
                # ZipRecruiter blocks datacenter IPs.
                'proxy': {'useApifyProxy': True, 'apifyProxyGroups': ['RESIDENTIAL'], 'apifyProxyCountry': 'US'},
            },
        )

    if source == 'jobright':
        return ActorCall(
            actorId=actor_for(source, ACTOR_ENV['jobright']),
            requested=requested,
            skipFirst=offset,
            input={
                'keyword': query,
                'location': '',
                'country': 'US',
                'datePosted': jobright_date_posted(freshness_days),
                'sortBy': 'date',
                'remoteOnly': False,
                'jobType': '',
                'experienceLevel': '',
                'includeCompanyDetails': True,
                'includeSalary': True,
                'maxItems': requested,
                'proxyEnabled': False,  # with a proxy JobRight's Cloudflare blocks every run
            },
        )

    # monster
    return ActorCall(
        actorId=actor_for(source, ACTOR_ENV['monster']),
        requested=requested,
        skipFirst=offset,
        input={
            'searchQuery': query,
            'location': MONSTER_LOCATION,
            'daysOld': max(1, js_ceil(freshness_days)),  # "posted OR updated" -- is_fresh() drops old reposts
            'fetchDetails': True,   # without it company/location/description are null
            'skipReposts': False,   # the actor's own memory would hide jobs we have; we dedupe in the DB
            'maxItems': requested,
        },
    )


def is_fresh(posting_date, freshness_days, keep_undated: bool) -> bool:
    """
    Our own date check on top of each board's filter. Most boards only give a day, so whole
    UTC days are compared: freshness_days = 1 keeps today and yesterday.
    """
    if not posting_date:
        return keep_undated
    n = now()
    start_of_today_utc = date_ms(n.replace(hour=0, minute=0, second=0, microsecond=0))
    return date_ms(posting_date) >= start_of_today_utc - freshness_days * 86400000


# StopReason: 'cap-reached' | 'no-more-jobs' | 'fetch-limit' | 'error' | 'unsupported'


@dataclass
class ScrapeSourceResult:
    source: str
    # Raw rows the actor delivered (= billed), over all pages.
    found: int = 0
    # Jobs that count toward the limit: new ones ('inserted'), or new + saved + duplicate ('all').
    accepted: int = 0
    skippedStale: int = 0
    # Posting dates of the stale rows, e.g. "2026-08-14 to 2026-09-12".
    staleRange: str | None = None
    skippedTitleMismatch: int = 0
    # Muse: jobs with no US city (e.g. remote-only in London or Poland).
    skippedNonUs: int = 0
    inserted: int = 0
    # Already in the database (refreshed).
    updated: int = 0
    # Same job as one from another board/listing.
    duplicate: int = 0
    pages: int = 0
    stopReason: str = 'cap-reached'
    failed: bool = False
    error: str | None = None
    emptyReason: EmptyReason | None = None
    # Apify runs this search used (pages + retries), the last one's id, and their total cost.
    apifyRuns: int = 0
    apifyRunId: str | None = None
    apifyCostUsd: float | None = None
    # Runs Apify reported no cost for (the total above then leaves them out).
    apifyRunsWithoutCost: int = 0


def _record_run(result: ScrapeSourceResult, run) -> None:
    result.apifyRuns += 1
    result.apifyRunId = run.runId
    cost = getattr(run, 'costUsd', None)
    if cost is None:
        result.apifyRunsWithoutCost += 1
    else:
        result.apifyCostUsd = (result.apifyCostUsd or 0.0) + cost


def _error_message(e: BaseException) -> str:
    return str(e)


def scrape_source(source: str, query: str, max_results: int, settings) -> ScrapeSourceResult:
    """
    Searches one board for one role until `max_results` jobs count, the board runs out, or the
    fetch budget (maxFetchMultiplier x max_results rows) is used up. In 'inserted' mode jobs we
    already have don't count, so it keeps paging.
    """
    freshness_days = settings.freshnessDays
    count_mode = settings.countMode
    keep_undated = settings.keepUndated
    fetch_budget = js_ceil(max_results * settings.maxFetchMultiplier)
    min_page = min(10, max_results)

    started = now()
    result = ScrapeSourceResult(source=source)

    try:
        offset = 0  # raw rows already looked at
        stale_oldest = None
        stale_newest = None

        while result.accepted < max_results:
            budget_left = fetch_budget - offset
            if budget_left <= 0:
                result.stopReason = 'fetch-limit'
                break

            # First page asks for exactly what is needed. Boards without `offset` re-read from the top
            # (and re-bill), so their second call takes the whole remaining budget at once.
            need = max_results - result.accepted
            # Single-call boards spend the whole row budget in one run (split across its cities).
            # Multi-board sites with the title filter look ahead MULTI_SCAN_FACTOR x what is needed:
            # `offset` and maxResults count jobs looked at, and only matching ones are delivered/billed.
            if source in LIMIT_ONLY_SOURCES:
                page_size = min(max_results, budget_left)
            elif source in SINGLE_CALL_SOURCES:
                page_size = budget_left
            elif actor_supports_offset(source):
                page_size = min(
                    max(need * MULTI_SCAN_FACTOR, min_page) if MULTI_REQUIRE_TITLE
                    else (need if result.pages == 0 else max(need, min_page)),
                    budget_left)
            else:
                page_size = min(need, budget_left) if result.pages == 0 else budget_left

            call = build_input(source, query, page_size, freshness_days, offset)
            if not call:
                result.stopReason = 'unsupported'
                return result

            result.pages += 1
            delivered: list = []
            empty_reason = None
            attempt = 0
            while True:
                run = apify.run_actor_detailed(call.actorId, call.input)
                _record_run(result, run)
                delivered = run.items
                empty_reason = run.emptyReason
                # Rows that are not jobs (CareerBuilder delivers one placeholder row when the search page
                # failed to load): treat the run as empty and read the log for the reason.
                if len(delivered) > 0 and all(not parse_job(raw, source) for raw in delivered):
                    empty_reason = apify.explain_run(run.runId)
                    log.warn(f'[{source}] "{query}": {len(delivered)} row(s) delivered but none is a job -- {empty_reason.message}')
                    delivered = []
                blocked = len(delivered) == 0 and empty_reason is not None and empty_reason.kind == 'board-error'
                if not blocked or attempt >= RETRY_FAILED:
                    break
                log.warn(f'[{source}] "{query}": {empty_reason.message} -- retrying ({attempt + 1}/{RETRY_FAILED})')
                time.sleep(5)
                attempt += 1
            raw_items = delivered[call.skipFirst:] if call.skipFirst > 0 else delivered
            result.found += len(delivered)
            counted_before = result.accepted

            if len(delivered) == 0 and empty_reason:
                result.emptyReason = empty_reason
                if empty_reason.kind == 'board-error' and result.found == 0:
                    result.failed = True
                    result.error = empty_reason.message
                    result.stopReason = 'error'
                    log.warn(f'[{source}] "{query}": the board reported a problem: {empty_reason.message}')
                    break

            for raw in raw_items:
                if result.accepted >= max_results:
                    break

                job = parse_job(raw, source)
                if not job:
                    continue

                if source in TITLE_CHECK_SOURCES and not title_matches_role(job.title, query):
                    result.skippedTitleMismatch += 1
                    continue

                if source in US_ONLY_SOURCES and not has_us_location(job.locationRaw):
                    result.skippedNonUs += 1
                    continue

                if not is_fresh(job.postingDate, freshness_days, keep_undated):
                    result.skippedStale += 1
                    if job.postingDate:
                        if not stale_oldest or job.postingDate < stale_oldest:
                            stale_oldest = job.postingDate
                        if not stale_newest or job.postingDate > stale_newest:
                            stale_newest = job.postingDate
                        result.staleRange = f'{to_iso(stale_oldest)[:10]} to {to_iso(stale_newest)[:10]}'
                    continue

                try:
                    outcome = upsert_job(job)
                    if outcome == 'inserted':
                        result.inserted += 1
                        result.accepted += 1
                    elif outcome == 'updated':
                        result.updated += 1
                        if count_mode == 'all':
                            result.accepted += 1
                    else:
                        result.duplicate += 1
                        if count_mode == 'all':
                            result.accepted += 1
                except Exception as job_err:
                    log.error(f'[scrapeSource] failed to save job {job.jobId}:', _error_message(job_err))

            log.info(
                f'[{source}] "{query}" page {result.pages} (skipping {offset}): '
                f'fetched={len(delivered)} {"counted" if count_mode == "all" else "new"}={result.accepted - counted_before}'
            )

            if len(raw_items) == 0:
                # The title filter removed every job on this page, but the board may have matches deeper
                # down: move on to the next page (the fetch budget still caps how far).
                if (empty_reason is not None and empty_reason.kind == 'filtered'
                        and actor_supports_offset(source) and source not in SINGLE_CALL_SOURCES):
                    offset += page_size
                    continue
                result.stopReason = 'no-more-jobs'
                break
            if source in SINGLE_CALL_SOURCES:
                if result.accepted < max_results:
                    result.stopReason = 'no-more-jobs'
                break
            # The board had fewer rows than asked for: nothing deeper to page into.
            if not actor_supports_offset(source) and len(delivered) < call.requested and result.accepted < max_results:
                result.stopReason = 'no-more-jobs'
                break
            offset += page_size
    except Exception as e:
        result.failed = True
        result.stopReason = 'error'
        result.error = _error_message(e)
        if isinstance(e, ActorNotSetError):
            # Say it once per board, not once per role.
            with _warned_lock:
                first_time = e.envName not in _warned_actor_not_set
                _warned_actor_not_set.add(e.envName)
            if first_time:
                log.error(f'[{source}] {e} Skipping {source} for this run.')
        else:
            log.error(f'[scrapeSource] {source} / "{query}" failed:', _error_message(e))

    log_run(source, query, started, result)

    log.info(
        f'[{source}] "{query}": {"counted" if count_mode == "all" else "new"}={result.accepted}/{max_results} '
        f'pages={result.pages} fetched={result.found} '
        f'already-known={result.updated} duplicate={result.duplicate} stale={result.skippedStale}'
        f'{f" (posted {result.staleRange})" if result.staleRange else ""} '
        f'{f"title-mismatch={result.skippedTitleMismatch} " if result.skippedTitleMismatch > 0 else ""}'
        f'{f"non-us={result.skippedNonUs} " if result.skippedNonUs > 0 else ""}'
        f'(stopped: {result.stopReason})'
    )
    if result.found == 0 and result.emptyReason and result.emptyReason.kind != 'board-error':
        log.info(f'[{source}] "{query}": nothing returned -- {result.emptyReason.message}')
    return result


@dataclass
class ScrapeRoleResult:
    role: str
    # Jobs counted for this role across all boards.
    total: int = 0
    # per_role: the role's limit. per_board: every board's limit added up.
    cap: int = 0
    capReached: bool = False
    # Counted jobs per board.
    bySource: dict = field(default_factory=dict)
    # The limit each board was given (per_role: its share of what was still missing).
    capBySource: dict = field(default_factory=dict)
    failedSources: list = field(default_factory=list)
    # Each board's full result, for the status table.
    details: dict = field(default_factory=dict)


# How many board searches run at once (per_board scope only; per_role always runs in order).
# Each running actor uses some of the Apify account's memory, so don't raise this too far.
PARALLEL_BOARDS = parallel_boards()
SHARED_QUEUE = shared_queue()


def run_with_limit(items: list, limit: int, fn) -> list:
    """
    Runs fn(item, i) on threads, at most `limit` at once, and returns results in item order.
    Starts are spaced 1.5 s apart so the actors don't all start in the same second.
    """
    out: list = [None] * len(items)
    lock = threading.Lock()
    state = {'next': 0}
    errors: list = []

    def worker(k: int):
        try:
            time.sleep(k * 1.5)
            while True:
                with lock:
                    if state['next'] >= len(items):
                        return
                    i = state['next']
                    state['next'] += 1
                out[i] = fn(items[i], i)
        except BaseException as e:  # noqa: BLE001 - re-raised in the calling thread below
            with lock:
                errors.append(e)
        finally:
            db.close_thread_connections()

    threads = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(min(limit, len(items)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return out


def _fill_role_result(result: ScrapeRoleResult, cfg, results: list) -> None:
    """Totals for one role in per_board scope; `results` are in cfg.sources order."""
    for i, source in enumerate(cfg.sources):
        res = results[i]
        result.capBySource.setdefault(source, board_limit(cfg, source))
        result.details[source] = res
        result.bySource[source] = res.accepted
        result.total += res.accepted
        if res.failed:
            result.failedSources.append(source)
    result.capReached = all((result.bySource.get(s) or 0) >= board_limit(cfg, s) for s in cfg.sources)


def search_boards(pairs: list, cfg, limit_for) -> list:
    """
    Runs (role, board) searches with at most PARALLEL_BOARDS at a time, in one queue, so a
    slow board never holds up searches for other roles. Results come back in `pairs` order.
    A search that crashes is recorded as failed and the others carry on.
    """
    def run(pair, _i):
        role, board = pair
        try:
            return scrape_source(board, role, limit_for(board), cfg)
        except Exception as e:
            log.error(f'[scrapeSource] {board} / "{role}" failed:', _error_message(e))
            return ScrapeSourceResult(source=board, failed=True, stopReason='error', error=_error_message(e))

    return run_with_limit(pairs, PARALLEL_BOARDS, run)


def scrape_roles_shared(roles: list, cfg) -> list:
    """per_board scope with SCRAPE_SHARED_QUEUE=true: every role's boards go into one queue."""
    pairs = [(role, board) for role in roles for board in cfg.sources]
    log.info(f'\n[scrapeRole] shared queue: {len(pairs)} board search(es) for {len(roles)} role(s), '
             f'{PARALLEL_BOARDS} at a time')
    results = search_boards(pairs, cfg, lambda board: board_limit(cfg, board))
    per_role = []
    n = len(cfg.sources)
    for k, role in enumerate(roles):
        result = ScrapeRoleResult(role=role, cap=sum(board_limit(cfg, b) for b in cfg.sources))
        _fill_role_result(result, cfg, results[k * n:(k + 1) * n])
        per_role.append(result)
    return per_role


def scrape_role(role: str, cfg) -> ScrapeRoleResult:
    """
    Scrapes one role across the configured boards.
      per_role:  one limit shared by the boards, filled in SCRAPE_SOURCES order; later boards
                 are skipped once it is reached.
      per_board: every board gets its own limit (SCRAPE_BOARD_LIMITS override, else the main one).
    """
    per_board = cfg.limitScope == 'per_board'
    result = ScrapeRoleResult(role=role)
    result.cap = sum(board_limit(cfg, b) for b in cfg.sources) if per_board else cfg.maxJobsPerRole

    if per_board:
        # Boards don't share a limit, so they can run side by side.
        def run_board(source, _i):
            result.capBySource[source] = board_limit(cfg, source)
            return scrape_source(source, role, board_limit(cfg, source), cfg)

        results = run_with_limit(cfg.sources, PARALLEL_BOARDS, run_board)
        _fill_role_result(result, cfg, results)
        return result

    # per_role: one shared limit, boards in SCRAPE_SOURCES order.
    for i, source in enumerate(cfg.sources):
        remaining = cfg.maxJobsPerRole - result.total
        if remaining <= 0:
            log.info(f'[scrapeRole] "{role}": limit of {cfg.maxJobsPerRole} reached, skipping remaining boards')
            break
        ask = min(remaining, board_limit(cfg, source))
        result.capBySource[source] = ask

        res = scrape_source(source, role, ask, cfg)
        result.details[source] = res
        result.bySource[source] = res.accepted
        result.total += res.accepted
        if res.failed:
            result.failedSources.append(source)

        more_to_do = i < len(cfg.sources) - 1 and result.total < cfg.maxJobsPerRole
        if more_to_do:
            time.sleep(2)  # gentle pacing between actor calls

    result.capReached = result.total >= cfg.maxJobsPerRole
    return result


def upsert_job(job: NormalizedJob) -> str:
    """Inserts or refreshes a job. A job whose content_hash already exists is saved as a duplicate."""
    dup = db.query(SQL.jobs.findDuplicateByContentHash, [job.contentHash, job.jobId])
    is_duplicate = dup.rowcount > 0
    canonical_id = dup.rows[0]['id'] if is_duplicate else None

    days_ago = js_floor((now_ms() - date_ms(job.postingDate)) / 86400000) if job.postingDate else None

    res = db.query(SQL.jobs.upsert, [
        job.jobId, job.contentHash, is_duplicate, canonical_id,
        job.title, job.companyName, job.companyNameNorm, job.jobUrl, job.jobDescription,
        job.locationRaw, job.city, job.state, job.stateCode, job.isRemote, job.isHybrid,
        job.salaryMin, job.salaryMax, job.salaryPeriod, job.salaryPresent,
        job.employmentType, job.source, job.postingDate, days_ago,
    ])

    if is_duplicate:
        return 'duplicate'
    return 'inserted' if res.rows[0]['inserted'] else 'updated'


def log_run(source: str, query: str, started, r: ScrapeSourceResult) -> None:
    db.query(SQL.scrapingLog.insertScrapeRun, [
        'daily_scrape', source, query, started, now(),
        r.found, r.inserted, r.updated, r.duplicate,
        'failed' if r.failed else 'success', r.error,
        r.apifyRunId, r.apifyRuns,
        round(r.apifyCostUsd, 6) if r.apifyCostUsd is not None else None,
    ])
