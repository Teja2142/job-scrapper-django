"""Helpers shared by the board parsers."""
from __future__ import annotations

import hashlib
import re

from ..core import conf
from ..core.jscompat import is_finite, js_number, js_or, js_round, js_string, js_trim, jsre, parse_js_date, truthy, unique


def parse_date(value):
    """Returns None instead of an Invalid Date."""
    if not truthy(value):
        return None
    return parse_js_date(value)


_NON_MONEY = jsre(r'[^0-9.]')


def parse_money(value):
    """"$85,000" | "85000" | 85000 -> 85000. Unreadable -> None."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = js_number(value)
    else:
        n = js_number(_NON_MONEY.sub('', js_string(value)))
    return js_round(n) if is_finite(n) and n > 0 else None


_PERIODS = [
    (jsre(r'^(year|yearly|annual|annually|per year|yr)$'), 'yearly'),
    (jsre(r'^(hour|hourly|per hour|hr)$'), 'hourly'),
    (jsre(r'^(day|daily|per day)$'), 'daily'),
    (jsre(r'^(week|weekly|per week)$'), 'weekly'),
    (jsre(r'^(month|monthly|per month)$'), 'monthly'),
]


def normalize_salary_period(value):
    """Pay period -> 'yearly' | 'hourly' | 'daily' | 'weekly' | 'monthly'. Unknown -> None."""
    if not truthy(value):
        return None
    v = js_trim(js_string(value).lower())
    for pattern, period in _PERIODS:
        if pattern.search(v):
            return period
    return None


_UNDERSCORE_SPACE = jsre(r'[_\s]+')


def normalize_employment_type(value):
    """"Full-time, Contract" | ["FULL_TIME", "CONTRACT"] -> "full-time, contract" (max 50 chars)."""
    if isinstance(value, (list, tuple)):
        parts = [js_string(v) for v in value]
    elif isinstance(value, str):
        parts = value.split(',')
    else:
        parts = []

    cleaned = [_UNDERSCORE_SPACE.sub('-', js_trim(p.lower())) for p in parts]
    cleaned = ['full-time' if p == 'fulltime' else 'part-time' if p == 'parttime' else 'contract' if p == 'contracts' else p
               for p in cleaned]
    cleaned = [p for p in cleaned if p]

    uniq = unique(cleaned)
    return ', '.join(uniq)[:50] if uniq else None


def make_content_hash(title: str, company: str, city, state_code) -> str:
    """title|company|city|state hash: the same job on two boards gets the same hash (cross-board dedup)."""
    city_s = '' if city is None else js_string(city)
    state_s = '' if state_code is None else js_string(state_code)
    text = f'{js_trim(title.lower())}|{js_trim(company.lower())}|{city_s}|{state_s}'
    # hash the UTF-8 bytes; a broken surrogate becomes U+FFFD
    text = text.encode('utf-16', 'surrogatepass').decode('utf-16', 'replace')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


# Words too common in job titles to tell roles apart ("engineer", "senior", ...).
# TITLE_GENERIC_WORDS in .env replaces this list.
DEFAULT_GENERIC_WORDS = (
    'engineer,engineers,engineering,software,developer,development,senior,sr,junior,jr,lead,staff,'
    'principal,associate,i,ii,iii,iv,systems,system,specialist,and,of,the'
)
GENERIC_WORDS = {
    js_trim(w).lower() for w in js_or(conf.env('TITLE_GENERIC_WORDS'), DEFAULT_GENERIC_WORDS).split(',')
    if js_trim(w).lower()
}

# distinctive = only the role's distinctive words must be in the title
#   ("embedded software engineer" keeps "Sr Embedded Engineer")
# all         = every word of the role must be in the title (stricter)
TITLE_MATCH_MODE = 'all' if js_trim(js_or(conf.env('TITLE_MATCH_MODE'), 'distinctive')).lower() == 'all' else 'distinctive'

_ROLE_SPLIT = jsre(r'[^a-z0-9+#]+')
_WS_SPLIT = jsre(r'\s+')


def distinctive_title_words(role: str) -> list:
    """
    The words that identify a role: "embedded software engineer" -> ["embedded"],
    "iot engineer" -> ["iot"]. Empty when every word is generic ("software engineer").
    """
    words = [w for w in _ROLE_SPLIT.split(role.lower()) if w]
    return unique(w for w in words if w not in GENERIC_WORDS)


def title_matches_role(title: str, role: str) -> bool:
    """
    Does the job title fit the role? With TITLE_MATCH_MODE=distinctive every distinctive word must
    start a word in the title ("sensor" matches "Sensors", "iot" does not match "Patriot"); a role
    with no distinctive word falls back to needing every word. TITLE_MATCH_MODE=all always needs
    every word.
    """
    distinctive = distinctive_title_words(role)
    if TITLE_MATCH_MODE == 'all' or len(distinctive) == 0:
        return title_matches_query(title, role)
    return all(jsre('(^|[^a-z0-9])' + re.escape(w), 'i').search(title) for w in distinctive)


def actor_title_keywords(role: str) -> list:
    """
    Keywords for the multi-board actor's requireTitleKeywords. The actor keeps a title containing ANY
    of them, so only the distinctive words are sent. A role without any is sent whole, as a phrase.
    """
    distinctive = distinctive_title_words(role)
    return distinctive if len(distinctive) > 0 else [js_trim(role)]


def title_matches_query(title: str, query: str) -> bool:
    """True if every word of the query appears in the title ("engineer" matches "Engineering")."""
    words = [w for w in _WS_SPLIT.split(query.lower()) if w]
    title_lower = title.lower()
    return all(w in title_lower for w in words)
