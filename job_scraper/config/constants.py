# Every board the scraper knows. The boards that actually run come from SCRAPE_SOURCES in
# .env; when that is empty, SOURCES below is used (order = priority for per_role).

# Boards covered by one actor, flash_scraper/multi-jobboard-scraper (multi_job_board_parser.py).
SUPPORTED_SITES = (
    'linkedin',
    'indeed',
    'glassdoor',
    # 'muse',
    'remotive',
    'jobicy',
    'himalayas',
    'hn_hiring',
    'remoteok',
    'weworkremotely',
    'working_nomads',
    # 'devitjobs_us',
    # 'devitjobs_uk',
)

# Boards with their own actor and parser (see build_input() in scraper/job_scraper.py).
DEDICATED_SITES = ('dice', 'monster', 'careerbuilder', 'ziprecruiter', 'jobright')

# Default board list: the client's required boards first, then the optional remote boards.
SOURCES = (
    'linkedin',
    'indeed',
    'glassdoor',
    'dice',
    'monster',
    'careerbuilder',
    'ziprecruiter',
    'jobright',
    # 'muse',
    'remotive',
    'jobicy',
    'himalayas',
    'hn_hiring',
    'remoteok',
    'weworkremotely',
    'working_nomads',
    # 'devitjobs_us',
    # 'devitjobs_uk',
)
