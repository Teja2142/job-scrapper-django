"""
Loads a DOL PERM (green card) disclosure .xlsx file into company_visa_history. Read row by row
like parse_lca.py. The employer column is EMP_BUSINESS_NAME (files before June 2023 used
EMPLOYER_NAME, which is still accepted).
"""
from __future__ import annotations

from ..core import log
from ..core.jscompat import current_local_year, js_string, js_trim, local_year, now_ms, to_fixed, truthy
from ..matcher.normalize_company import normalize_company
from .build_visa_history import upsert_company_visa
from .excel_utils import cell, index_of, iter_xlsx_rows, read_header_row, resolve_excel_date


def parse_perm_file(file_path: str) -> dict:
    log.info(f'[parsePERM] streaming {file_path}...')
    start = now_ms()

    employer_agg: dict = {}

    processed = 0
    skipped = 0
    row_index = 0

    col_employer_name = -1
    col_decision_date = -1

    for values in iter_xlsx_rows(file_path):
        row_index += 1

        if row_index == 1:
            headers = read_header_row(values)

            # Newer files use EMP_BUSINESS_NAME, older ones EMPLOYER_NAME.
            col_employer_name = index_of(headers, 'EMP_BUSINESS_NAME')
            if col_employer_name == -1:
                col_employer_name = index_of(headers, 'EMPLOYER_NAME')
            col_decision_date = index_of(headers, 'DECISION_DATE')

            if col_employer_name == -1:
                log.warn(
                    '[parsePERM] WARNING: neither EMP_BUSINESS_NAME nor EMPLOYER_NAME '
                    f'column found. Found headers: {", ".join(h for h in headers if h)}. '
                    'Check the PERM Record Layout PDF for this fiscal year — column '
                    'names may have changed again.'
                )
            continue

        try:
            employer_name = cell(values, col_employer_name)

            if not truthy(employer_name):
                continue  # skip blank padding rows
            if not js_trim(js_string(employer_name)):
                skipped += 1
                continue

            norm_key = normalize_company(js_string(employer_name))
            if not norm_key:
                skipped += 1
                continue

            decision_date_raw = cell(values, col_decision_date)

            year = current_local_year()
            parsed_date = resolve_excel_date(decision_date_raw)
            if parsed_date:
                year = local_year(parsed_date)

            entry = employer_agg.get(norm_key) or {
                'companyName': js_trim(js_string(employer_name)),
                'filings': 0,
                'lastYear': 0,
            }

            entry['filings'] += 1
            if year > entry['lastYear']:
                entry['lastYear'] = year

            employer_agg[norm_key] = entry
            processed += 1
        except Exception:
            skipped += 1

    elapsed = to_fixed((now_ms() - start) / 1000, 1)
    log.info(f'[parsePERM] streamed {row_index} total rows ({elapsed}s), processed {processed}, '
             f'skipped {skipped}. Unique employers: {len(employer_agg)}')

    if len(employer_agg) == 0:
        log.warn(
            '[parsePERM] WARNING: 0 employers extracted. Check column names against '
            "the actual file's headers using log_column_headers_streaming() from excel_utils.py."
        )

    written = 0
    for entry in employer_agg.values():
        upsert_company_visa(entry['companyName'], perm_filings=entry['filings'], year=entry['lastYear'])
        written += 1

    log.info(f'[parsePERM] wrote {written} companies to company_visa_history')
    return {'processed': processed, 'skipped': skipped, 'employers': written}
