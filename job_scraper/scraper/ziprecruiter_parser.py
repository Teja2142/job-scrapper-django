from __future__ import annotations

from ..core.jscompat import js_or, js_string, truthy
from ..matcher.normalize_company import normalize_company
from .job_parser import NormalizedJob, get
from .location_parser import state_code_from_text, state_name_from_code
from .parser_utils import make_content_hash, normalize_employment_type, normalize_salary_period, parse_date, parse_money


def parse_ziprecruiter_row(raw, source: str = 'ziprecruiter') -> NormalizedJob | None:
    """
    One row from memo23/ziprecruiter-scraper. Filler cards have no company and are dropped;
    obviously corrupted salaries are removed by _cap_salary().
    """
    title = get(raw, 'title')
    company = get(raw, 'company')
    if not truthy(title) or not truthy(company):
        return None

    job_url = js_or(get(raw, 'url'), get(raw, 'applyUrl'), None)
    native_id = js_or(get(raw, 'id'), job_url, f'{js_string(title)}-{js_string(company)}')
    job_id = f'{source}_{js_string(native_id)}'

    city = js_or(get(raw, 'city'), None)
    state_code = state_code_from_text(get(raw, 'state'))
    state = state_name_from_code(state_code)

    location_text = f"{js_string(js_or(get(raw, 'location'), ''))} {js_string(js_or(city, ''))}".lower()
    is_remote = get(raw, 'isRemote') is True or 'remote' in location_text
    is_hybrid = 'hybrid' in location_text

    raw_min = parse_money(get(raw, 'salaryMin'))
    raw_max = parse_money(get(raw, 'salaryMax'))
    period = normalize_salary_period(get(raw, 'salaryPeriod'))
    salary_min, salary_max = _cap_salary(raw_min, raw_max, period)
    salary_present = salary_min is not None or salary_max is not None
    salary_period = period if salary_present else None

    return NormalizedJob(
        jobId=job_id,
        contentHash=make_content_hash(js_string(title), js_string(company), city, state_code),
        title=js_string(title)[:500],
        companyName=js_string(company)[:300],
        companyNameNorm=normalize_company(company),
        jobUrl=job_url,
        jobDescription=js_or(get(raw, 'description'), get(raw, 'descriptionHtml'), None),
        locationRaw=js_or(get(raw, 'location'), None),
        city=city,
        state=state,
        stateCode=state_code,
        isRemote=is_remote,
        isHybrid=is_hybrid,
        salaryMin=salary_min,
        salaryMax=salary_max,
        salaryPeriod=salary_period,
        salaryPresent=salary_present,
        employmentType=normalize_employment_type(get(raw, 'employmentType')),
        source=source,
        postingDate=parse_date(get(raw, 'datePosted')),
    )


def _cap_salary(mn, mx, period):
    """Drops both values when either is above a generous ceiling for its pay period (e.g. $150k "/hour")."""
    ceilings = {'hourly': 1000, 'daily': 5000, 'weekly': 20000, 'monthly': 80000, 'yearly': 5_000_000}
    ceiling = ceilings.get(period) if period else None
    if not ceiling:
        return mn, mx
    if (mn is not None and mn > ceiling) or (mx is not None and mx > ceiling):
        return None, None
    return mn, mx
