from __future__ import annotations

import unicodedata

from ..core.jscompat import js_trim, jsre, truthy

_COMBINING = jsre('[̀-ͯ]')
_SYMBOLS = jsre('[®™©℠]')
_AMP = jsre('&')
_SEPARATORS = jsre('[.,\'"\\/\\\\()\\-‘’“”–—]')
_WS = jsre(r'\s+')

LEGAL_SUFFIXES = {
    'inc', 'llc', 'ltd', 'limited', 'corp', 'corporation', 'co',
    'company', 'lp', 'llp', 'plc', 'gmbh',
}


def normalize_company_words(name) -> list:
    """normalize_company() without the final join: the list of words (used to count words)."""
    if not truthy(name):
        return []
    if not isinstance(name, str):
        raise TypeError('name.toLowerCase is not a function')

    n = js_trim(name.lower())

    # Remove accents and symbols like ® so "Vālenz Health®" and "VALENZ HEALTH" give the same key.
    # NFD, not NFKD: NFKD would turn "™" into the letters "tm".
    n = _COMBINING.sub('', unicodedata.normalize('NFD', n))
    n = _SYMBOLS.sub('', n)

    n = _AMP.sub(' and ', n)

    # Also the curly quotes and long dashes LinkedIn uses ("Moody’s", "Foo – Bar").
    n = _SEPARATORS.sub(' ', n)
    n = js_trim(_WS.sub(' ', n))

    words = [w for w in n.split(' ') if w]

    if words and words[0] == 'the':
        words = words[1:]

    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()

    return words


def normalize_company(name) -> str:
    """
    Turns a company name into a matching key: "Google LLC" -> "google", "Amazon.com, Inc." -> "amazoncom".

    Only legal suffixes at the end (inc, llc, ltd, corp ...) are removed. Words like group, software,
    solutions or services are kept, because they are often the only difference between two companies
    ("United Software Group" vs "United Consulting Group").
    """
    return ''.join(normalize_company_words(name))
