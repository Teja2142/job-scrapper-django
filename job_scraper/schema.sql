-- =====================================================================
-- Job Scraping + Visa Intelligence Engine -- database schema (v1)
--
-- Run with: python manage.py setup_db
-- Safe to run again: every statement is IF NOT EXISTS / ON CONFLICT DO
-- NOTHING, so a second run changes nothing and deletes no data.
-- All SQL the app runs is in core/sql.py.
-- =====================================================================

-- ---------------------------------------------------------------------
-- us_jobs -- one row per scraped job
-- Written by the daily scrape, the visa matcher and the inactive check.
-- Rows are never deleted; jobs not seen for SCRAPE_INACTIVE_AFTER_DAYS
-- get is_active = FALSE.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS us_jobs (
    id BIGSERIAL PRIMARY KEY,

    -- Identity & duplicates
    job_id VARCHAR(300) UNIQUE NOT NULL,      -- "<board>_<board's own id>"
    content_hash VARCHAR(64),                 -- sha256 of title|company|city|state, same job on two boards = same hash
    is_duplicate BOOLEAN DEFAULT FALSE,       -- TRUE when another board's row has the same content_hash
    canonical_job_id BIGINT,                  -- for a duplicate: id of the original row

    -- Job
    title VARCHAR(500) NOT NULL,
    company_name VARCHAR(300) NOT NULL,
    company_name_norm VARCHAR(300),           -- normalizeCompany(company_name), used for visa matching
    job_url TEXT,
    job_description TEXT,

    -- Location (parsed)
    location_raw VARCHAR(500),
    city VARCHAR(150),
    state VARCHAR(100),                       -- full name, e.g. 'Texas'
    state_code VARCHAR(5),                    -- e.g. 'TX'
    is_remote BOOLEAN DEFAULT FALSE,
    is_hybrid BOOLEAN DEFAULT FALSE,
    country VARCHAR(50) DEFAULT 'USA',

    -- Salary
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency VARCHAR(10) DEFAULT 'USD',
    salary_period VARCHAR(20),                -- 'yearly' | 'hourly' | 'daily' | 'weekly' | 'monthly'
    salary_present BOOLEAN DEFAULT FALSE,

    -- Requirements
    experience_min_years INTEGER,             -- not filled yet (reserved for an experience filter)
    employment_type VARCHAR(50),              -- e.g. 'full-time' | 'contract' | 'full-time, contract'

    -- Source & freshness
    source VARCHAR(50) NOT NULL,              -- board: 'linkedin' | 'indeed' | 'dice' | ...
    posting_date TIMESTAMP,
    posted_days_ago INTEGER,                  -- age when last scraped

    -- Visa data copied from company_visa_history by the matcher
    visa_matched BOOLEAN DEFAULT FALSE,       -- did the matcher find the company?
    h1b_sponsor BOOLEAN,
    h1b_filings_recent INTEGER,               -- H-1B count if the company filed in the last 2 years, else 0
    everify_enrolled BOOLEAN,
    green_card_sponsor BOOLEAN,
    visa_grade VARCHAR(2),                    -- 'A'..'F'
    visa_friendly_score INTEGER,              -- 0-100

    -- Admin
    is_active BOOLEAN DEFAULT TRUE,
    first_seen_at TIMESTAMP DEFAULT NOW(),
    last_scraped_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Filters the search API uses
CREATE INDEX IF NOT EXISTS idx_jobs_state ON us_jobs(state_code);
CREATE INDEX IF NOT EXISTS idx_jobs_city ON us_jobs(city);
CREATE INDEX IF NOT EXISTS idx_jobs_remote ON us_jobs(is_remote);
CREATE INDEX IF NOT EXISTS idx_jobs_salary ON us_jobs(salary_min);
CREATE INDEX IF NOT EXISTS idx_jobs_posting_date ON us_jobs(posting_date DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON us_jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_active ON us_jobs(is_active);
CREATE INDEX IF NOT EXISTS idx_jobs_h1b ON us_jobs(h1b_sponsor);
CREATE INDEX IF NOT EXISTS idx_jobs_visa_score ON us_jobs(visa_friendly_score DESC);
-- Used by the matcher and duplicate check
CREATE INDEX IF NOT EXISTS idx_jobs_company_norm ON us_jobs(company_name_norm);
CREATE INDEX IF NOT EXISTS idx_jobs_content_hash ON us_jobs(content_hash);
-- Full-text search on the title
CREATE INDEX IF NOT EXISTS idx_jobs_title_fts ON us_jobs USING GIN (to_tsvector('english', title));

-- ---------------------------------------------------------------------
-- company_visa_history -- one row per company (by normalized name)
-- Written by the visa refresh (python manage.py visa_refresh). Each source
-- REPLACES its own columns on every load; score and grade are recalculated
-- for every company at the end.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS company_visa_history (
    id BIGSERIAL PRIMARY KEY,
    company_name VARCHAR(300) NOT NULL,
    company_name_norm VARCHAR(300) NOT NULL UNIQUE,  -- normalizeCompany(company_name); UNIQUE also indexes it

    -- H-1B: filings and salary from DOL LCA; approvals / denials from the USCIS Data Hub
    h1b_total_filings INTEGER DEFAULT 0,
    h1b_approvals INTEGER DEFAULT 0,
    h1b_denials INTEGER DEFAULT 0,
    h1b_approval_rate NUMERIC(5,2),           -- e.g. 94.23 (%)
    h1b_avg_salary INTEGER,                   -- MEDIAN yearly H-1B salary (name kept for the API)
    h1b_last_filing_year INTEGER,

    -- PERM / green card (DOL PERM)
    perm_total_filings INTEGER DEFAULT 0,
    perm_last_year INTEGER,
    green_card_sponsor BOOLEAN DEFAULT FALSE,

    -- E-Verify: TRUE = has an Open account, FALSE = only Terminated accounts,
    -- NULL = not in the E-Verify list (unknown -- large employers are often
    -- registered under a parent-company name, so missing is not "no").
    everify_enrolled BOOLEAN,

    -- Calculated by visa/scoring.py
    visa_friendly_score INTEGER DEFAULT 0,    -- 0-100
    visa_grade VARCHAR(2),                    -- 'A'..'F'

    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_visa_grade ON company_visa_history(visa_grade);
CREATE INDEX IF NOT EXISTS idx_visa_score ON company_visa_history(visa_friendly_score DESC);

-- ---------------------------------------------------------------------
-- company_aliases -- hand-checked brand name -> government filing name
-- For companies the automatic matcher can't safely connect, e.g. postings
-- say "CGI Group" but the H-1B filer is "CGI Technologies and Solutions
-- Inc". The matcher uses these first. Add more with:
--   python manage.py add_alias "<brand>" "<filing name>" "<how it was confirmed>"
-- Both columns hold normalize_company() values.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS company_aliases (
    id BIGSERIAL PRIMARY KEY,
    alias_norm VARCHAR(300) NOT NULL UNIQUE,  -- brand name as seen in job postings (UNIQUE also indexes it)
    target_norm VARCHAR(300) NOT NULL,        -- company_name_norm in company_visa_history
    notes VARCHAR(500),                       -- how the mapping was confirmed
    created_at TIMESTAMP DEFAULT NOW()
);

-- ---------------------------------------------------------------------
-- scraping_log -- one row per board search, matcher run or visa refresh
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scraping_log (
    id BIGSERIAL PRIMARY KEY,
    run_type VARCHAR(50),                     -- 'daily_scrape' | 'match' | 'visa_refresh'
    source VARCHAR(50),                       -- board (daily_scrape only)
    search_query VARCHAR(300),                -- role (daily_scrape only)
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    jobs_found INTEGER DEFAULT 0,
    jobs_inserted INTEGER DEFAULT 0,
    jobs_updated INTEGER DEFAULT 0,           -- 'match' rows: jobs matched
    jobs_duplicate INTEGER DEFAULT 0,
    status VARCHAR(20),                       -- 'success' | 'failed'
    error_message TEXT,
    apify_run_id VARCHAR(200),                -- daily_scrape: id of the last Apify run for this search
    apify_runs INTEGER DEFAULT 0,             -- daily_scrape: Apify runs this search took (pages + retries)
    apify_cost_usd NUMERIC(10,4),             -- daily_scrape: what those runs cost, as reported by Apify
    created_at TIMESTAMP DEFAULT NOW()
);

-- Added after the first release: brings an existing scraping_log up to date.
ALTER TABLE scraping_log ADD COLUMN IF NOT EXISTS apify_runs INTEGER DEFAULT 0;
ALTER TABLE scraping_log ADD COLUMN IF NOT EXISTS apify_cost_usd NUMERIC(10,4);

CREATE INDEX IF NOT EXISTS idx_log_run_type ON scraping_log(run_type);
CREATE INDEX IF NOT EXISTS idx_log_created ON scraping_log(created_at DESC);

-- ---------------------------------------------------------------------
-- Starting data: the hand-checked aliases confirmed so far (add new ones
-- with python manage.py add_alias, and copy them here so a new database
-- gets them too). Some brands also have their own row in the government
-- data (a holding company or E-Verify-only entry with no filings), and a few
-- are "pins" (alias = target) that stop the fuzzy / prefix passes from
-- matching an unrelated company. They take effect once the visa refresh has
-- loaded the target companies. Re-running setup_db never duplicates them.
-- ---------------------------------------------------------------------
INSERT INTO company_aliases (alias_norm, target_norm, notes) VALUES
    ('cgigroup',                                'cgitechnologiesandsolutions',             'confirmed via myvisajobs.com, ~1259 LCAs FY2025'),
    ('govcio',                                  'governmentcio',                           'GovCIO''s official registered name is GovernmentCIO LLC per myvisajobs.com/h1bgrader.com listings'),
    ('johnshopkinsappliedphysicslaboratoryapl', 'johnshopkinsuniversityappliedphysicslab', 'corrects existing alias which pointed at a 0-filing duplicate'),
    ('vacobyhighspring',                        'highspring',                              'Vaco rebranded to Highspring'),
    ('franklincovey',                           'franklincoveyclientsales',                'FranklinCovey Client Sales, Inc. is their LCA filer entity'),
    ('meta',                                    'metaplatforms',                           'Meta job postings use "Meta"; LCA/PERM filer name is META PLATFORMS INC'),
    ('oracle',                                  'oracleamerica',                           'parent legal entity has 0 filings; Oracle America Inc is the actual LCA filer'),
    ('amazoncom',                               'amazoncomservices',                       'AMAZON.COM INC is the holding co; Amazon.com Services LLC is the real filer'),
    ('walmart',                                 'walmartassociates',                       'Walmart Inc holding co has 0 filings; Wal-Mart Associates Inc is the real filer'),
    ('cognizant',                               'cognizanttechnologysolutionsus',          'main H-1B filer'),
    ('citi',                                    'citibankna',                              'main H-1B filer'),
    ('avaconsulting',                           'avaconsulting',                           'pin: fuzzy matched unrelated SVA Consulting'),
    ('stateofwisconsin',                        'stateofwisconsin',                        'pin: prefix matched a single agency'),
    ('stellartech',                             'stellartech',                             'pin: prefix matched unrelated StellarTech Research'),
    ('jpmorganchase',                           'jpmorganchaseand',                        'brand spelling, same filer as JPMorgan Chase & Co.'),
    ('capitalone',                              'capitaloneservices',                      'largest of 2 filers (657 vs 427 NA)'),
    ('nttdata',                                 'nttdataamericas',                         'main US filer'),
    ('nttdatanorthamerica',                     'nttdataamericas',                         'main US filer'),
    ('goldmansachs',                            'goldmansachsand',                         'largest filer'),
    ('capgemini',                               'capgeminiamerica',                        'main US filer'),
    ('infosystechnologies',                     'infosys',                                 'former name'),
    ('jabilcircuit',                            'jabil',                                   'renamed'),
    ('blackstonegroup',                         'blackstone',                              'renamed 2021'),
    ('siemensdigitalindustriessoftware',        'siemensindustrysoftware',                 'legal entity'),
    ('bny',                                     'bankofnewyorkmellon',                     'brand name'),
    ('keycorp',                                 'keybanknationalassociation',              'bank subsidiary is the filer'),
    ('usaa',                                    'unitedservicesautomobileassociation',     'full legal name'),
    ('cvshealth',                               'cvssharedservicesresources',              'largest filer'),
    ('walmartglobaltech',                       'walmartassociates',                       'Walmart''s filer'),
    ('wspintheus',                              'wspusa',                                  'main US filer'),
    ('davitakidneycare',                        'davita',                                  'parent filer'),
    ('broadridge',                              'broadridgefinancialsolutions',            'legal name'),
    ('adyen',                                   'adyennv',                                 'legal name'),
    ('stifel',                                  'stifelfinancial',                         'legal name'),
    ('ice',                                     'intercontinentalexchangeholdings',        'Atlanta Java job = Intercontinental Exchange, not I.C.E., Inc.'),
    ('kforcetechnologystaffing',                'kforce',                                  'same company, postings use the brand name'),
    ('americanfamilyinsurance',                 'americanfamilymutualinsurancecompanysi',  'same company, main H-1B filer'),
    ('stjudechildrensresearchhospitalalsac',    'stjudechildrensresearchhospital',         'ALSAC is St. Jude''s fundraising arm; the hospital is the filer'),
    ('ucirvine',                                'universityofcaliforniairvine',            'short name of University of California, Irvine')
ON CONFLICT (alias_norm) DO NOTHING;
