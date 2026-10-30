from __future__ import annotations

from ..core.jscompat import js_number, js_or, js_round, js_string, truthy
from ..matcher.normalize_company import normalize_company
from .job_parser import NormalizedJob, get
from .location_parser import parse_location
from .parser_utils import make_content_hash, parse_date


def parse_multi_job_board_row(raw, source: str) -> NormalizedJob | None:
    """
    One row from flash_scraper/multi-jobboard-scraper (LinkedIn, Indeed, Glassdoor and the
    remote boards). Every row has the same fixed columns; a missing value is null.
    """
    title = get(raw, 'title')
    company = get(raw, 'company')
    if not truthy(title) or not truthy(company):
        return None

    job_url = js_or(get(raw, 'job_url'), get(raw, 'job_url_direct'), None)
    native_id = js_or(get(raw, 'id'), job_url, f'{js_string(title)}-{js_string(company)}')
    loc = parse_location(get(raw, 'location'))

    salary_min = js_round(js_number(get(raw, 'salary_min'))) if get(raw, 'salary_min') is not None else None
    salary_max = js_round(js_number(get(raw, 'salary_max'))) if get(raw, 'salary_max') is not None else None
    salary_present = salary_min is not None or salary_max is not None

    return NormalizedJob(
        jobId=f'{source}_{js_string(native_id)}',
        contentHash=make_content_hash(js_string(title), js_string(company), loc.city, loc.stateCode),
        title=js_string(title)[:500],
        companyName=js_string(company)[:300],
        companyNameNorm=normalize_company(company),
        jobUrl=job_url,
        jobDescription=js_or(get(raw, 'description'), None),
        locationRaw=js_or(get(raw, 'location'), None),
        city=loc.city,
        state=loc.state,
        stateCode=loc.stateCode,
        isRemote=get(raw, 'is_remote') is True or loc.isRemote,
        # Only Indeed sends work_from_home_type; other boards fall back to the location text.
        isHybrid=get(raw, 'work_from_home_type') == 'Hybrid' or loc.isHybrid,
        salaryMin=salary_min,
        salaryMax=salary_max,
        salaryPeriod=js_or(get(raw, 'salary_interval'), 'yearly') if salary_present else None,
        salaryPresent=salary_present,
        employmentType=js_or(get(raw, 'job_type'), None),
        source=source,
        postingDate=js_or(parse_date(get(raw, 'date_posted')), parse_date(get(raw, 'first_seen_on_board'))),
    )
