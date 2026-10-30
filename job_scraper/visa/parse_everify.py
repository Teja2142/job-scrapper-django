"""
Loads the E-Verify employer list into company_visa_history.everify_enrolled.

E-Verify.gov only lets you download the list in parts (e.g. Open accounts and Terminated accounts
separately), so every file in data/everify is read and the result is worked out per company:
  TRUE  = at least one Open account (and its end date, if any, is still in the future)
  FALSE = only Terminated accounts
  NULL  = not in the list. Not being listed doesn't mean "not enrolled": many companies are
          registered under a parent name while their H-1B filings use a subsidiary's name.

Before matching, account IDs at the end of names ("Cynet Systems 2580603") and parent wording
("... and Integrated Subsidiaries") are removed. "Doing business as" names only update companies
that already exist.

The website's "Download Results" file is UTF-16 and tab-separated even though it ends in .csv.
Normal UTF-8 .csv and .xlsx / .xls files work too.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass

from django.db import transaction

from ..core import conf, db, log
from ..core.jscompat import js_string, js_trim, jsre, local_datetime, now, parse_js_date, truthy
from ..matcher.normalize_company import normalize_company
from ..core.sql import SQL
from .excel_utils import get_field, read_excel_rows

WRITE_BATCH_SIZE = 5000
MAX_NAME_LENGTH = 300  # company_name / company_name_norm are VARCHAR(300)

EMPLOYER_FIELDS = ['Employer', 'Employer Name', 'Company Name', 'EMPLOYER_NAME', 'COMPANY_NAME']
DBA_FIELDS = ['Doing Business As', 'DBA', 'DOING_BUSINESS_AS']
STATUS_FIELDS = ['Account Status', 'ACCOUNT_STATUS', 'Status', 'STATUS']
TERMINATED_DATE_FIELDS = ['Date Terminated', 'DATE_TERMINATED']


@dataclass
class CompanyStatus:
    name: str                 # display name (from the Employer column when available)
    open: bool                # has at least one currently Open account
    fromEmployerColumn: bool  # seen as a legal Employer name (may create a row)


@dataclass
class Totals:
    rows: int = 0
    skipped: int = 0
    openWithPastEndDate: int = 0


def parse_everify_files(file_paths: list) -> dict:
    companies: dict = {}
    totals = Totals()
    today = now()

    for file_path in file_paths:
        ext = os.path.splitext(file_path)[1].lower()
        before = totals.rows
        if ext in ('.xlsx', '.xls'):
            _read_excel(file_path, companies, totals, today)
        else:
            _read_csv(file_path, companies, totals, today)
        log.info(f'[parseEVerify] {os.path.basename(file_path)}: {totals.rows - before} rows read')

    log.info(
        f'[parseEVerify] {totals.rows} rows from {len(file_paths)} file(s) -> {len(companies)} companies '
        f'(skipped {totals.skipped} rows; {totals.openWithPastEndDate} Open rows had an end date already passed, counted as Terminated)'
    )

    if len(companies) == 0:
        log.warn('[parseEVerify] WARNING: no companies read -- check the column names. Leaving existing E-Verify data unchanged.')
        return {'processed': totals.rows, 'skipped': totals.skipped, 'companies': 0}

    _write_companies(companies)
    return {'processed': totals.rows, 'skipped': totals.skipped, 'companies': len(companies)}


_ACCOUNT_ID = jsre(r'[\s\-–(#]*(?:coid\s*)?#?\d{5,}\)?\s*$', 'i')


def strip_account_id(name: str) -> str:
    """
    Removes a trailing account / company ID:
      "Cynet Systems 2580603"                      -> "Cynet Systems"
      "Graspen Learning Centers LLC - COID 901687" -> "Graspen Learning Centers LLC"
      "Compass Group - Bon Appetit (15355)"        -> "Compass Group - Bon Appetit"
    Only 5+ digit numbers are removed, so "Studio 54" stays as it is.
    """
    stripped = js_trim(_ACCOUNT_ID.sub('', name, count=1))
    return stripped or js_trim(name)


# "... and its subsidiaries", "... and certain affiliates", "... subsidiaries and affiliates".
# Needs "and", "&" or "," in front, so a real name like "Smith & Affiliates" is kept.
PARENT_WORDING_PATTERNS = [
    jsre(r'\s*(?:,|\band\b|&)\s*(?:its\s+|all\s+|certain\s+)?(?:(?:integrated|operating|wholly[- ]owned|domestic|u\.?s\.?|affiliated)\s+)?subsidiaries\b.*$', 'i'),
    jsre(r'\s*(?:,|\band\b|&)\s*(?:its|all|certain)\s+affiliates\b.*$', 'i'),
    jsre(r'\s+subsidiaries\s+(?:and|&)\s+affiliates\b.*$', 'i'),
]


def clean_everify_name(name: str) -> str:
    """Account ID removed, then parent-company wording removed (see above)."""
    cleaned = strip_account_id(name)
    for pattern in PARENT_WORDING_PATTERNS:
        cleaned = pattern.sub('', cleaned, count=1)
    cleaned = js_trim(cleaned)
    return cleaned or js_trim(name)


_US_DATE = jsre(r'^(\d{1,2})\/(\d{1,2})\/(\d{4})$')


def _parse_us_date(raw):
    from datetime import datetime
    if isinstance(raw, datetime):
        # Excel date cells are read as local time
        return parse_js_date(raw)
    s = js_trim(js_string('' if raw is None else raw))
    if not s:
        return None
    m = _US_DATE.search(s)  # M/D/YYYY, as in the export
    if m:
        return local_datetime(int(m.group(3)), int(m.group(1)) - 1, int(m.group(2)))
    return parse_js_date(s)


def _is_currently_open(raw_status, raw_terminated, today, totals: Totals):
    """
    Is this account enrolled right now? Open counts unless its end date has passed. Any other status
    returns None and the row is skipped.
    """
    status = js_trim(js_string('' if raw_status is None else raw_status)).lower()
    if status == 'terminated':
        return False
    if status != 'open':
        return None

    end_date = _parse_us_date(raw_terminated)
    if end_date and end_date <= today:
        totals.openWithPastEndDate += 1
        return False
    return True


def _add_name(companies: dict, raw_name, from_employer_column: bool, is_open: bool) -> None:
    raw = js_trim(js_string('' if raw_name is None else raw_name))
    if not raw:
        return

    name = clean_everify_name(raw)[:MAX_NAME_LENGTH]
    norm = normalize_company(name)
    if not norm or len(norm) > MAX_NAME_LENGTH:
        return

    existing = companies.get(norm)
    if not existing:
        companies[norm] = CompanyStatus(name=name, open=is_open, fromEmployerColumn=from_employer_column)
        return
    existing.open = existing.open or is_open
    if from_employer_column and not existing.fromEmployerColumn:
        existing.fromEmployerColumn = True
        existing.name = name  # prefer the legal Employer name for display


def _add_row(companies: dict, employer, dba, status, terminated, today, totals: Totals) -> None:
    totals.rows += 1
    is_open = _is_currently_open(status, terminated, today, totals)
    if not js_trim(js_string('' if employer is None else employer)) or is_open is None:
        totals.skipped += 1
        return
    _add_name(companies, employer, True, is_open)
    _add_name(companies, dba, False, is_open)


def _pick(row: dict, fields: list):
    for f in fields:
        v = row.get(f)
        if v is not None and v != '':
            return v
    return None


def _read_excel(file_path: str, companies: dict, totals: Totals, today) -> None:
    log.info(f'[parseEVerify] reading Excel file {file_path}...')
    for row in read_excel_rows(file_path):
        _add_row(
            companies,
            get_field(row, EMPLOYER_FIELDS),
            get_field(row, DBA_FIELDS),
            get_field(row, STATUS_FIELDS),
            get_field(row, TERMINATED_DATE_FIELDS),
            today,
            totals,
        )


# The characters csv-parse's `trim` option removes around a field
_CSV_TRIM = ' \t\r\n\x0c'


def _read_csv(file_path: str, companies: dict, totals: Totals, today) -> None:
    """
    Reads the file line by line (the full export is about 180 MB), so memory stays low. Encoding and
    delimiter are detected from the file itself, not the extension.
    """
    encoding, delimiter = _detect_format(file_path)
    log.info(
        f"[parseEVerify] reading {file_path} (encoding={'utf16le' if encoding == 'utf-16-le' else 'utf8'}, "
        f"delimiter={'tab' if delimiter == chr(9) else 'comma'})..."
    )

    bom = jsre(r'^﻿')
    with open(file_path, 'r', encoding=encoding, errors='replace', newline='') as f:
        reader = csv.reader(f, delimiter=delimiter, skipinitialspace=True)
        columns = None
        for fields in reader:
            # skip_empty_lines
            if len(fields) == 0 or (len(fields) == 1 and js_trim(fields[0]) == ''):
                continue
            fields = [v.strip(_CSV_TRIM) for v in fields]  # trim: true
            if columns is None:
                columns = [js_trim(bom.sub('', h, count=1)) for h in fields]
                continue
            row = {}
            for i, col in enumerate(columns):
                if i < len(fields):
                    row[col] = fields[i]
            _add_row(
                companies,
                _pick(row, EMPLOYER_FIELDS),
                _pick(row, DBA_FIELDS),
                _pick(row, STATUS_FIELDS),
                _pick(row, TERMINATED_DATE_FIELDS),
                today,
                totals,
            )
            if totals.rows % 250000 == 0:
                log.info(f'[parseEVerify] ...{totals.rows} rows read')


def _detect_format(file_path: str):
    with open(file_path, 'rb') as f:
        buf = f.read(64 * 1024)

    is_utf16le = len(buf) >= 2 and buf[0] == 0xFF and buf[1] == 0xFE
    encoding = 'utf-16-le' if is_utf16le else 'utf-8'

    if is_utf16le:
        text = buf[: len(buf) - (len(buf) % 2)].decode('utf-16-le', errors='replace')
    else:
        text = buf.decode('utf-8', errors='replace')
    newline_idx = text.find('\n')
    first_line = text if newline_idx == -1 else text[:newline_idx]
    tab_count = first_line.count('\t')
    comma_count = first_line.count(',')

    return encoding, '\t' if tab_count > comma_count else ','


def _write_companies(companies: dict) -> None:
    with transaction.atomic(using=conf.db_alias()):
        # Everyone starts as "unknown"; only the list decides TRUE / FALSE.
        reset = db.query(SQL.visaHistory.resetEverify)
        log.info(f'[parseEVerify] reset E-Verify status to unknown on {reset.rowcount} companies')

        # Legal Employer names: create the company if new, else set its status.
        batch = {'names': [], 'norms': [], 'flags': []}
        upserted = 0

        def flush_employers():
            nonlocal upserted
            if len(batch['norms']) == 0:
                return
            db.query(SQL.visaHistory.upsertEverifyBatch, [batch['names'], batch['norms'], batch['flags']])
            upserted += len(batch['norms'])
            batch['names'], batch['norms'], batch['flags'] = [], [], []
            if upserted % 100000 < WRITE_BATCH_SIZE:
                log.info(f'[parseEVerify] ...{upserted} companies written')

        for norm, c in companies.items():
            if not c.fromEmployerColumn:
                continue
            batch['names'].append(c.name)
            batch['norms'].append(norm)
            batch['flags'].append(c.open)
            if len(batch['norms']) >= WRITE_BATCH_SIZE:
                flush_employers()
        flush_employers()

        # "Doing business as" names run after the legal names and only update existing companies.
        # They never turn TRUE back into FALSE.
        dba = {'norms': [], 'flags': []}
        dba_updated = 0

        def flush_dba():
            nonlocal dba_updated
            if len(dba['norms']) == 0:
                return
            r = db.query(SQL.visaHistory.updateEverifyExistingBatch, [dba['norms'], dba['flags']])
            dba_updated += r.rowcount
            dba['norms'], dba['flags'] = [], []

        for norm, c in companies.items():
            if c.fromEmployerColumn:
                continue
            dba['norms'].append(norm)
            dba['flags'].append(c.open)
            if len(dba['norms']) >= WRITE_BATCH_SIZE:
                flush_dba()
        flush_dba()

    log.info(
        f'[parseEVerify] wrote {upserted} companies by legal name; '
        f'{dba_updated} existing companies matched by "Doing Business As" name'
    )
