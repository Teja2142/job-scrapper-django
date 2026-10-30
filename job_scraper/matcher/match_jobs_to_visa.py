"""
Visa matcher: copies each company's visa record (company_visa_history) onto its jobs in us_jobs.

A wrong match tells a candidate a company sponsors H-1B when it may not, which is worse than no
data. So every pass is strict, and when in doubt the job stays unmatched.
"""
from __future__ import annotations

from ..core import db, log
from ..core.jscompat import current_local_year, now, to_fixed
from ..core.sql import SQL
from .normalize_company import normalize_company_words
from .string_similarity import BestMatchFinder, to_units

# Similarity needed for a fuzzy match. Kept high on purpose: only near-identical names match.
FUZZY_THRESHOLD = 0.9

# Names shorter than this are not fuzzy matched. Short names match unrelated companies too
# easily ("ctac" and "acta" scored a perfect 1.00 because they are anagrams).
MIN_NORM_LENGTH = 6

# A second, edit-distance check a fuzzy match must also pass. It catches anagrams like the
# one above, which the first check can't tell apart.
FUZZY_SECONDARY_RATIO_THRESHOLD = 0.85

# Prefix pass: for brands whose legal name is longer, e.g. "Capital One" vs
# "CAPITAL ONE SERVICES LLC". These never match exactly or fuzzily.
MIN_PREFIX_LENGTH = 7

# The job's company name must have at least this many words for the prefix pass. Single words
# gave wrong matches ("hired" -> "hiredigital"). They can still match in the other passes.
MIN_PREFIX_WORDS = 2

# Reverse prefix: the job's name is the longer one, e.g. "Amazon Web Services (AWS)" vs the
# filer "Amazon Web Services". Only allowed when the extra last word is short like "AWS";
# longer words gave wrong matches ("United Software Group" -> "United Software").
MAX_REVERSE_ABBREVIATION_LENGTH = 4

# A company counts as a recent filer if its last filing year is within this many years.
RECENT_YEARS_WINDOW = 2


def match_jobs_to_visa(rematch_all: bool = False) -> None:
    """
    Matches every job that isn't matched yet, in five passes:
      1. company_aliases (added by hand, so it runs first and can override an exact match)
      2. exact name match
      3. fuzzy match (two similarity checks)
      4. prefix match (unique, multi-word names only)
      5. exact match on companies that are only in the E-Verify list (last resort)
    Passes 2-4 only use companies with real filings (see HAS_EVIDENCE_SQL in core/sql.py).
    """
    started = now()
    recent_cutoff_year = current_local_year() - RECENT_YEARS_WINDOW

    # rematch_all clears every job's match first. The visa fields are copied at match time, so
    # this is how new visa data reaches jobs that were already matched.
    if rematch_all:
        reset = db.query(SQL.matcher.resetAllMatches)
        log.info(f'[matcher] --rematch-all: reset visa_matched = FALSE (and cleared stale visa fields) on {reset.rowcount} job(s) before matching')

    # Pass 1: alias table
    alias_matched = run_alias_match_pass(recent_cutoff_year)

    # Pass 2: exact match
    exact = db.query(SQL.matcher.applyExactMatch, [recent_cutoff_year])
    log.info(f'[matcher] exact-matched {exact.rowcount} jobs to visa data')

    # Pass 3: fuzzy match
    fuzzy_matched = run_fuzzy_match_pass(recent_cutoff_year)
    log.info(f'[matcher] fuzzy-matched {fuzzy_matched} additional jobs to visa data')

    # Pass 4: prefix match
    prefix_matched = run_prefix_match_pass(recent_cutoff_year)
    log.info(f'[matcher] prefix-matched {prefix_matched} additional jobs to visa data')

    # Pass 5: E-Verify-only companies. Big brands whose filings are under another name need an
    # alias instead (python manage.py add_alias).
    no_evidence = db.query(SQL.matcher.applyExactMatchNoEvidence, [recent_cutoff_year])
    log.info(f'[matcher] exact-matched {no_evidence.rowcount} more jobs to companies with E-Verify data only (no H-1B/PERM filings)')

    db.query(SQL.scrapingLog.insertMatchRun, [
        started, exact.rowcount + alias_matched + fuzzy_matched + prefix_matched + no_evidence.rowcount,
    ])


def run_alias_match_pass(recent_cutoff_year: int) -> int:
    """
    Pass 1: company_aliases maps a brand name to the right filing name, for cases the other passes
    can't solve: names with nothing in common ("CGI Group" vs "CGI Technologies and Solutions"), or an
    exact match that is the wrong company ("oracle" has a holding-company row with no filings, the
    real filer is "Oracle America, Inc").
    """
    res = db.query(SQL.matcher.applyAliasMatch, [recent_cutoff_year])
    log.info(f'[matcher] alias pass: {res.rowcount} jobs matched via curated alias table')
    return res.rowcount


def levenshtein_ratio(a: str, b: str) -> float:
    """Edit-distance similarity from 0 to 1. Unlike the first check, it notices reordered letters."""
    if a == b:
        return 1
    a, b = to_units(a), to_units(b)
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        ai = a[i - 1]
        for j in range(1, n + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1]
            else:
                cur[j] = 1 + min(prev[j], cur[j - 1], prev[j - 1])
        prev = cur
    distance = prev[n]
    return 1 - distance / max(m, n)


def is_prefix_or_suffix_pair(a: str, b: str) -> bool:
    """
    True when one name starts or ends with the other ("grandview" / "grandviewi"). Similarity alone
    can't judge these, so the fuzzy pass leaves them to the prefix pass, which has stricter rules.
    """
    return a.startswith(b) or b.startswith(a) or a.endswith(b) or b.endswith(a)


def run_fuzzy_match_pass(recent_cutoff_year: int) -> int:
    # Unmatched company names, with how many jobs each one has.
    unmatched = db.query(SQL.matcher.unmatchedCompaniesForFuzzy, [MIN_NORM_LENGTH]).rows

    if len(unmatched) == 0:
        log.info('[matcher] fuzzy pass: no unmatched companies to check.')
        return 0

    # Load all known company norms once, rather than querying per candidate.
    known = db.query(SQL.matcher.knownCompanyNorms).rows
    known_norms = [k['company_name_norm'] for k in known]

    if len(known_norms) == 0:
        log.info('[matcher] fuzzy pass: company_visa_history is empty, nothing to match against.')
        return 0

    log.info(f'[matcher] fuzzy pass: checking {len(unmatched)} unmatched companies against {len(known_norms)} known companies...')

    # Gives the same best match as comparing against every known name, just much faster.
    finder = BestMatchFinder(known_norms, FUZZY_THRESHOLD)

    total_jobs_updated = 0
    companies_matched = 0
    checked = 0
    deferred_to_prefix = 0
    failed_secondary_check = 0

    for candidate in unmatched:
        checked += 1
        name = candidate['company_name_norm']
        best = finder.best(name)

        if best is not None and best[0] != name:
            target, rating = best
            # Leave these to the prefix pass (see is_prefix_or_suffix_pair).
            if is_prefix_or_suffix_pair(name, target):
                deferred_to_prefix += 1
                log.info(
                    f'[matcher] fuzzy pass: deferring prefix/suffix-shaped pair to prefix pass: '
                    f'"{name}" ~ "{target}" (Dice {to_fixed(rating, 2)})'
                )
                continue

            # Catches anagrams like "ctac"/"acta".
            secondary_ratio = levenshtein_ratio(name, target)
            if secondary_ratio < FUZZY_SECONDARY_RATIO_THRESHOLD:
                failed_secondary_check += 1
                log.info(
                    f'[matcher] fuzzy pass: rejecting likely-coincidental match (Dice {to_fixed(rating, 2)} '
                    f'but sequence ratio only {to_fixed(secondary_ratio, 2)}): '
                    f'"{name}" ~ "{target}"'
                )
                continue

            res = db.query(SQL.matcher.applyNamedMatch, [name, target, recent_cutoff_year])

            if res.rowcount > 0:
                companies_matched += 1
                total_jobs_updated += res.rowcount
                # Each fuzzy match is logged so it can be checked by hand.
                log.info(
                    f'[matcher] fuzzy match ({to_fixed(rating, 2)}): '
                    f'"{name}" -> "{target}" '
                    f'({res.rowcount} job(s) updated)'
                )

        if checked % 500 == 0:
            log.info(f'[matcher] fuzzy pass progress: {checked}/{len(unmatched)} checked, {companies_matched} matched so far')

    log.info(
        f'[matcher] fuzzy pass complete: {companies_matched} companies matched, {total_jobs_updated} jobs updated, '
        f'{deferred_to_prefix} deferred to prefix pass, {failed_secondary_check} rejected by secondary similarity check'
    )
    return total_jobs_updated


def run_prefix_match_pass(recent_cutoff_year: int) -> int:
    """
    Pass 4. Forward: the job's name is the start of exactly one known company's name
    ("capitalone" -> "capitaloneservices"). If it is the start of two or more, it's left alone.
    Reverse (only if forward finds nothing): a known company's name is the job's name without its
    short last word ("amazonwebservicesaws" -> "amazonwebservices").
    """
    # One original name per company, to count its words and split it into words for the reverse check.
    unmatched = db.query(SQL.matcher.unmatchedCompaniesForPrefix, [MIN_PREFIX_LENGTH]).rows

    if len(unmatched) == 0:
        log.info('[matcher] prefix pass: no unmatched companies to check.')
        return 0

    # The same length and word-count rules apply to the known company in the reverse direction.
    known_raw = db.query(SQL.matcher.knownCompaniesForReversePrefix, [MIN_PREFIX_LENGTH]).rows

    known_for_reverse = {
        k['company_name_norm'] for k in known_raw
        if len(normalize_company_words(k['company_name'])) >= MIN_PREFIX_WORDS
    }

    log.info(f'[matcher] prefix pass: checking {len(unmatched)} unmatched companies...')

    total_jobs_updated = 0
    companies_matched = 0
    ambiguous_skipped = 0
    single_word_skipped = 0
    reverse_matched = 0

    for candidate in unmatched:
        norm = candidate['company_name_norm']
        candidate_words = normalize_company_words(candidate['sample_name'])

        # Skip single-word (too generic) brand names -- see MIN_PREFIX_WORDS.
        if len(candidate_words) < MIN_PREFIX_WORDS:
            single_word_skipped += 1
            continue

        # Forward. % and _ are escaped for LIKE.
        like_pattern = ''.join('\\' + c if c in '%_' else c for c in norm) + '%'
        candidates = db.query(SQL.matcher.findPrefixCandidates, [like_pattern]).rows  # 0, 1 or 2 rows

        target = None
        is_reverse_match = False

        if len(candidates) == 1:
            target = candidates[0]['company_name_norm']
        elif len(candidates) > 1:
            ambiguous_skipped += 1
            continue  # 2+ forward candidates -- ambiguous, don't guess
        else:
            # Reverse: only at the boundary before the last word, and only if that word is short.
            target = None
            if len(candidate_words) - 1 >= MIN_PREFIX_WORDS:
                last_word = candidate_words[-1]
                if len(to_units(last_word)) <= MAX_REVERSE_ABBREVIATION_LENGTH:
                    root_norm = ''.join(candidate_words[:-1])
                    if root_norm != norm and root_norm in known_for_reverse:
                        target = root_norm
                        is_reverse_match = True

            if target is None:
                continue  # no reverse match either -- don't guess

        res = db.query(SQL.matcher.applyNamedMatch, [norm, target, recent_cutoff_year])

        if res.rowcount > 0:
            companies_matched += 1
            total_jobs_updated += res.rowcount
            if is_reverse_match:
                reverse_matched += 1
            log.info(
                f'[matcher] {"reverse " if is_reverse_match else ""}prefix match: "{norm}" -> "{target}" '
                f'({res.rowcount} job(s) updated)'
            )

    log.info(
        f'[matcher] prefix pass complete: {companies_matched} companies matched ({reverse_matched} via reverse direction), '
        f'{total_jobs_updated} jobs updated, {ambiguous_skipped} skipped as ambiguous (2+ forward candidates), '
        f'{single_word_skipped} skipped as single-word (too generic for safe prefix matching)'
    )
    return total_jobs_updated
