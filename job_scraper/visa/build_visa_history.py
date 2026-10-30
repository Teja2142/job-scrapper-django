from __future__ import annotations

from ..core import db, log
from ..core.jscompat import js_number, js_or, js_round
from ..matcher.normalize_company import normalize_company
from ..core.sql import SQL
from .scoring import compute_visa_score

_UNSET = object()


def upsert_company_visa(company_name: str, h1b_filings=_UNSET, year=_UNSET, approvals=_UNSET, denials=_UNSET,
                        avg_salary=_UNSET, perm_filings=_UNSET, everify=_UNSET) -> None:
    """
    Saves one company's numbers from one loader (LCA, USCIS, PERM or E-Verify).

    Values are replaced, not added: the DOL files are cumulative (each quarter's file already has
    every filing since October), so adding would double the counts. Pass None for a field this
    loader doesn't provide and the existing value is kept.

    Scores and grades are calculated later by rescore_all_companies().
    """
    norm = normalize_company(company_name)
    if not norm:
        return

    # None (not 0) keeps what another loader wrote there (COALESCE in the SQL).
    h1b = None if h1b_filings is _UNSET else h1b_filings
    appr = None if approvals is _UNSET else approvals
    den = None if denials is _UNSET else denials
    avg = None if avg_salary is _UNSET else avg_salary
    # Any H-1B field updates the last filing year, including USCIS approvals / denials.
    supplies_h1b_data = h1b_filings is not _UNSET or approvals is not _UNSET or denials is not _UNSET
    yr = None if year is _UNSET else year
    h1b_year = yr if supplies_h1b_data else None
    perm = None if perm_filings is _UNSET else perm_filings
    perm_year = yr if perm_filings is not _UNSET else None
    ev = None if everify is _UNSET else everify
    green_card_sponsor = (perm_filings > 0) if perm_filings is not _UNSET else None

    # Create the row first without numbers, so the table's defaults (0 / FALSE) apply instead of NULL.
    db.query(SQL.visaHistory.ensureCompany, [company_name, norm])

    # Then update only the fields this loader provides, so e.g. PERM never touches LCA numbers.
    db.query(SQL.visaHistory.mergeCompany, [
        company_name, norm,
        h1b, appr, den, avg,
        h1b_year, perm, perm_year, green_card_sponsor,
        ev,
    ])


RESCORE_BATCH_SIZE = 5000


def rescore_all_companies() -> int:
    """
    Recalculates score, grade and approval rate for every company. Runs once at the end of the visa
    refresh, after all four loaders, so every score matches the final data (the E-Verify loader and
    the column resets don't rescore companies themselves).
    """
    last_id = 0
    total = 0

    while True:
        rows = db.query(SQL.visaHistory.selectForRescore, [last_id, RESCORE_BATCH_SIZE]).rows
        if len(rows) == 0:
            break

        ids: list = []
        scores: list = []
        grades: list = []
        rates: list = []

        for r in rows:
            approvals = js_or(js_number(r['h1b_approvals']), 0)
            denials = js_or(js_number(r['h1b_denials']), 0)
            decisions = approvals + denials

            res = compute_visa_score(
                h1b_filings=js_or(js_number(r['h1b_total_filings']), 0),
                approvals=approvals,
                denials=denials,
                year=js_or(js_number(r['h1b_last_filing_year']), 0),
                perm_filings=js_or(js_number(r['perm_total_filings']), 0),
                everify=r['everify_enrolled'] is True,
            )

            ids.append(str(r['id']))
            scores.append(res['score'])
            grades.append(res['grade'])
            rates.append(js_round((approvals / decisions) * 10000) / 100 if decisions > 0 else None)

        db.query(SQL.visaHistory.saveScoresBatch, [ids, scores, grades, rates])

        total += len(rows)
        last_id = js_number(rows[-1]['id'])
        if total % 100000 < RESCORE_BATCH_SIZE:
            log.info(f'[rescore] ...{total} companies rescored')

    log.info(f'[rescore] done: {total} companies rescored')
    return total
