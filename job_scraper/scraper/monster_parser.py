from __future__ import annotations

from ..core.jscompat import js_or, js_string, js_trim, truthy
from ..matcher.normalize_company import normalize_company
from .job_parser import NormalizedJob, get
from .location_parser import parse_location, state_name_from_code
from .parser_utils import make_content_hash, normalize_employment_type, normalize_salary_period, parse_date, parse_money


def parse_monster_row(raw, source: str = 'monster') -> NormalizedJob | None:
    """
    One row from the Monster actor (needs fetchDetails: true, otherwise company and location are
    empty). For a repost, publishDate is the first posting date and originalPublishDate the latest
    repost, despite the name. We take the newer one.
    """
    title = get(raw, 'title')
    company = get(raw, 'company')
    if not truthy(title) or not truthy(company):
        return None

    job_url = js_or(get(raw, 'url'), get(raw, 'applyUrl'), None)
    native_id = js_or(get(raw, 'id'), job_url, f'{js_string(title)}-{js_string(company)}')
    job_id = f'{source}_{js_string(native_id)}'

    loc = parse_location(get(raw, 'location'))
    city = js_or(get(raw, 'city'), loc.city)
    raw_state = js_trim(js_string(get(raw, 'stateCode'))).upper() if truthy(get(raw, 'stateCode')) else None
    state_name = state_name_from_code(raw_state)
    state_code = raw_state if state_name else loc.stateCode
    state = state_name if state_name is not None else loc.state

    is_remote = get(raw, 'isRemote') is True or loc.isRemote
    is_hybrid = loc.isHybrid

    salary_min = parse_money(get(raw, 'salaryMin'))
    salary_max = parse_money(get(raw, 'salaryMax'))
    salary_present = salary_min is not None or salary_max is not None
    salary_period = normalize_salary_period(get(raw, 'salaryPeriod')) if salary_present else None

    publish_date = js_or(parse_date(get(raw, 'publishDateISO')), parse_date(get(raw, 'publishDate')))
    original_publish_date = parse_date(get(raw, 'originalPublishDate'))
    if original_publish_date and publish_date:
        posting_date = original_publish_date if original_publish_date > publish_date else publish_date
    else:
        posting_date = js_or(original_publish_date, publish_date)

    return NormalizedJob(
        jobId=job_id,
        contentHash=make_content_hash(js_string(title), js_string(company), city, state_code),
        title=js_string(title)[:500],
        companyName=js_string(company)[:300],
        companyNameNorm=normalize_company(company),
        jobUrl=job_url,
        jobDescription=js_or(get(raw, 'descriptionText'), get(raw, 'descriptionMarkdown'), get(raw, 'descriptionHtml'), None),
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
        employmentType=normalize_employment_type(get(raw, 'workTypes')),
        source=source,
        postingDate=posting_date,
    )
