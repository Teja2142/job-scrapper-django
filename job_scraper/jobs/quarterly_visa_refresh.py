from __future__ import annotations

import os

from ..core import conf, db, log
from ..core.jscompat import json_stringify, now, to_iso
from ..core.sql import SQL
from ..visa.build_visa_history import rescore_all_companies
from ..visa.parse_everify import parse_everify_files
from ..visa.parse_lca import parse_lca_file
from ..visa.parse_perm import parse_perm_file
from ..visa.parse_uscis_hub import parse_uscis_hub_file


def run_quarterly_visa_refresh() -> dict:
    """
    Loads the government files found in data/lca, data/perm, data/uscis-hub and data/everify into
    company_visa_history.

    Files are not downloaded automatically (the DOL links change every quarter). Download them by
    hand from https://www.dol.gov/agencies/eta/foreign-labor/performance and put them in those
    folders first. A missing file is skipped with a log message; the other steps still run.
    """
    data_dir = conf.data_dir()
    started = now()
    log.info(f'[quarterlyVisaRefresh] start {to_iso(started)}')

    results = {
        'lca': None,
        'perm': None,
        'uscisHub': None,
        'everify': None,
        'rescored': 0,
    }

    try:
        # Step 1: LCA (H-1B filings)
        lca_dir = _join(data_dir, 'lca')
        lca_file = find_first_data_file(lca_dir)
        if lca_file:
            results['lca'] = parse_lca_file(lca_file)
        else:
            log.info(f'[quarterlyVisaRefresh] No LCA file found in {lca_dir} — skipping. '
                     f'Download from https://www.dol.gov/agencies/eta/foreign-labor/performance and place it there.')

        # Step 2: PERM (green card)
        perm_dir = _join(data_dir, 'perm')
        perm_file = find_first_data_file(perm_dir)
        if perm_file:
            results['perm'] = parse_perm_file(perm_file)
        else:
            log.info(f'[quarterlyVisaRefresh] No PERM file found in {perm_dir} — skipping.')

        # Step 3: USCIS H-1B Employer Data Hub (real approvals / denials). Use the .xlsx export.
        uscis_hub_dir = _join(data_dir, 'uscis-hub')
        uscis_hub_file = find_first_data_file(uscis_hub_dir)
        if uscis_hub_file:
            results['uscisHub'] = parse_uscis_hub_file(uscis_hub_file)
        else:
            log.info(f'[quarterlyVisaRefresh] WARNING: No USCIS Hub file found in {uscis_hub_dir} — skipping. '
                     f'Without it h1b_approvals / h1b_denials are not refreshed (the LCA file no longer fills them). '
                     f'Download the .xlsx export from https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub and place it there.')

        # Step 4: E-Verify. All files in data/everify are read together, because the list is
        # downloaded in parts.
        everify_dir = _join(data_dir, 'everify')
        everify_files = find_all_data_files(everify_dir)
        if len(everify_files) > 0:
            log.info(f'[quarterlyVisaRefresh] E-Verify files: {", ".join(os.path.basename(f) for f in everify_files)}')
            results['everify'] = parse_everify_files(everify_files)
        else:
            log.info(f'[quarterlyVisaRefresh] No E-Verify file found in {everify_dir} — skipping.')

        # Step 5: recompute every company's score and grade from the loaded data.
        results['rescored'] = rescore_all_companies()

        log.info('[quarterlyVisaRefresh] summary:', json_stringify(results, indent=2))

        db.query(SQL.scrapingLog.insertVisaRefreshSuccess, [started])
    except Exception as e:
        log.error('[quarterlyVisaRefresh] failed:', str(e))
        db.query(SQL.scrapingLog.insertVisaRefreshFailed, [started, str(e)])

    log.info(f'[quarterlyVisaRefresh] done {to_iso(now())}')
    return results


def _join(*parts: str) -> str:
    """Joins and cleans up a path ('./data' + 'lca' -> 'data/lca')."""
    return os.path.normpath(os.path.join(*parts))


def find_all_data_files(directory: str) -> list:
    """All data files (.xlsx, .xls, .csv) in a directory, sorted by name."""
    if not os.path.exists(directory):
        return []
    names = [f for f in os.listdir(directory) if os.path.splitext(f)[1].lower() in ('.xlsx', '.xls', '.csv')]
    # same order as JavaScript's default sort
    names.sort(key=lambda f: f.encode('utf-16-be'))
    return [_join(directory, f) for f in names]


def find_first_data_file(directory: str) -> str | None:
    """The first data file (by name) in a directory, or None if there is none."""
    files = find_all_data_files(directory)
    return files[0] if files else None
