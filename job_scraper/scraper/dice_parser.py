from __future__ import annotations

from ..core.jscompat import js_or, js_string, js_trim, jsre, truthy
from ..matcher.normalize_company import normalize_company
from .job_parser import NormalizedJob, get
from .location_parser import ParsedLocation, parse_location, state_code_from_text, state_name_from_code
from .parser_utils import make_content_hash, normalize_employment_type, normalize_salary_period, parse_date, parse_money

_USA_SUFFIX = jsre(r',\s*(USA|US|United States)\s*$', 'i')


def parse_dice_location(raw) -> ParsedLocation:
    """Dice locations: "Austin, Texas, USA", "Illinois, USA" (state only) or "Remote"."""
    if not truthy(raw):
        return parse_location(raw)
    text = js_trim(_USA_SUFFIX.sub('', js_string(raw), count=1))
    loc = parse_location(text)
    if not loc.stateCode and ',' not in text:
        code = state_code_from_text(text)
        if code:
            loc.city = None
            loc.stateCode = code
            loc.state = state_name_from_code(code)
    return loc


def parse_dice_row(raw, source: str = 'dice') -> NormalizedJob | None:
    """One row from solidcode/dice-scraper. Closed postings (isExpired) are dropped."""
    title = get(raw, 'title')
    company = get(raw, 'company')
    if not truthy(title) or not truthy(company):
        return None
    if get(raw, 'isExpired') is True:
        return None

    job_url = js_or(get(raw, 'jobUrl'), None)
    native_id = js_or(get(raw, 'jobId'), job_url, f'{js_string(title)}-{js_string(company)}')
    job_id = f'{source}_{js_string(native_id)}'

    loc = parse_dice_location(get(raw, 'location'))

    workplace_raw = get(raw, 'workplaceTypes')
    workplace = [js_string(w).lower() for w in workplace_raw] if isinstance(workplace_raw, list) else []
    wfh_raw = get(raw, 'workFromHomeAvailability')
    wfh = js_string('' if wfh_raw is None else wfh_raw).lower()

    is_remote = get(raw, 'isRemote') is True or wfh == 'yes' or 'remote' in workplace or loc.isRemote
    is_hybrid = wfh == 'hybrid' or 'hybrid' in workplace or loc.isHybrid

    salary_min = parse_money(get(raw, 'salaryMin'))
    salary_max = parse_money(get(raw, 'salaryMax'))
    salary_present = salary_min is not None or salary_max is not None
    salary_period = normalize_salary_period(get(raw, 'salaryPeriod')) if salary_present else None

    emp_types = get(raw, 'employmentTypes')
    emp_types_len = len(emp_types) if isinstance(emp_types, (list, str)) else None

    return NormalizedJob(
        jobId=job_id,
        contentHash=make_content_hash(js_string(title), js_string(company), loc.city, loc.stateCode),
        title=js_string(title)[:500],
        companyName=js_string(company)[:300],
        companyNameNorm=normalize_company(company),
        jobUrl=job_url,
        jobDescription=js_or(get(raw, 'description'), get(raw, 'summary'), None),
        locationRaw=js_or(get(raw, 'location'), None),
        city=loc.city,
        state=loc.state,
        stateCode=loc.stateCode,
        isRemote=is_remote,
        isHybrid=is_hybrid,
        salaryMin=salary_min,
        salaryMax=salary_max,
        salaryPeriod=salary_period,
        salaryPresent=salary_present,
        employmentType=normalize_employment_type(emp_types if truthy(emp_types_len) else get(raw, 'employmentType')),
        source=source,
        # Not modifiedDate: an old job that was only edited would look fresh.
        postingDate=parse_date(get(raw, 'postedDate')),
    )
