"""Runs Apify actors and explains runs that delivered nothing."""
from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass

from ..core import conf, log
from ..core.jscompat import js_trim, json_stringify, jsre

# Echo each actor's own log to the console (noisy, off by default).
STREAM_LOGS = conf.env('APIFY_STREAM_LOGS') == 'true'
# Print the input sent to each actor, and the run id, status, row count and Console link after it.
_log_runs_raw = conf.env('APIFY_LOG_RUNS')
LOG_RUNS = js_trim('true' if _log_runs_raw is None else _log_runs_raw).lower() != 'false'

_local = threading.local()


def _client():
    """One ApifyClient per thread (boards run on several threads at once)."""
    c = getattr(_local, 'client', None)
    if c is None:
        from apify_client import ApifyClient
        c = ApifyClient(token=conf.env('APIFY_API_TOKEN'))
        _local.client = c
    return c


def _field(obj, camel: str, snake: str):
    """Reads a run field from apify-client 1.x/2.x (dicts) or 3.x (models)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        v = obj.get(camel, obj.get(snake))
    else:
        v = getattr(obj, snake, None)
        if v is None:
            v = getattr(obj, camel, None)
    if v is not None and hasattr(v, 'value') and not isinstance(v, (str, int)):
        v = v.value  # enum (e.g. ActorJobStatus) -> its text
    return v


@dataclass
class EmptyReason:
    """
    Why an actor delivered no rows (actors finish "successfully" even when the board failed).
      board-error: the board blocked or broke the scrape (worth one retry).
      filtered:    the board had jobs, but the actor's own filters (requireTitleKeywords) removed
                   all of them -- deeper pages can still have matches.
      no-matches:  the board answered and simply had nothing.
    """
    kind: str  # 'board-error' | 'filtered' | 'no-matches' | 'unknown'
    message: str


@dataclass
class ActorResult:
    items: list
    runId: str
    # Only set when `items` is empty.
    emptyReason: EmptyReason | None
    # What Apify charged for the run (usageTotalUsd); None when Apify didn't report it.
    costUsd: float | None = None


_WS = jsre(r'\s+')


def clean(s: str, max_len: int = 240) -> str:
    return js_trim(_WS.sub(' ', s))[:max_len]


_RE_FAILED = jsre(r'\]\s*FAILED:\s*(.+)$')
_RE_BOARD_REPORTED = jsre(r'board reported:\s*(.+)$')
_RE_PARSER_FAILURE = jsre(r'PARSER FAILURE:\s*(.+)$')
_RE_CHALLENGE = jsre(r'security\/challenge|security challenge|cloudflare', 'i')
_RE_BLOCKED = jsre(r'Request blocked', 'i')
_RE_FOUND_JOBS = jsre(r'Found [1-9]\d* job', 'i')
_RE_NO_JOBS_FOR = jsre(r'No jobs found for ', 'i')
_RE_FOUND_ZERO = jsre(r'Found 0 job\(s\)', 'i')
_RE_ALL_REMOVED = jsre(r'All (\d+) scraped jobs? (?:was|were) removed by your output filters')
_RE_PER_BOARD = jsre(r'Per-board status:\s*(\{.*\})')
_RE_PER_BOARD_BAD = jsre(r'blocked|erroring|failed|error', 'i')
_RE_EXPLAINED = jsre(r'\]\s*(no row in the feed matches.+)$')
_RE_NO_MATCHING = jsre(r'no matching (listings|jobs)', 'i')
_LINE_SPLIT = jsre(r'\r?\n')


def explain_empty_run(log_text: str) -> EmptyReason:
    """Reads the actor's log and says why nothing was delivered."""
    lines = _LINE_SPLIT.split(log_text)

    def find(regex):
        for line in lines:
            m = regex.search(line)
            if m:
                return clean(m.group(1))
        return None

    failed = find(_RE_FAILED)
    if failed is None:
        failed = find(_RE_BOARD_REPORTED)
    if failed:
        return EmptyReason('board-error', failed)

    # ZipRecruiter: a block/challenge page fetched "OK" reads as a parser failure.
    parser = find(_RE_PARSER_FAILURE)
    if parser:
        return EmptyReason('board-error', f'parser failure (usually a block page): {parser}')

    # JobRight: redirected to its bot check.
    if _RE_CHALLENGE.search(log_text):
        return EmptyReason('board-error', "blocked by the board's bot check (security challenge page)")

    # CareerBuilder: the search page itself was blocked.
    if _RE_BLOCKED.search(log_text) and not _RE_FOUND_JOBS.search(log_text):
        return EmptyReason('board-error', 'the board blocked the search page (HTTP 403 after retries)')
    # CareerBuilder: the page loaded but listed no jobs.
    if _RE_NO_JOBS_FOR.search(log_text) or _RE_FOUND_ZERO.search(log_text):
        return EmptyReason('no-matches', 'the board answered, but no listings matched this search in the time window')

    # Multi-board actor: rows were found, but requireTitleKeywords removed every one of them.
    filtered = find(_RE_ALL_REMOVED)
    if filtered:
        return EmptyReason('filtered', f'all {filtered} job(s) looked at had off-title names (removed by the title filter, not billed)')

    per_board = find(_RE_PER_BOARD)
    if per_board and _RE_PER_BOARD_BAD.search(per_board):
        return EmptyReason('board-error', per_board)

    explained = find(_RE_EXPLAINED)
    if explained:
        return EmptyReason('no-matches', explained)

    if _RE_NO_MATCHING.search(log_text):
        return EmptyReason('no-matches', 'the board answered, but no listings matched this search in the time window')
    return EmptyReason('unknown', 'the actor returned no rows and its log gave no reason')


def explain_run(run_id: str, status: str | None = None, status_message: str | None = None) -> EmptyReason:
    """Reads a finished run's log and says why it delivered nothing usable."""
    try:
        log_text = _client().run(run_id).log().get()
        reason = explain_empty_run(log_text or '')
        if reason.kind == 'unknown' and status and status != 'SUCCEEDED':
            return EmptyReason('board-error', f'actor run {status}{f": {clean(status_message)}" if status_message else ""}')
        return reason
    except Exception:
        return EmptyReason('unknown', 'could not read the actor log')


def _call_actor(actor_id: str, run_input: dict):
    actor = _client().actor(actor_id)
    # With STREAM_LOGS off the actor's log is not copied into ours. Older apify-client versions
    # have no `logger` argument and never copy it.
    try:
        has_logger = 'logger' in inspect.signature(actor.call).parameters
    except (TypeError, ValueError):
        has_logger = False
    if has_logger:
        return actor.call(run_input=run_input, logger='default' if STREAM_LOGS else None)
    return actor.call(run_input=run_input)


def _as_dict(obj) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(by_alias=True)
    return dict(getattr(obj, '__dict__', {}))


def _number(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _charged_cost(run, row_count: int) -> float | None:
    """
    What the actor's own pricing adds up to: charged events x event price (pay per event), or
    rows x price per row (pay per result). Apify adds these charges to usageTotalUsd a little after
    the run ends, so right after the run that total can still show only the platform usage.
    """
    pricing = _as_dict(_field(run, 'pricingInfo', 'pricing_info'))
    model = pricing.get('pricingModel') or pricing.get('pricing_model')
    if model == 'PAY_PER_EVENT':
        per_event = _as_dict(pricing.get('pricingPerEvent') or pricing.get('pricing_per_event'))
        events = _as_dict(per_event.get('actorChargeEvents') or per_event.get('actor_charge_events'))
        counts = _as_dict(_field(run, 'chargedEventCounts', 'charged_event_counts'))
        total = 0.0
        for name, count in counts.items():
            event = _as_dict(events.get(name))
            price = _number(event.get('eventPriceUsd', event.get('event_price_usd')))
            if price is not None and _number(count) is not None:
                total += price * _number(count)
        return total
    if model == 'PRICE_PER_DATASET_ITEM':
        price = _number(pricing.get('pricePerUnitUsd', pricing.get('price_per_unit_usd')))
        return price * row_count if price is not None else None
    return None


def _run_cost(run_id: str, finished_run, row_count: int = 0) -> float | None:
    """
    Cost of a finished run: Apify's usageTotalUsd, or the actor's charges for the run if those are
    higher (usageTotalUsd may not include them yet). The run is read again to get the latest figures.
    """
    run = finished_run
    try:
        run = _client().run(run_id).get() or finished_run
    except Exception:
        pass
    usage = _number(_field(run, 'usageTotalUsd', 'usage_total_usd'))
    try:
        charged = _charged_cost(run, row_count)
    except Exception:
        charged = None
    if usage is None and charged is None:
        return None
    return max(usage or 0.0, charged or 0.0)


def run_actor_detailed(actor_id: str, run_input: dict) -> ActorResult:
    """Runs an actor and returns its dataset rows, plus the reason when there are none."""
    if LOG_RUNS:
        log.info(f'[apify] {actor_id} input: {json_stringify(run_input)}')
    run = _call_actor(actor_id, run_input)
    if run is None:
        raise RuntimeError(f'Actor {actor_id} run could not be started or did not finish')
    run_id = _field(run, 'id', 'id')
    status = _field(run, 'status', 'status')
    dataset_id = _field(run, 'defaultDatasetId', 'default_dataset_id')
    page = _client().dataset(dataset_id).list_items()
    items = list(_field(page, 'items', 'items') or [])
    cost = _run_cost(run_id, run, len(items))
    if LOG_RUNS:
        cost_text = f', ${cost:.4f}' if cost is not None else ''
        log.info(
            f'[apify] {actor_id} run {run_id}: {status}, {len(items)} row(s){cost_text} -- '
            f'https://console.apify.com/view/runs/{run_id}'
        )
    if len(items) > 0:
        return ActorResult(items=items, runId=run_id, emptyReason=None, costUsd=cost)
    return ActorResult(items=[], runId=run_id, costUsd=cost,
                       emptyReason=explain_run(run_id, status, _field(run, 'statusMessage', 'status_message')))
