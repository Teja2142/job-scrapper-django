"""Turns a company's visa data into a 0-100 score and an A-F grade."""
from __future__ import annotations

from ..core.jscompat import current_local_year, js_or, js_round


def compute_visa_score(h1b_filings=0, approvals=0, denials=0, perm_filings=0, everify=False, year=0) -> dict:
    score = 0

    # Filing volume: take the bigger of the LCA filings and the USCIS decisions. The two files don't
    # fully overlap, so a company with plenty of USCIS approvals can show 0 LCA filings.
    total_decisions = js_or(approvals, 0) + js_or(denials, 0)
    filings = max(js_or(h1b_filings, 0), total_decisions)
    if filings >= 100:
        score += 40
    elif filings >= 25:
        score += 30
    elif filings >= 5:
        score += 20
    elif filings >= 1:
        score += 10

    # Approval rate
    total = total_decisions
    if total > 0:
        rate = js_or(approvals, 0) / total
        score += js_round(rate * 25)

    # Recency
    yr = js_or(year, 0)
    current_year = current_local_year()
    if yr >= current_year - 1:
        score += 15
    elif yr >= current_year - 3:
        score += 8

    # Green card sponsorship
    if js_or(perm_filings, 0) > 0:
        score += 10

    # E-Verify
    if everify:
        score += 10

    score = min(score, 100)

    grade = 'F'
    if score >= 85:
        grade = 'A'
    elif score >= 70:
        grade = 'B'
    elif score >= 50:
        grade = 'C'
    elif score >= 30:
        grade = 'D'
    elif score >= 15:
        grade = 'E'

    return {'score': score, 'grade': grade}
