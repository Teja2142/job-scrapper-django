from __future__ import annotations

from datetime import timedelta

from ..core.jscompat import is_finite, js_number, js_or, js_round, js_string, js_trim, jsre, truthy
from ..matcher.normalize_company import normalize_company
from .job_parser import NormalizedJob, get
from .location_parser import parse_location
from .parser_utils import make_content_hash, parse_date

_ID_IN_URL = jsre(r'/jobs/info/([a-f0-9]+)', 'i')
_MORE_SUFFIX = jsre(r'\+\d+\s*more$', 'i')


def parse_jobright_row(raw, source: str = 'jobright') -> NormalizedJob | None:
    """
    One row from the JobRight actor. Its salary, description and job type fields are empty, so salary
    and job type are taken from the requirements / benefits lists. Those lists are mixed with page
    text, so no description is saved. postedDate is relative ("3 minutes ago") to scrapedAt.
    """
    title = get(raw, 'jobTitle')
    company = get(raw, 'companyName')
    if not truthy(title) or not truthy(company):
        return None

    job_url = js_or(get(raw, 'applyUrl'), None)
    # The id is in the URL path: .../jobs/info/<id>
    id_match = _ID_IN_URL.search(job_url) if isinstance(job_url, str) else None
    native_id = js_or(id_match.group(1) if id_match else None, job_url, f'{js_string(title)}-{js_string(company)}')
    job_id = f'{source}_{js_string(native_id)}'

    # "Charlotte, NC+3 more" -> "Charlotte, NC"
    location = get(raw, 'location')
    clean_location = js_trim(_MORE_SUFFIX.sub('', location, count=1)) if isinstance(location, str) else location
    loc = parse_location(clean_location)

    req = get(raw, 'requirements')
    ben = get(raw, 'benefits')
    bag = (list(req) if isinstance(req, list) else []) + (list(ben) if isinstance(ben, list) else [])

    salary_min, salary_max, salary_period = _extract_salary(bag)
    salary_present = salary_min is not None or salary_max is not None

    employment_type = _extract_first_token(bag, EMPLOYMENT_TOKENS)
    work_mode = _extract_first_token(bag, ['Remote', 'Hybrid', 'Onsite'])
    is_remote = work_mode == 'Remote' or loc.isRemote
    is_hybrid = work_mode == 'Hybrid' or loc.isHybrid

    return NormalizedJob(
        jobId=job_id,
        contentHash=make_content_hash(js_string(title), js_string(company), loc.city, loc.stateCode),
        title=js_string(title)[:500],
        companyName=js_string(company)[:300],
        companyNameNorm=normalize_company(company),
        jobUrl=job_url,
        jobDescription=None,
        locationRaw=js_or(location, None),
        city=loc.city,
        state=loc.state,
        stateCode=loc.stateCode,
        isRemote=is_remote,
        isHybrid=is_hybrid,
        salaryMin=salary_min,
        salaryMax=salary_max,
        salaryPeriod=salary_period,
        salaryPresent=salary_present,
        employmentType=employment_type.lower() if employment_type else None,
        source=source,
        postingDate=_parse_relative_date(get(raw, 'postedDate'), get(raw, 'scrapedAt')),
    )


EMPLOYMENT_TOKENS = ['Full-time', 'Part-time', 'Contract', 'Internship', 'Temporary', 'Contract-to-hire']


def _extract_first_token(bag: list, tokens: list):
    """First array entry that is an exact (case-insensitive) match for one of `tokens`, or None."""
    lower_tokens = {t.lower() for t in tokens}
    for item in bag:
        if isinstance(item, str) and js_trim(item).lower() in lower_tokens:
            return js_trim(item)
    return None


_SALARY_RANGE = jsre(r'\$([\d,]+(?:\.\d+)?)\s*(K)?\s*\/\s*(hr|yr)\s*-\s*\$([\d,]+(?:\.\d+)?)\s*(K)?\s*\/\s*(hr|yr)', 'i')


def _extract_salary(bag: list):
    """Finds a "$X/hr - $Y/hr" or "$XK/yr - $YK/yr" range. No match -> all None."""
    for item in bag:
        if not isinstance(item, str):
            continue
        m = _SALARY_RANGE.search(item)
        if not m:
            continue

        def to_num(num_str, k_flag):
            n = js_number(num_str.replace(',', ''))
            return n * 1000 if k_flag else n

        mn = to_num(m.group(1), m.group(2))
        mx = to_num(m.group(4), m.group(5))
        period = 'yearly' if m.group(3).lower() == 'yr' else 'hourly'
        if is_finite(mn) and is_finite(mx):
            return js_round(mn), js_round(mx), period
    return None, None, None


_RELATIVE = jsre(r'^(\d+)\s*(minute|hour|day|week|month)s?\s*ago$')
_MS_PER = {
    'minute': 60_000, 'hour': 3_600_000, 'day': 86_400_000, 'week': 7 * 86_400_000, 'month': 30 * 86_400_000,
}


def _parse_relative_date(relative, scraped_at):
    """"3 minutes ago" / "2 hours ago" / "just now" -> a datetime, relative to scrapedAt."""
    base = parse_date(scraped_at)
    if not base or not isinstance(relative, str):
        return None
    text = js_trim(relative).lower()
    if text == 'just now':
        return base
    m = _RELATIVE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    try:
        return base - timedelta(milliseconds=n * _MS_PER[m.group(2)])
    except OverflowError:
        return None
