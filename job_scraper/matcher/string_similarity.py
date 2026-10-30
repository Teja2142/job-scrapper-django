"""
String similarity (Dice's coefficient on pairs of letters), the same as the `string-similarity`
npm package, plus a fast best-match search for the fuzzy pass.

Strings are handled as UTF-16 code units, like JavaScript, so lengths and results are identical.

BestMatchFinder returns the same best match as find_best_match() when the score is at or above a
threshold, but skips names that can't reach it: names of a very different length, and names that
share none of the candidate's rarest letter pairs.
"""
from __future__ import annotations

import math
from collections import defaultdict

from ..core.jscompat import jsre

_WS = jsre(r'\s+')


def to_units(s: str) -> str:
    """The string as UTF-16 code units (an astral character becomes its two surrogates)."""
    if all(ord(c) < 0x10000 for c in s):
        return s
    out = []
    for c in s:
        o = ord(c)
        if o < 0x10000:
            out.append(c)
        else:
            o -= 0x10000
            out.append(chr(0xD800 + (o >> 10)))
            out.append(chr(0xDC00 + (o & 0x3FF)))
    return ''.join(out)


def compare_two_strings(first: str, second: str) -> float:
    first = to_units(_WS.sub('', first))
    second = to_units(_WS.sub('', second))

    if first == second:
        return 1  # identical or empty
    if len(first) < 2 or len(second) < 2:
        return 0  # if either is a 0-letter or 1-letter string

    first_bigrams: dict = {}
    for i in range(len(first) - 1):
        bigram = first[i:i + 2]
        first_bigrams[bigram] = first_bigrams.get(bigram, 0) + 1

    intersection_size = 0
    for i in range(len(second) - 1):
        bigram = second[i:i + 2]
        count = first_bigrams.get(bigram, 0)
        if count > 0:
            first_bigrams[bigram] = count - 1
            intersection_size += 1

    return (2.0 * intersection_size) / (len(first) + len(second) - 2)


def find_best_match(main_string: str, target_strings: list) -> dict:
    ratings = []
    best_match_index = 0
    for i, target in enumerate(target_strings):
        rating = compare_two_strings(main_string, target)
        ratings.append({'target': target, 'rating': rating})
        if rating > ratings[best_match_index]['rating']:
            best_match_index = i
    best = ratings[best_match_index] if ratings else None
    return {'ratings': ratings, 'bestMatch': best, 'bestMatchIndex': best_match_index}


def _tokens(units: str) -> list:
    """Bigram multiset as distinct tokens: the k-th occurrence of bigram b is (b, k)."""
    seen: dict = {}
    out = []
    for i in range(len(units) - 1):
        b = units[i:i + 2]
        k = seen.get(b, 0)
        seen[b] = k + 1
        out.append((b, k))
    return out


class BestMatchFinder:
    """Index over the known names for repeated best-match searches."""

    def __init__(self, targets: list, threshold: float):
        self.targets = targets
        self.threshold = threshold
        self._units = [to_units(_WS.sub('', t)) for t in targets]
        self._by_len_tokens: dict = defaultdict(lambda: defaultdict(list))  # len -> token -> [index]
        self._by_len_short: dict = defaultdict(list)                         # len < 2 -> [index]
        self._freq: dict = defaultdict(int)
        self._toks: list = []
        for idx, u in enumerate(self._units):
            n = len(u)
            toks = _tokens(u) if n >= 2 else []
            self._toks.append(toks)
            if n < 2:
                self._by_len_short[n].append(idx)
                continue
            bucket = self._by_len_tokens[n]
            for tok in toks:
                bucket[tok].append(idx)
                self._freq[tok] += 1
        self._lengths = sorted(self._by_len_tokens.keys())

    def best(self, main_string: str):
        """
        (target, rating) of the best match when the rating is >= threshold, otherwise None.
        """
        if not self.targets:
            return None
        m_units = to_units(_WS.sub('', main_string))
        m = len(m_units)
        t = self.threshold

        if m < 2:
            # Only an identical string rates above 0; take the first one in list order.
            for idx in self._by_len_short.get(m, []):
                if self._units[idx] == m_units:
                    return self.targets[idx], 1
            return None if t > 0 else (self.targets[0], compare_two_strings(main_string, self.targets[0]))

        # Lengths n with 2*min(m-1, n-1) / (m+n-2) >= t can reach the threshold.
        bm = m - 1
        lo = math.ceil(bm * t / (2 - t)) + 1 if t < 2 else m
        hi = math.floor(bm * (2 - t) / t) + 1 if t > 0 else 10 ** 9
        lo = max(2, lo - 1)  # one length of slack for float rounding; exact scoring decides
        hi = hi + 1

        m_tokens = _tokens(m_units)
        m_token_set = set(m_tokens)
        # Rarest tokens first. A target of bigram count bn needs an overlap of at least
        # need = ceil(t * (bm + bn) / 2); with the smallest bn in range that is need_min, and every
        # qualifying target shares one of the first bm - need_min + 1 tokens.
        ordered = sorted(m_tokens, key=lambda tok: (self._freq.get(tok, 0), tok))
        min_bn = max(1, lo - 1)
        need_min = max(1, math.ceil(t * (bm + min_bn) / 2) - 1)  # -1: slack, exact scoring decides
        prefix = ordered[: max(1, bm - need_min + 1)]

        candidates: set = set()
        for n in self._lengths:
            if n < lo or n > hi:
                continue
            bucket = self._by_len_tokens[n]
            for tok in prefix:
                lst = bucket.get(tok)
                if lst:
                    candidates.update(lst)

        best_idx = -1
        best_rating = -1.0
        for idx in sorted(candidates):
            u = self._units[idx]
            if u == m_units:
                rating = 1
            else:
                inter = sum(1 for tok in self._toks[idx] if tok in m_token_set)
                rating = (2.0 * inter) / (m + len(u) - 2)
            if rating > best_rating:
                best_rating = rating
                best_idx = idx
        if best_idx >= 0 and best_rating >= t:
            return self.targets[best_idx], best_rating
        return None
