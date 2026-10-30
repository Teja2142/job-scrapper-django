"""
Loads the USCIS H-1B Employer Data Hub .xlsx file into company_visa_history (approvals and
denials only).

LCA "Certified" (parse_lca.py) is only the Department of Labor step before the real petition.
This file has the actual USCIS decisions, so it fills h1b_approvals / h1b_denials and never
touches h1b_total_filings.

- Use the .xlsx export. The .csv one is UTF-16, tab-separated and has commas in the numbers.
- One employer can have several rows (one per city), so rows are added up per company.
- Approvals / denials of a row = the sum of the six petition-type columns.
- Rows with a blank employer name (USCIS hides very small employers) are skipped.
"""
from __future__ import annotations

from ..core import db, log
from ..core.jscompat import current_local_year, js_number, js_or, js_parse_int, js_string, js_trim, now_ms, to_fixed, truthy
from ..matcher.normalize_company import normalize_company
from ..core.sql import SQL
from .build_visa_history import upsert_company_visa
from .excel_utils import cell, index_of, iter_xlsx_rows, read_header_row

APPROVAL_COLUMNS = [
    'New Employment Approval',
    'Continuation Approval',
    'Change with Same Employer Approval',
    'New Concurrent Approval',
    'Change of Employer Approval',
    'Amended Approval',
]

DENIAL_COLUMNS = [
    'New Employment Denial',
    'Continuation Denial',
    'Change with Same Employer Denial',
    'New Concurrent Denial',
    'Change of Employer Denial',
    'Amended Denial',
]


def parse_uscis_hub_file(file_path: str) -> dict:
    log.info(f'[parseUSCISHub] streaming {file_path}...')
    start = now_ms()

    employer_agg: dict = {}

    processed = 0
    skipped = 0
    row_index = 0

    col_fiscal_year = -1
    col_employer_name = -1
    col_approvals: list = []
    col_denials: list = []

    for values in iter_xlsx_rows(file_path):
        row_index += 1

        if row_index == 1:
            # Headers have trailing spaces ("Fiscal Year   "); read_header_row() trims them.
            headers = read_header_row(values)

            col_fiscal_year = index_of(headers, 'Fiscal Year')
            col_employer_name = index_of(headers, 'Employer (Petitioner) Name')

            for name in APPROVAL_COLUMNS:
                col_approvals.append(index_of(headers, name))
            for name in DENIAL_COLUMNS:
                col_denials.append(index_of(headers, name))

            if col_employer_name == -1:
                log.warn(
                    "[parseUSCISHub] WARNING: 'Employer (Petitioner) Name' column not found. "
                    f'Found headers: {", ".join(h for h in headers if h)}. '
                    'USCIS may have changed the export layout -- re-verify column names.'
                )
            if any(c == -1 for c in col_approvals) or any(c == -1 for c in col_denials):
                log.warn(
                    '[parseUSCISHub] WARNING: one or more approval/denial columns not found. '
                    f'Expected: {", ".join(APPROVAL_COLUMNS + DENIAL_COLUMNS)}. '
                    f'Found headers: {", ".join(h for h in headers if h)}.'
                )
            continue

        try:
            employer_name = cell(values, col_employer_name)

            if not truthy(employer_name) or not js_trim(js_string(employer_name)):
                # Skip blank rows and rows with a hidden (blank) employer name.
                skipped += 1
                continue

            norm_key = normalize_company(js_string(employer_name))
            if not norm_key:
                skipped += 1
                continue

            approvals = 0
            for col in col_approvals:
                if col >= 0:
                    approvals += js_or(js_number(cell(values, col)), 0)
            denials = 0
            for col in col_denials:
                if col >= 0:
                    denials += js_or(js_number(cell(values, col)), 0)

            year_raw = cell(values, col_fiscal_year) if col_fiscal_year >= 0 else None
            year = js_parse_int(js_string(year_raw)) if truthy(year_raw) else current_local_year()

            entry = employer_agg.get(norm_key) or {
                'companyName': js_trim(js_string(employer_name)),
                'approvals': 0,
                'denials': 0,
                'lastYear': 0,
            }

            entry['approvals'] += approvals
            entry['denials'] += denials
            if year > entry['lastYear']:
                entry['lastYear'] = year

            employer_agg[norm_key] = entry
            processed += 1
        except Exception:
            # Never crash on a single bad row.
            skipped += 1

        if processed > 0 and processed % 25000 == 0:
            log.info(f'[parseUSCISHub] ...{processed} real rows processed so far')

    elapsed = to_fixed((now_ms() - start) / 1000, 1)
    log.info(f'[parseUSCISHub] streamed {row_index} total rows ({elapsed}s), processed {processed}, '
             f'skipped {skipped}. Unique employers: {len(employer_agg)}')

    if len(employer_agg) == 0:
        log.warn(
            '[parseUSCISHub] WARNING: 0 employers extracted. Check that column '
            "names match this file's actual headers -- see the warning above for "
            'what was actually found.'
        )
        # Nothing parsed -- leave the previous load's numbers alone.
        return {'processed': processed, 'skipped': skipped, 'employers': 0}

    # Replace, don't add: clear approvals / denials first.
    reset = db.query(SQL.visaHistory.resetUscisFields)
    log.info(f'[parseUSCISHub] cleared previous approval/denial numbers on {reset.rowcount} companies')

    written = 0
    for entry in employer_agg.values():
        upsert_company_visa(
            entry['companyName'],
            approvals=entry['approvals'],
            denials=entry['denials'],
            year=entry['lastYear'],
            # Deliberately NOT setting h1b_filings -- see the module doc.
        )
        written += 1

    log.info(f'[parseUSCISHub] wrote {written} companies to company_visa_history')
    return {'processed': processed, 'skipped': skipped, 'employers': written}
