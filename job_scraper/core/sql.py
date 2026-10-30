"""
All SQL the app runs. Usage: db.query(SQL.jobs.upsert, [params...])

Placeholders are $1, $2, ... (db.query converts them). The comment above each statement says
what each placeholder is, so if you add or reorder one, change the call site as well.
Table definitions are in schema.sql.
"""
from types import SimpleNamespace

# LCA filings and USCIS decisions come from different files and don't fully overlap, so a
# company can have USCIS approvals but no LCA rows. Take the bigger of the two (scoring.py does
# the same), otherwise such a company would show h1b_sponsor = FALSE.
EFFECTIVE_FILINGS_SQL = 'GREATEST(v.h1b_total_filings, v.h1b_approvals + v.h1b_denials)'

# Companies with real sponsorship records (LCA, USCIS or PERM). The others only come from the
# E-Verify list, often a brand name like "Citi" whose filings are under another legal name, so
# they are only used as a last resort.
HAS_EVIDENCE_SQL = '(v.h1b_total_filings > 0 OR v.h1b_approvals + v.h1b_denials > 0 OR v.perm_total_filings > 0)'


def COPY_VISA_FIELDS_SQL(cutoff: str) -> str:
    """The visa columns copied onto us_jobs. `cutoff` is the placeholder for the cutoff year."""
    return f"""
        visa_matched = TRUE,
        h1b_sponsor = ({EFFECTIVE_FILINGS_SQL} > 0),
        h1b_filings_recent = CASE
          WHEN v.h1b_last_filing_year >= {cutoff} THEN {EFFECTIVE_FILINGS_SQL}
          ELSE 0
        END,
        everify_enrolled = v.everify_enrolled,
        green_card_sponsor = v.green_card_sponsor,
        visa_grade = v.visa_grade,
        visa_friendly_score = v.visa_friendly_score,
        updated_at = NOW()"""


SQL = SimpleNamespace(
    # us_jobs
    jobs=SimpleNamespace(
        # $1..$23 = the columns in the order listed (see upsert_job() in scraper/job_scraper.py).
        # An existing job gets its fields refreshed, but COALESCE keeps a value we already had
        # when the board left it out this time. The duplicate columns are not touched here.
        upsert="""INSERT INTO us_jobs (
        job_id, content_hash, is_duplicate, canonical_job_id,
        title, company_name, company_name_norm, job_url, job_description,
        location_raw, city, state, state_code, is_remote, is_hybrid,
        salary_min, salary_max, salary_period, salary_present,
        employment_type, source, posting_date, posted_days_ago,
        last_scraped_at, updated_at
     ) VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,
        $16,$17,$18,$19,$20,$21,$22,$23, NOW(), NOW()
     )
     ON CONFLICT (job_id) DO UPDATE SET
        title = EXCLUDED.title,
        company_name = EXCLUDED.company_name,
        company_name_norm = EXCLUDED.company_name_norm,
        job_url = COALESCE(EXCLUDED.job_url, us_jobs.job_url),
        job_description = EXCLUDED.job_description,
        location_raw = COALESCE(EXCLUDED.location_raw, us_jobs.location_raw),
        city = COALESCE(EXCLUDED.city, us_jobs.city),
        state = COALESCE(EXCLUDED.state, us_jobs.state),
        state_code = COALESCE(EXCLUDED.state_code, us_jobs.state_code),
        is_remote = EXCLUDED.is_remote,
        is_hybrid = EXCLUDED.is_hybrid,
        employment_type = COALESCE(EXCLUDED.employment_type, us_jobs.employment_type),
        salary_min = EXCLUDED.salary_min,
        salary_max = EXCLUDED.salary_max,
        salary_period = EXCLUDED.salary_period,
        salary_present = EXCLUDED.salary_present,
        posting_date = EXCLUDED.posting_date,
        posted_days_ago = EXCLUDED.posted_days_ago,
        last_scraped_at = NOW(),
        updated_at = NOW(),
        is_active = TRUE
     RETURNING (xmax = 0) AS inserted""",

        # $1 = content_hash, $2 = job_id of the job being saved
        findDuplicateByContentHash='SELECT id FROM us_jobs WHERE content_hash = $1 AND job_id <> $2 LIMIT 1',

        # $1 = days without being scraped before a job is flagged inactive
        markStaleInactive="""UPDATE us_jobs SET is_active = FALSE
     WHERE last_scraped_at < NOW() - make_interval(days => $1::int) AND is_active = TRUE""",

        # no parameters
        countAll='SELECT COUNT(*) FROM us_jobs',

        # no parameters
        countMatched='SELECT COUNT(*) FROM us_jobs WHERE visa_matched = TRUE',
    ),

    # scraping_log: one row per board search, visa refresh or match run
    scrapingLog=SimpleNamespace(
        # $1 run_type, $2 source, $3 search_query, $4 started_at, $5 ended_at, $6 jobs_found,
        # $7 jobs_inserted, $8 jobs_updated, $9 jobs_duplicate, $10 status, $11 error_message,
        # $12 apify_run_id (last run), $13 apify_runs, $14 apify_cost_usd
        insertScrapeRun="""INSERT INTO scraping_log
        (run_type, source, search_query, started_at, ended_at,
         jobs_found, jobs_inserted, jobs_updated, jobs_duplicate, status, error_message,
         apify_run_id, apify_runs, apify_cost_usd)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",

        # $1 = started_at, $2 = error message
        insertVisaRefreshFailed="""INSERT INTO scraping_log (run_type, started_at, ended_at, status, error_message)
       VALUES ('visa_refresh', $1, NOW(), 'failed', $2)""",

        # $1 = started_at
        insertVisaRefreshSuccess="""INSERT INTO scraping_log (run_type, started_at, ended_at, status)
       VALUES ('visa_refresh', $1, NOW(), 'success')""",

        # $1 = started_at, $2 = number of jobs matched
        insertMatchRun="""INSERT INTO scraping_log (run_type, started_at, ended_at, jobs_updated, status)
     VALUES ('match', $1, NOW(), $2, 'success')""",
    ),

    # company_visa_history
    visaHistory=SimpleNamespace(
        # $1 name, $2 norm, $3 h1b filings, $4 approvals, $5 denials, $6 avg salary, $7 h1b last year,
        # $8 perm filings, $9 perm last year, $10 green-card sponsor, $11 e-verify
        mergeCompany="""UPDATE company_visa_history SET
        company_name = $1,
        h1b_total_filings = COALESCE($3, h1b_total_filings),
        h1b_approvals = COALESCE($4, h1b_approvals),
        h1b_denials = COALESCE($5, h1b_denials),
        h1b_avg_salary = COALESCE($6, h1b_avg_salary),
        h1b_last_filing_year = GREATEST(COALESCE(h1b_last_filing_year, 0), COALESCE($7, 0)),
        perm_total_filings = COALESCE($8, perm_total_filings),
        perm_last_year = GREATEST(COALESCE(perm_last_year, 0), COALESCE($9, 0)),
        green_card_sponsor = COALESCE($10, green_card_sponsor),
        everify_enrolled = COALESCE($11, everify_enrolled),
        updated_at = NOW()
     WHERE company_name_norm = $2""",

        # $1 = company_name, $2 = company_name_norm
        ensureCompany="""INSERT INTO company_visa_history (company_name, company_name_norm)
     VALUES ($1, $2)
     ON CONFLICT (company_name_norm) DO NOTHING""",

        # $1 = company_name_norm to look up
        findForAliasTarget="""SELECT company_name, h1b_total_filings, h1b_approvals, visa_grade
     FROM company_visa_history WHERE company_name_norm = $1""",

        # no parameters
        countAll='SELECT COUNT(*) FROM company_visa_history',

        # Each loader clears its own columns before writing (only if its file parsed fine),
        # because the government files are cumulative: replace, don't add.

        # no parameters -- run by parse_lca.py before writing
        resetLcaFields="""UPDATE company_visa_history
     SET h1b_total_filings = 0, h1b_avg_salary = NULL
     WHERE h1b_total_filings <> 0 OR h1b_avg_salary IS NOT NULL""",

        # no parameters -- run by parse_uscis_hub.py before writing
        resetUscisFields="""UPDATE company_visa_history
     SET h1b_approvals = 0, h1b_denials = 0, h1b_approval_rate = NULL
     WHERE h1b_approvals <> 0 OR h1b_denials <> 0 OR h1b_approval_rate IS NOT NULL""",

        # no parameters -- run by parse_everify.py before writing: every company starts as
        # "unknown" and only the E-Verify list can set TRUE / FALSE
        resetEverify="""UPDATE company_visa_history
     SET everify_enrolled = NULL
     WHERE everify_enrolled IS NOT NULL""",

        # $1 = company names text[], $2 = normalized names text[], $3 = enrolled boolean[]
        # (norms must be unique in one call). For an existing company only everify_enrolled changes.
        upsertEverifyBatch="""INSERT INTO company_visa_history (company_name, company_name_norm, everify_enrolled)
     SELECT * FROM unnest($1::text[], $2::text[], $3::boolean[])
     ON CONFLICT (company_name_norm) DO UPDATE
       SET everify_enrolled = EXCLUDED.everify_enrolled, updated_at = NOW()""",

        # $1 = normalized names text[], $2 = enrolled boolean[]
        # For "doing business as" names: only updates existing companies, and never turns TRUE into FALSE.
        updateEverifyExistingBatch="""UPDATE company_visa_history v
     SET everify_enrolled = u.enrolled, updated_at = NOW()
     FROM unnest($1::text[], $2::boolean[]) AS u(norm, enrolled)
     WHERE v.company_name_norm = u.norm
       AND v.everify_enrolled IS DISTINCT FROM TRUE""",

        # $1 = last id seen, $2 = batch size -- used by rescore_all_companies()
        selectForRescore="""SELECT id, h1b_total_filings, h1b_approvals, h1b_denials,
            h1b_last_filing_year, perm_total_filings, everify_enrolled
     FROM company_visa_history
     WHERE id > $1
     ORDER BY id
     LIMIT $2""",

        # $1 = ids bigint[], $2 = scores int[], $3 = grades text[], $4 = approval rates numeric[]
        saveScoresBatch="""UPDATE company_visa_history v
     SET visa_friendly_score = u.score, visa_grade = u.grade, h1b_approval_rate = u.rate
     FROM unnest($1::bigint[], $2::int[], $3::text[], $4::numeric[]) AS u(id, score, grade, rate)
     WHERE v.id = u.id""",
    ),

    # visa matcher
    matcher=SimpleNamespace(
        # $1 = job company norm, $2 = target visa company norm, $3 = recent-filings cutoff year.
        # Used by the fuzzy and prefix passes once they have picked the target company.
        applyNamedMatch=f"""UPDATE us_jobs j
     SET {COPY_VISA_FIELDS_SQL('$3')}
     FROM company_visa_history v
     WHERE j.company_name_norm = $1
       AND v.company_name_norm = $2
       AND j.visa_matched = FALSE""",

        # $1 = LIKE pattern (already escaped) -- returns at most 2 rows
        findPrefixCandidates=f"""SELECT company_name_norm FROM company_visa_history v
       WHERE company_name_norm LIKE $1 ESCAPE '\\'
         AND {HAS_EVIDENCE_SQL}
       LIMIT 2""",

        # $1 = minimum normalized-name length
        knownCompaniesForReversePrefix=f"""
    SELECT company_name, company_name_norm
    FROM company_visa_history v
    WHERE length(company_name_norm) >= $1
      AND {HAS_EVIDENCE_SQL}
  """,

        # $1 = minimum normalized-name length
        unmatchedCompaniesForPrefix="""
    SELECT company_name_norm, MIN(company_name) AS sample_name, COUNT(*) AS job_count
    FROM us_jobs
    WHERE visa_matched = FALSE
      AND company_name_norm IS NOT NULL
      AND length(company_name_norm) >= $1
    GROUP BY company_name_norm
  """,

        # no parameters
        knownCompanyNorms=f"""
    SELECT DISTINCT company_name_norm FROM company_visa_history v
    WHERE {HAS_EVIDENCE_SQL}
  """,

        # $1 = minimum normalized-name length
        unmatchedCompaniesForFuzzy="""
    SELECT company_name_norm, COUNT(*) AS job_count
    FROM us_jobs
    WHERE visa_matched = FALSE
      AND company_name_norm IS NOT NULL
      AND length(company_name_norm) >= $1
    GROUP BY company_name_norm
  """,

        # $1 = recent-filings cutoff year
        applyAliasMatch=f"""
    UPDATE us_jobs j
    SET {COPY_VISA_FIELDS_SQL('$1')}
    FROM company_aliases a
    JOIN company_visa_history v ON v.company_name_norm = a.target_norm
    WHERE j.company_name_norm = a.alias_norm
      AND j.visa_matched = FALSE
  """,

        # $1 = recent-filings cutoff year
        applyExactMatch=f"""
    UPDATE us_jobs j
    SET {COPY_VISA_FIELDS_SQL('$1')}
    FROM company_visa_history v
    WHERE j.company_name_norm = v.company_name_norm
      AND j.visa_matched = FALSE
      AND {HAS_EVIDENCE_SQL}
  """,

        # $1 = recent-filings cutoff year. Last pass: exact match on the E-Verify-only rows.
        applyExactMatchNoEvidence=f"""
    UPDATE us_jobs j
    SET {COPY_VISA_FIELDS_SQL('$1')}
    FROM company_visa_history v
    WHERE j.company_name_norm = v.company_name_norm
      AND j.visa_matched = FALSE
  """,

        # no parameters (used by --rematch-all)
        resetAllMatches="""
      UPDATE us_jobs
      SET visa_matched = FALSE,
          h1b_sponsor = NULL,
          h1b_filings_recent = NULL,
          everify_enrolled = NULL,
          green_card_sponsor = NULL,
          visa_grade = NULL,
          visa_friendly_score = NULL
    """,
    ),

    # company_aliases: brand name -> legal filing name, added by hand
    aliases=SimpleNamespace(
        # $1 = alias_norm, $2 = target_norm, $3 = notes
        upsert="""INSERT INTO company_aliases (alias_norm, target_norm, notes)
     VALUES ($1, $2, $3)
     ON CONFLICT (alias_norm) DO UPDATE SET target_norm = EXCLUDED.target_norm, notes = EXCLUDED.notes
     RETURNING id""",
    ),

    # read-only checks used by verify_data
    verify=SimpleNamespace(
        recentScrapingLog="""
      SELECT run_type, source, search_query, jobs_found, jobs_inserted, status,
             apify_runs, apify_cost_usd, created_at
      FROM scraping_log ORDER BY created_at DESC LIMIT 20;
    """,

        explainSearchPerformance="""
      EXPLAIN ANALYZE
      SELECT * FROM us_jobs
      WHERE state_code = 'NY' AND h1b_sponsor = TRUE AND salary_min >= 100000
      ORDER BY posting_date DESC LIMIT 50;
    """,

        sampleFriendlyJobs="""
      SELECT title, company_name, city, state_code, salary_min, visa_grade, h1b_filings_recent
      FROM us_jobs
      WHERE state_code = 'CA'
        AND h1b_sponsor = TRUE
        AND to_tsvector('english', title) @@ to_tsquery('english', 'data & engineer')
      ORDER BY visa_friendly_score DESC
      LIMIT 20;
    """,

        visaHistorySize="""
      SELECT COUNT(*) FROM company_visa_history;
    """,

        visaGradeDistribution="""
      SELECT visa_grade, COUNT(*) FROM us_jobs
      WHERE visa_matched GROUP BY visa_grade ORDER BY 1;
    """,

        visaMatchCoverage="""
      SELECT
        COUNT(*) FILTER (WHERE visa_matched) AS matched,
        COUNT(*) AS total,
        ROUND(100.0 * COUNT(*) FILTER (WHERE visa_matched) / NULLIF(COUNT(*),0), 1) AS pct
      FROM us_jobs;
    """,

        locationQuality="""
      SELECT
        COUNT(*) FILTER (WHERE state_code IS NOT NULL) AS with_state,
        COUNT(*) FILTER (WHERE is_remote) AS remote,
        COUNT(*) AS total
      FROM us_jobs;
    """,

        duplicateRate="""
      SELECT is_duplicate, COUNT(*) FROM us_jobs GROUP BY is_duplicate;
    """,

        jobsPerSource="""
      SELECT source, COUNT(*) FROM us_jobs GROUP BY source ORDER BY 2 DESC;
    """,

        totalJobs='SELECT COUNT(*) AS total_jobs FROM us_jobs;',
    ),
)
