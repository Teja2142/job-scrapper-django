"""
Loads a DOL LCA (H-1B) disclosure .xlsx file into company_visa_history.

The file is huge (about 1 GB unzipped, a million rows, most of them blank), so it is read row by
row. Columns used: EMPLOYER_NAME, CASE_STATUS, DECISION_DATE, VISA_CLASS, WAGE_RATE_OF_PAY_FROM,
WAGE_UNIT_OF_PAY. Column names change between fiscal years, so check a new file with
log_column_headers_streaming() (excel_utils) first.

- Rows are grouped by the normalized company name, so name variants of one company add up.
- Only VISA_CLASS = 'H-1B' rows count (E-3 and H-1B1 are other visas).
- Wages are turned into a yearly figure first, and the company figure is the median.
- This file only fills h1b_total_filings and h1b_avg_salary. "Certified" here is a Department of
  Labor step, not a USCIS approval; approvals come from parse_uscis_hub.py.
- The files are cumulative, so each load replaces the previous numbers instead of adding to them.
"""
from __future__ import annotations

from ..core import db, log
from ..core.jscompat import (current_local_year, is_nan, js_number, js_round, js_string, js_trim, local_year,
                        now_ms, to_fixed, truthy)
from ..matcher.normalize_company import normalize_company
from ..core.sql import SQL
from .build_visa_history import upsert_company_visa
from .excel_utils import cell, index_of, iter_xlsx_rows, read_header_row, resolve_excel_date

HOURS_PER_YEAR = 2080  # 40 hrs/week * 52 weeks, standard DOL assumption
WEEKS_PER_YEAR = 52
MONTHS_PER_YEAR = 12
BIWEEKLY_PER_YEAR = 26

# A wage this high can't really be per hour / week / month. It is a yearly salary entered with the
# wrong unit (e.g. "135533 Hour"), so it is used as yearly.
YEARLY_TYPO_THRESHOLD = {
    'hour': 1000,
    'week': 20000,
    'bi-weekly': 40000,
    'biweekly': 40000,
    'month': 50000,
}

# Only these LCAs count as H-1B filings (E-3 and H-1B1 are different visas).
H1B_VISA_CLASS = 'H-1B'


def normalize_to_annual(amount, unit):
    if not truthy(amount) or is_nan(amount) or amount <= 0:
        return None
    if truthy(unit) and not isinstance(unit, str):
        raise TypeError('(unit || "").toLowerCase is not a function')
    u = js_trim((unit or '').lower())

    typo_threshold = YEARLY_TYPO_THRESHOLD.get(u)
    if typo_threshold is not None and amount >= typo_threshold:
        return amount

    if u == 'year':
        return amount
    if u == 'hour':
        return amount * HOURS_PER_YEAR
    if u == 'week':
        return amount * WEEKS_PER_YEAR
    if u == 'month':
        return amount * MONTHS_PER_YEAR
    if u in ('bi-weekly', 'biweekly'):
        return amount * BIWEEKLY_PER_YEAR

    # Unknown unit: assume it is already yearly rather than lose the row
    return amount


def median(values: list):
    """Median, so a few $1M+ or mistyped salaries don't push a company's figure up."""
    if len(values) == 0:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return (s[mid - 1] + s[mid]) / 2 if len(s) % 2 == 0 else s[mid]


def parse_lca_file(file_path: str) -> dict:
    log.info(f'[parseLCA] streaming {file_path}...')
    start = now_ms()

    employer_agg: dict = {}

    processed = 0
    skipped = 0
    other_visa_class = 0
    row_index = 0

    col_employer_name = -1
    col_case_status = -1
    col_decision_date = -1
    col_wage_from = -1
    col_wage_unit = -1
    col_visa_class = -1

    for values in iter_xlsx_rows(file_path):
        row_index += 1

        if row_index == 1:
            # Header row -- locate the columns we need by name.
            headers = read_header_row(values)

            col_employer_name = index_of(headers, 'EMPLOYER_NAME')
            col_case_status = index_of(headers, 'CASE_STATUS')
            col_decision_date = index_of(headers, 'DECISION_DATE')
            col_wage_from = index_of(headers, 'WAGE_RATE_OF_PAY_FROM')
            col_wage_unit = index_of(headers, 'WAGE_UNIT_OF_PAY')
            col_visa_class = index_of(headers, 'VISA_CLASS')

            if col_visa_class == -1:
                log.warn(
                    '[parseLCA] WARNING: VISA_CLASS column not found -- E-3 / H-1B1 rows '
                    "can't be told apart and will be counted as H-1B."
                )

            if col_employer_name == -1 or col_case_status == -1:
                log.warn(
                    '[parseLCA] WARNING: expected columns not found in header row. '
                    f'Found headers: {", ".join(h for h in headers if h)}. '
                    "This file's layout may differ from the confirmed FY2026 Q2 layout — "
                    'check the Record Layout PDF for this fiscal year/quarter.'
                )
            continue

        try:
            employer_name = cell(values, col_employer_name)
            case_status = cell(values, col_case_status)

            # Skip blank padding rows (most of the file)
            if not truthy(employer_name) and not truthy(case_status):
                continue

            if not truthy(employer_name) or not js_trim(js_string(employer_name)):
                skipped += 1
                continue

            norm_key = normalize_company(js_string(employer_name))
            if not norm_key:
                skipped += 1
                continue
            # Not an H-1B LCA (E-3 Australian, H-1B1 Chile / Singapore): skip.
            if col_visa_class >= 0:
                vc = cell(values, col_visa_class)
                visa_class = js_trim(js_string('' if vc is None else vc))
                if visa_class != H1B_VISA_CLASS:
                    other_visa_class += 1
                    continue

            wage_from_raw = cell(values, col_wage_from)
            wage_unit_raw = cell(values, col_wage_unit)
            annual_wage = normalize_to_annual(js_number(wage_from_raw), wage_unit_raw)

            decision_date_raw = cell(values, col_decision_date)
            year = current_local_year()
            parsed_date = resolve_excel_date(decision_date_raw)
            if parsed_date:
                year = local_year(parsed_date)

            entry = employer_agg.get(norm_key) or {
                'companyName': js_trim(js_string(employer_name)),
                'filings': 0,
                'annualSalaries': [],
                'lastYear': 0,
            }

            entry['filings'] += 1
            if truthy(annual_wage):
                entry['annualSalaries'].append(annual_wage)
            if year > entry['lastYear']:
                entry['lastYear'] = year

            employer_agg[norm_key] = entry
            processed += 1
        except Exception:
            # One bad row must not stop the whole load
            skipped += 1

        if processed > 0 and processed % 100000 == 0:
            log.info(f'[parseLCA] ...{processed} real rows processed so far')

    elapsed = to_fixed((now_ms() - start) / 1000, 1)
    log.info(f'[parseLCA] streamed {row_index} total rows ({elapsed}s), processed {processed}, skipped {skipped}, '
             f'not H-1B (E-3/H-1B1) {other_visa_class}. Unique employers: {len(employer_agg)}')

    if len(employer_agg) == 0:
        log.warn(
            '[parseLCA] WARNING: 0 employers extracted. Check that column names '
            "(EMPLOYER_NAME, CASE_STATUS, etc.) match this file's actual headers — "
            'see the warning above for what was actually found.'
        )
        # Nothing parsed -- leave the previous load's numbers alone.
        return {'processed': processed, 'skipped': skipped, 'employers': 0}

    # Replace, don't add: clear the LCA columns first so a company missing from this file
    # doesn't keep old numbers.
    reset = db.query(SQL.visaHistory.resetLcaFields)
    log.info(f'[parseLCA] cleared previous LCA numbers on {reset.rowcount} companies')

    written = 0
    for entry in employer_agg.values():
        median_salary = median(entry['annualSalaries'])
        kwargs = {}
        if median_salary is not None:
            kwargs['avg_salary'] = js_round(median_salary)
        upsert_company_visa(
            entry['companyName'],
            h1b_filings=entry['filings'],
            # approvals / denials come from the USCIS file only
            year=entry['lastYear'],
            **kwargs,
        )
        written += 1

    log.info(f'[parseLCA] wrote {written} companies to company_visa_history')
    return {'processed': processed, 'skipped': skipped, 'employers': written}
