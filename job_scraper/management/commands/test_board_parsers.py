"""
Offline check of every board parser, no Apify or database needed:
    python manage.py test_board_parsers
The sample rows follow each actor's documented fields.
"""
import sys

from ...core import log
from ...core.jscompat import json_stringify, to_iso
from ...scraper.job_parser import parse_job
from ...scraper.jobright_parser import parse_jobright_row
from ...scraper.parser_utils import title_matches_query
from ._base import ScriptCommand


def iso(d):
    return to_iso(d) if d is not None else None


def run_checks() -> int:
    failures = 0

    def check(name, actual, expected):
        nonlocal failures
        ok = json_stringify(actual) == json_stringify(expected)
        if not ok:
            failures += 1
        log.info(f"{'ok  ' if ok else 'FAIL'} {name}" + ('' if ok else f"\n     expected {json_stringify(expected)}\n     actual   {json_stringify(actual)}"))

    # Dice
    dice = parse_job({
      'jobId': '5b2d4f7c-9a3e-4d12-b7e8-1f0c2a9b8d54', 'title': 'Senior Data Engineer', 'company': 'Acme Cloud Systems',
      'location': 'Austin, Texas, USA', 'postedDate': '2026-09-20T00:02:43Z', 'salary': '$140,000 - $170,000 per year',
      'salaryMin': 140000, 'salaryMax': 170000, 'salaryCurrency': 'USD', 'salaryPeriod': 'year',
      'employmentType': 'Full-time, Contract', 'employmentTypes': ['Full-time', 'Contract'],
      'isRemote': False, 'workFromHomeAvailability': 'Hybrid', 'workplaceTypes': ['Hybrid'],
      'description': 'Build pipelines...', 'jobUrl': 'https:#www.dice.com/job-detail/5b2d4f7c', 'isExpired': False,
    }, 'dice')
    check('dice: job id', dice.jobId, 'dice_5b2d4f7c-9a3e-4d12-b7e8-1f0c2a9b8d54')
    check('dice: location', [dice.city, dice.state, dice.stateCode], ['Austin', 'Texas', 'TX'])
    check('dice: salary', [dice.salaryMin, dice.salaryMax, dice.salaryPeriod, dice.salaryPresent], [140000, 170000, 'yearly', True])
    check('dice: employment type', dice.employmentType, 'full-time, contract')
    check('dice: hybrid / remote', [dice.isHybrid, dice.isRemote], [True, False])
    check('dice: posting date', iso(dice.postingDate), '2026-09-20T00:02:43.000Z')
    check('dice: description + url', [dice.jobDescription, dice.jobUrl], ['Build pipelines...', 'https:#www.dice.com/job-detail/5b2d4f7c'])

    diceRemote = parse_job({ 'jobId': 'x1', 'title': 'Data Analyst', 'company': 'Foo Inc', 'location': 'Remote', 'isRemote': True,
      'workFromHomeAvailability': 'Yes', 'workplaceTypes': ['Remote'], 'salary': 'Depends on Experience', 'salaryMin': None, 'salaryMax': None,
      'salaryPeriod': None, 'postedDate': '2026-09-21T10:00:00Z', 'jobUrl': 'u', 'isExpired': False }, 'dice')
    check('dice remote: flags', [diceRemote.isRemote, diceRemote.isHybrid, diceRemote.city], [True, False, None])
    check('dice remote: no salary', [diceRemote.salaryMin, diceRemote.salaryPeriod, diceRemote.salaryPresent], [None, None, False])

    diceHourly = parse_job({ 'jobId': 'x2', 'title': 'Data Worker', 'company': 'Bar LLC', 'location': 'New York, New York, USA',
      'salaryMin': 60, 'salaryMax': 80, 'salaryPeriod': 'hour', 'postedDate': '2026-09-21T10:00:00Z' }, 'dice')
    check('dice hourly: period', [diceHourly.salaryMin, diceHourly.salaryMax, diceHourly.salaryPeriod], [60, 80, 'hourly'])
    check('dice hourly: state', diceHourly.stateCode, 'NY')
    diceState = parse_job({ 'jobId': 'x5', 'title': 'Data Analyst', 'company': 'Centuria Corp', 'location': 'Illinois, USA', 'postedDate': '2026-09-21T02:26:20Z' }, 'dice')
    check('dice: state-only location "Illinois, USA"', [diceState.city, diceState.state, diceState.stateCode], [None, 'Illinois', 'IL'])
    diceCityUsa = parse_job({ 'jobId': 'x6', 'title': 'Data Analyst', 'company': 'Guidehouse', 'location': 'Arlington, Virginia, USA', 'postedDate': '2026-09-21T11:22:16Z' }, 'dice')
    check('dice: "Arlington, Virginia, USA"', [diceCityUsa.city, diceCityUsa.state, diceCityUsa.stateCode], ['Arlington', 'Virginia', 'VA'])
    check('dice: expired row dropped', parse_job({ 'jobId': 'x3', 'title': 'T', 'company': 'C', 'isExpired': True }, 'dice'), None)
    check('dice: row without company dropped', parse_job({ 'jobId': 'x4', 'title': 'T' }, 'dice'), None)

    # Monster
    monster = parse_job({
      'id': '59a3ce42-767e-46a9-8494-f6b6a4635635', 'url': 'https:#www.monster.com/job-openings/x--59a3ce42', 'title': 'Data Analyst',
      'company': 'L3Harris', 'location': 'Rochester, NY', 'city': 'Rochester', 'stateCode': 'NY', 'country': 'US', 'workTypes': ['FULL_TIME'],
      'isRemote': False, 'salaryMin': '85000', 'salaryMax': '90,000', 'salaryCurrency': 'USD', 'salaryPeriod': 'year',
      'publishDate': '2026-09-20', 'publishDateISO': '2026-09-20', 'descriptionText': 'Analyze data...', 'descriptionHtml': '<p>Analyze data...</p>',
    }, 'monster')
    check('monster: job id', monster.jobId, 'monster_59a3ce42-767e-46a9-8494-f6b6a4635635')
    check('monster: location', [monster.city, monster.state, monster.stateCode], ['Rochester', 'New York', 'NY'])
    check('monster: salary (strings)', [monster.salaryMin, monster.salaryMax, monster.salaryPeriod], [85000, 90000, 'yearly'])
    check('monster: employment type', monster.employmentType, 'full-time')
    check('monster: posting date', iso(monster.postingDate), '2026-09-20T00:00:00.000Z')
    check('monster: description', monster.jobDescription, 'Analyze data...')

    monsterHourly = parse_job({ 'id': 'm2', 'title': 'Data Worker', 'company': 'Baz', 'location': 'Austin, TX', 'workTypes': ['CONTRACT', 'PART_TIME'],
      'salaryMin': '25', 'salaryMax': None, 'salaryPeriod': 'hour', 'publishDateISO': '2026-09-21', 'isRemote': True }, 'monster')
    check('monster hourly: salary', [monsterHourly.salaryMin, monsterHourly.salaryMax, monsterHourly.salaryPeriod, monsterHourly.salaryPresent], [25, None, 'hourly', True])
    check('monster: employment types', monsterHourly.employmentType, 'contract, part-time')
    check('monster: state from location text when stateCode is missing', [monsterHourly.stateCode, monsterHourly.state], ['TX', 'Texas'])
    check('monster: null-company row (fetchDetails off) dropped', parse_job({ 'id': 'm3', 'title': 'Data Analyst', 'company': None }, 'monster'), None)

    # Same job on two boards -> same content hash (the DB then flags the second one as a duplicate)
    a = parse_job({ 'jobId': 'd', 'title': 'Data Analyst', 'company': 'Foo Inc', 'location': 'Austin, Texas, USA' }, 'dice')
    b = parse_job({ 'id': 'm', 'title': 'Data Analyst', 'company': 'Foo Inc', 'location': 'Austin, TX', 'city': 'Austin', 'stateCode': 'TX' }, 'monster')
    check('same job on Dice and Monster shares a content hash', a.contentHash == b.contentHash, True)

    # CareerBuilder (rows from real runs, trimmed to the fields the parser reads)
    cb1 = parse_job({
      'jobId': '717f4c9b-0752-4cba-a20f-63bce4bd88c2', 'title': 'Senior Data Operations Analyst', 'company': 'TSR Consulting',
      'location': 'California, California, US', 'city': 'California', 'state': 'California', 'country': 'US',
      'employmentType': ['CONTRACTOR', 'TEMPORARY', 'FULL_TIME'], 'jobLocationType': None, 'isRemote': None,
      'salaryMin': 70, 'salaryMax': 80, 'salaryCurrency': 'USD', 'salaryUnit': 'HOUR', 'datePosted': '2026-09-22T12:28:36.874Z',
      'description': '<img src="https:#counter.adcourier.com/x.gif"><p><strong>Job Title</strong></p>',
      'url': 'https:#www.careerbuilder.com/job-details/senior-data-operations-analyst-california-md--717f4c9b',
      'applyUrl': 'https:#www.careerbuilder.com/job-details/x?mstr_dist=True',
    }, 'careerbuilder')
    check('careerbuilder: id / url', [cb1.jobId, cb1.jobUrl], ['careerbuilder_717f4c9b-0752-4cba-a20f-63bce4bd88c2', 'https:#www.careerbuilder.com/job-details/senior-data-operations-analyst-california-md--717f4c9b'])
    check('careerbuilder: multi-location "California, California" -> state only, no fake city', [cb1.city, cb1.stateCode, cb1.state], [None, 'CA', 'California'])
    check('careerbuilder: hourly salary', [cb1.salaryMin, cb1.salaryMax, cb1.salaryPeriod, cb1.salaryPresent], [70, 80, 'hourly', True])
    check('careerbuilder: employmentType array', cb1.employmentType, 'contractor, temporary, full-time')
    check('careerbuilder: tracking <img> removed from description', cb1.jobDescription, '<p><strong>Job Title</strong></p>')
    check('careerbuilder: posting date', iso(cb1.postingDate), '2026-09-22T12:28:36.874Z')

    cb2 = parse_job({
      'jobId': 'ce029853', 'title': 'ASSOCIATE DATA SCIENTIST ANALYST', 'company': 'BC Forward Asymbl',
      'location': 'Jacksonville, Florida, US', 'city': 'Jacksonville', 'state': 'Florida', 'country': 'US', 'url': 'u',
    }, 'careerbuilder')
    check('careerbuilder: full state name -> code, real city kept', [cb2.city, cb2.stateCode], ['Jacksonville', 'FL'])

    cb3 = parse_job({
      'jobId': 'ffd55e04', 'title': 'Junior Data Analyst', 'company': 'SynergisticIT', 'location': 'Chicago, IL',
      'city': 'Chicago', 'state': 'IL', 'country': None, 'salaryMin': 82000, 'salaryMax': 136000, 'salaryUnit': 'YEAR', 'url': 'u',
    }, 'careerbuilder')
    check('careerbuilder: null country still US when the state is valid', [cb3.city, cb3.stateCode, cb3.salaryPeriod], ['Chicago', 'IL', 'yearly'])

    cb4 = parse_job({
      'jobId': '59bdff9b', 'title': 'Senior Business Analyst', 'company': 'Falcon Smart IT', 'location': 'US', 'city': None, 'state': None,
      'jobLocationType': 'REMOTE', 'isRemote': True, 'url': 'u',
    }, 'careerbuilder')
    check('careerbuilder: remote job with no city/state', [cb4.isRemote, cb4.city, cb4.stateCode], [True, None, None])

    for unit, expected in [['Per hour', 'hourly'], ['hour', 'hourly'], ['annually', 'yearly']]:
        r = parse_job({'jobId': f'u-{unit}', 'title': 'Data Analyst', 'company': 'Foo Inc', 'salaryMin': 31, 'salaryUnit': unit, 'url': 'u'}, 'careerbuilder')
        check(f'careerbuilder: salaryUnit "{unit}" -> {expected}', r.salaryPeriod, expected)
    cb5 = parse_job({ 'jobId': 'x5', 'title': 'Data Analyst', 'company': 'Foo Inc', 'state': 'Not A Real State Value That Is Long', 'url': 'u' }, 'careerbuilder')
    check('careerbuilder: garbage state -> null, never truncated into the column', [cb5.stateCode, cb5.city], [None, None])

    check('careerbuilder: row without company dropped', parse_job({ 'jobId': 'x2', 'title': 'T' }, 'careerbuilder'), None)

    # title check used when a role has no distinctive word
    check('titleMatchesQuery: both words present', title_matches_query('Senior Data Engineer', 'data engineer'), True)
    check('titleMatchesQuery: substring match ("engineer" in "Engineering")', title_matches_query('Data Engineering Lead', 'data engineer'), True)
    check('titleMatchesQuery: missing word fails', title_matches_query('Financial Analyst', 'data engineer'), False)
    check('titleMatchesQuery: case-insensitive', title_matches_query('DATA ENGINEER', 'Data Engineer'), True)

    # ZipRecruiter
    zr1 = parse_job({
      'id': '32fb3352fafc8b3f', 'title': 'Data Analyst', 'company': 'ITnova', 'city': 'New City', 'state': 'NY', 'postalCode': '10956',
      'location': 'New City, NY', 'isRemote': False, 'employmentType': 'FULL_TIME', 'salaryMin': None, 'salaryMax': None, 'salary': None,
      'description': 'plain text desc', 'descriptionHtml': '<p>html desc</p>', 'datePosted': '2026-09-22T01:16:17Z',
      'url': 'https:#www.ziprecruiter.com/c/ITnova/Job/Data-Analyst/x?jid=32fb3352fafc8b3f',
    }, 'ziprecruiter')
    check('ziprecruiter: id / location', [zr1.jobId, zr1.city, zr1.stateCode, zr1.state], ['ziprecruiter_32fb3352fafc8b3f', 'New City', 'NY', 'New York'])
    check('ziprecruiter: employmentType FULL_TIME -> full-time', zr1.employmentType, 'full-time')
    check('ziprecruiter: prefers plain description over HTML', zr1.jobDescription, 'plain text desc')
    check('ziprecruiter: posting date', iso(zr1.postingDate), '2026-09-22T01:16:17.000Z')

    zr2 = parse_job({
      'id': 'x2', 'title': 'Data and Payment Processing Analyst', 'company': 'Archdiocese Of Denver', 'city': 'Denver', 'state': 'CO',
      'location': 'Denver, CO', 'isRemote': False, 'employmentType': 'FULL_TIME', 'salaryMin': 25, 'salaryMax': 27, 'salaryCurrency': 'USD',
      'salaryPeriod': 'HOUR', 'datePosted': '2026-09-22T10:45:11Z',
    }, 'ziprecruiter')
    check('ziprecruiter: hourly salary + period', [zr2.salaryMin, zr2.salaryMax, zr2.salaryPeriod, zr2.salaryPresent], [25, 27, 'hourly', True])

    # A broken salary ($75,000-$130,000 labelled "/hour") must be dropped, not saved.
    zr3 = parse_job({
      'id': 'x3', 'title': 'Data Analyst', 'company': 'HEB', 'city': 'Austin', 'state': 'TX', 'location': 'Austin, TX',
      'salaryMin': 75000, 'salaryMax': 130000, 'salaryCurrency': 'USD', 'salaryPeriod': 'HOUR', 'datePosted': '2026-09-21T23:59:15Z',
    }, 'ziprecruiter')
    check('ziprecruiter: implausible hourly salary dropped, not saved as-is', [zr3.salaryMin, zr3.salaryMax, zr3.salaryPresent], [None, None, False])

    # A high but realistic yearly salary must be kept
    zr4 = parse_job({
      'id': 'x4', 'title': 'Data Analyst', 'company': 'BigTech', 'city': 'Austin', 'state': 'TX', 'location': 'Austin, TX',
      'salaryMin': 400000, 'salaryMax': 600000, 'salaryCurrency': 'USD', 'salaryPeriod': 'YEAR', 'datePosted': '2026-09-21T23:59:15Z',
    }, 'ziprecruiter')
    check('ziprecruiter: high-but-plausible yearly salary kept', [zr4.salaryMin, zr4.salaryMax, zr4.salaryPresent], [400000, 600000, True])

    # Filler rows from the page (no company) must be dropped
    check('ziprecruiter: page-furniture row (no company) dropped', parse_job({ 'id': 'x5', 'title': 'ZipRecruiter', 'company': None }, 'ziprecruiter'), None)
    check('ziprecruiter: row without company dropped', parse_job({ 'id': 'x6', 'title': 'T' }, 'ziprecruiter'), None)
    check('ziprecruiter: title word match applies (multi-word role)', title_matches_query(zr2.title, 'data analyst'), True)

    # Reposts: publishDate is the first posting date, originalPublishDate the latest repost.
    # postingDate must take the more recent one.
    mRepost = parse_job({
      'id': 'x1', 'title': 'Cyber Data Analyst', 'company': 'SkyePoint Decisions', 'location': 'Arlington, VA', 'stateCode': 'VA',
      'workTypes': ['FULL_TIME'], 'publishDate': '2026-07-26', 'publishDateISO': '2026-07-26', 'isRepost': True, 'originalPublishDate': '2026-09-21',
    }, 'monster')
    check('monster: repost -- originalPublishDate wins over old publishDate', iso(mRepost.postingDate), '2026-09-21T00:00:00.000Z')
    mNotRepost = parse_job({
      'id': 'x2', 'title': 'Data Analyst', 'company': 'Uline, Inc.', 'location': 'Kenosha, WI', 'stateCode': 'WI',
      'workTypes': ['FULL_TIME'], 'publishDate': '2026-09-20', 'publishDateISO': '2026-09-20', 'isRepost': False, 'originalPublishDate': None,
    }, 'monster')
    check('monster: non-repost -- falls through to publishDate as before', iso(mNotRepost.postingDate), '2026-09-20T00:00:00.000Z')
    mRepostOlderOriginal = parse_job({
      'id': 'x3', 'title': 'Data Analyst', 'company': 'Foo', 'location': 'Austin, TX', 'stateCode': 'TX', 'workTypes': ['FULL_TIME'],
      'publishDate': '2026-09-21', 'publishDateISO': '2026-09-21', 'isRepost': True, 'originalPublishDate': '2026-01-01',
    }, 'monster')
    check('monster: defensive -- if originalPublishDate is somehow OLDER, publishDate (more recent) still wins', iso(mRepostOlderOriginal.postingDate), '2026-09-21T00:00:00.000Z')
    # Full state name instead of "NY": the location text must still give the state
    mFullStateName = parse_job({
      'id': 'x4', 'title': 'Data Analyst', 'company': 'Pfizer', 'location': 'New York, New York', 'stateCode': 'New York',
      'workTypes': ['FULL_TIME'], 'publishDate': '2026-09-09', 'publishDateISO': '2026-09-09', 'isRepost': False,
    }, 'monster')
    check('monster: full-name stateCode falls back to location-text parse', [mFullStateName.stateCode, mFullStateName.state], ['NY', 'New York'])

    # JobRight
    jr1 = parse_job({
      'jobTitle': "Data Engineer - AWS, Snowflake", 'companyName': "Regions Bank", 'location': "Charlotte, NC+3 more",
      'postedDate': "20 minutes ago", 'applyUrl': "https:#jobright.ai/jobs/info/6ab2a0bd1e4847ddae9153c9?value=data+engineer",
      'requirements': ["AWS", "Snowflake", "Charlotte, NC+3 more", "Full-time", "Onsite", "Senior Level", "6+ years exp"],
      'benefits': ["Full-time", "Onsite", "Paid Vacation/Sick Time"],
      'scrapedAt': "2026-09-22T16:12:38.132Z",
    }, 'jobright')
    check('jobright: id extracted from applyUrl path', jr1.jobId, 'jobright_6ab2a0bd1e4847ddae9153c9')
    check('jobright: "+N more" stripped, location resolves', [jr1.city, jr1.stateCode, jr1.state], ['Charlotte', 'NC', 'North Carolina'])
    check('jobright: employment type + onsite from token scan', [jr1.employmentType, jr1.isRemote, jr1.isHybrid], ['full-time', False, False])
    check('jobright: relative date "20 minutes ago" resolved against scrapedAt', iso(jr1.postingDate), '2026-09-22T15:52:38.132Z')
    check('jobright: no description extracted (deliberately null)', jr1.jobDescription, None)

    jr2 = parse_job({
      'jobTitle': "Data Engineer III", 'companyName': "Amazon", 'location': "Charleston, SC", 'postedDate': "22 minutes ago",
      'applyUrl': "https:#jobright.ai/jobs/info/6ab2a8571e4847ddae9157ec",
      'requirements': ["Full-time", "Onsite", "$160K/yr - $188K/yr", "3+ years exp"],
      'benefits': ["Full-time", "Onsite"],
      'scrapedAt': "2026-09-22T16:32:38.132Z",
    }, 'jobright')
    check('jobright: yearly salary range parsed (K suffix)', [jr2.salaryMin, jr2.salaryMax, jr2.salaryPeriod, jr2.salaryPresent], [160000, 188000, 'yearly', True])

    jr3 = parse_job({
      'jobTitle': "Data/Database Administration - Data Engineer", 'companyName': "Mitchell Martin Inc.", 'location': "Montvale, NJ",
      'postedDate': "just now", 'applyUrl': "https:#jobright.ai/jobs/info/6ab2a94e78c69ff506c3fa3c",
      'requirements': ["Contract", "Hybrid", "$55/hr - $65/hr", "5+ years exp"], 'benefits': ["Contract", "Hybrid"],
      'scrapedAt': "2026-09-22T16:29:36.909Z",
    }, 'jobright')
    check('jobright: hourly salary range parsed, "just now" -> scrapedAt itself', [jr3.salaryMin, jr3.salaryMax, jr3.salaryPeriod, iso(jr3.postingDate)], [55, 65, 'hourly', '2026-09-22T16:29:36.909Z'])
    check('jobright: contract + hybrid from token scan', [jr3.employmentType, jr3.isRemote, jr3.isHybrid], ['contract', False, True])

    jr4 = parse_job({
      'jobTitle': "Data Engineer Lead, RBQM", 'companyName': "Atlas", 'location': "United States", 'postedDate': "5 minutes ago",
      'applyUrl': "https:#jobright.ai/jobs/info/6ab2ac421e4847ddae915a73",
      'requirements': ["Contract", "Remote", "Lead/Staff", "5+ years exp"], 'benefits': ["Contract", "Remote"],
      'scrapedAt': "2026-09-22T16:32:38.132Z",
    }, 'jobright')
    check('jobright: no salary in bag -> all null, not guessed', [jr4.salaryMin, jr4.salaryMax, jr4.salaryPeriod, jr4.salaryPresent], [None, None, None, False])
    check('jobright: bare "United States" -> no city/state', [jr4.city, jr4.stateCode], [None, None])
    check('jobright: remote from token scan', [jr4.isRemote, jr4.isHybrid], [True, False])

    check('jobright: row without company dropped', parse_job({ 'jobTitle': 'T', 'applyUrl': 'u' }, 'jobright'), None)
    check('jobright: unparseable relative date -> null, not a guess', parse_jobright_row({ 'jobTitle': 'T', 'companyName': 'C', 'postedDate': 'yesterday', 'scrapedAt': '2026-09-22T16:00:00Z' }).postingDate, None)

    # Other boards still go through the multi-board parser
    li = parse_job({ 'id': '123', 'title': 'Data Engineer', 'company': 'Qux', 'location': 'Dallas, TX', 'date_posted': '2026-09-20', 'job_url': 'u' }, 'linkedin')
    check('linkedin still uses the multi-board parser', li.jobId, 'linkedin_123')

    return failures


class Command(ScriptCommand):
    help = 'Offline check of every board parser (no Apify or database needed).'
    script_name = 'test-board-parsers'
    requires_system_checks = []

    def handle(self, *args, **opts):
        failures = run_checks()
        log.info('\nAll parser checks passed.' if failures == 0 else f'\n{failures} check(s) FAILED.')
        sys.exit(0 if failures == 0 else 1)
