from __future__ import annotations

from ..core.jscompat import js_or, js_string, js_trim, jsre, truthy
from ..matcher.normalize_company import normalize_company
from .job_parser import NormalizedJob, get
from .location_parser import state_code_from_text, state_name_from_code
from .parser_utils import make_content_hash, normalize_employment_type, normalize_salary_period, parse_date, parse_money


def parse_careerbuilder_row(raw, source: str = 'careerbuilder') -> NormalizedJob | None:
    """
    One row from the CareerBuilder actor. Things it handles:
      - multi-location jobs come as "California, California, US" with the state as city: city dropped
      - state is either "MO" or "Florida"; both become the code
      - country is sometimes empty on US jobs; a valid US state is enough
      - salaryUnit is written many ways ("HOUR", "Per hour", "annually" ...)
      - employmentType is a list or null
      - many descriptions start with a tracking image, which is removed
    """
    if not isinstance(raw, dict):
        return None
    title = js_trim(raw['title']) if isinstance(raw.get('title'), str) else ''
    company = js_trim(raw['company']) if isinstance(raw.get('company'), str) else ''
    if not title or not company:
        return None

    job_url = js_or(get(raw, 'url'), get(raw, 'applyUrl'), None)
    native_id = js_or(get(raw, 'jobId'), job_url, f'{title}-{company}')
    job_id = f'{source}_{js_string(native_id)}'

    state_code = state_code_from_text(get(raw, 'state'))
    state = state_name_from_code(state_code)
    city_raw = get(raw, 'city')
    city = js_trim(city_raw) if isinstance(city_raw, str) and js_trim(city_raw) else None
    # "California, California, US": the "city" is the state itself (a multi-location posting).
    if city and (state_code_from_text(city) is not None or not state_code):
        city = None

    location_text = js_string(js_or(get(raw, 'location'), '')).lower()
    location_type = js_string(js_or(get(raw, 'jobLocationType'), '')).upper()
    is_remote = get(raw, 'isRemote') is True or location_type == 'REMOTE' or 'remote' in location_text
    is_hybrid = location_type == 'HYBRID' or 'hybrid' in location_text

    salary_min = parse_money(get(raw, 'salaryMin'))
    salary_max = parse_money(get(raw, 'salaryMax'))
    salary_present = salary_min is not None or salary_max is not None
    salary_period = normalize_salary_period(get(raw, 'salaryUnit')) if salary_present else None

    emp = get(raw, 'employmentType')
    employment_type = normalize_employment_type(emp) if isinstance(emp, (list, str)) else None

    return NormalizedJob(
        jobId=job_id,
        contentHash=make_content_hash(title, company, city, state_code),
        title=title[:500],
        companyName=company[:300],
        companyNameNorm=normalize_company(company),
        jobUrl=job_url,
        jobDescription=_clean_description(get(raw, 'description')),
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
        employmentType=employment_type,
        source=source,
        postingDate=parse_date(get(raw, 'datePosted')),
    )


_IMG = jsre(r'<img\b[^>]*>', 'i')


def _clean_description(html):
    """Removes <img> tags (tracking pixels) from the HTML description; empty -> None."""
    if not isinstance(html, str):
        return None
    cleaned = js_trim(_IMG.sub('', html))
    return cleaned or None
