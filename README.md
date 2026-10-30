# job_scraper

A Django app that:

1. scrapes fresh US jobs from job boards through Apify (LinkedIn, Indeed, Glassdoor, Dice, Monster,
   CareerBuilder, ZipRecruiter, JobRight, and a few optional remote boards),
2. saves them in PostgreSQL (`us_jobs`),
3. loads the government visa data (DOL LCA, DOL PERM, USCIS H-1B Data Hub, E-Verify) into
   `company_visa_history`,
4. tags each job with its company's visa record and a grade from A to F.

Everything runs as `python manage.py ...` commands inside the existing Django project. There is no
API yet.

---

## Setup

You need Python 3.9 or newer, Django 4.2 or newer and a PostgreSQL database.

1. Copy the `job_scraper` folder into the project, next to the other apps.

2. Install the requirements (inside the project's virtual environment):
   ```
   pip install -r job_scraper/requirements.txt
   ```

3. Add the app to `settings.py`:
   ```python
   INSTALLED_APPS = [
       # ...
       'job_scraper',
   ]
   ```

4. Database. The app has to use PostgreSQL.
   - If the project's `default` database is PostgreSQL, nothing else is needed.
   - If not, add a PostgreSQL entry and tell the app to use it:
     ```python
     DATABASES = {
         'default': {...},
         'jobs': {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'jobplatform', ...},
     }
     JOB_SCRAPER_DB_ALIAS = 'jobs'
     DATABASE_ROUTERS = ['job_scraper.routers.JobScraperRouter']
     ```

5. Create the tables:
   ```
   python manage.py setup_db
   ```
   This runs `schema.sql`. It is safe to run again at any time: it never deletes or overwrites
   data, it only creates what is missing. Run it again after updating the app, so new columns
   get added. (`python manage.py migrate job_scraper` does the same thing if you prefer migrations.)

6. Settings. Copy `job_scraper/env.example` to `.env` in the folder you run `manage.py` from and
   fill in `APIFY_API_TOKEN`. Every setting is explained in that file. Don't share `.env`: it holds
   the token.

7. Put these in the same folder as `manage.py`:
   - `roles.txt`: the job roles to search, one per line (lines starting with `#` are ignored)
   - `data/`: the government files, in `data/lca`, `data/perm`, `data/uscis-hub` and `data/everify`

8. Check that it works (this costs nothing):
   ```
   python manage.py test_board_parsers
   python manage.py daily_run --check-config
   ```

**Windows:** activate the environment with `venv\Scripts\activate`. If the log shows broken
characters, run `set PYTHONUTF8=1` first (or `setx PYTHONUTF8 1` once to make it permanent).

---

## Commands

| Command | What it does |
|---|---|
| `python manage.py setup_db` | Creates the tables, or adds new columns to existing ones |
| `python manage.py visa_refresh` | Loads the government files from `data/`. Run it when new quarterly files are out |
| `python manage.py daily_run` | The daily job: scrape every role in `roles.txt`, match visa data, mark old jobs inactive |
| `python manage.py daily_run --check-config` | Shows the settings and the estimated run size. Scrapes nothing |
| `python manage.py daily_run --each-board` | Tests every board on its own (3 jobs each unless `--max` is given) |
| `python manage.py run_match` | Runs the visa matcher on jobs that aren't matched yet |
| `python manage.py run_match --rematch-all` | Matches every job again. Use it after `visa_refresh` |
| `python manage.py add_alias "<brand>" "<filing name>" "<note>"` | Links a brand name to the company's legal filing name when the matcher can't |
| `python manage.py peek_board --board dice --role "iot engineer" --n 20` | Shows what one board returns, without saving anything |
| `python manage.py verify_data` | Read-only checks on the saved data |
| `python manage.py test_board_parsers` | Offline parser checks |

`daily_run` options override `.env` for that run only: `--file`, `--max`, `--days`, `--scope`,
`--count`, `--sources`, `--board-limits`, `--no-match`, `--no-inactive`. Run
`python manage.py daily_run --help` for details. Example:
```
python manage.py daily_run --max 20 --sources linkedin,dice
```

The commands exit with code 0 when they finish and 1 on a bad setting or a failure.

### Scheduling

Run `daily_run` once a day.

- Linux (cron):
  ```
  15 6 * * *  cd /path/to/project && /path/to/venv/bin/python manage.py daily_run >> logs/daily-run.log 2>&1
  ```
- Windows: create a Task Scheduler task that runs
  `C:\path\to\venv\Scripts\python.exe manage.py daily_run` with "Start in" set to the project folder.

---

## How a daily run works

1. Settings are read from `.env` and checked. A wrong value stops the run before any Apify cost.
2. Each role is searched on each board. With `SCRAPE_LIMIT_SCOPE=per_board`, up to
   `SCRAPE_PARALLEL_BOARDS` searches run at the same time.
   - `SCRAPE_SHARED_QUEUE=true`: all searches of all roles share one queue, so the next search
     starts as soon as any one finishes.
   - `SCRAPE_SHARED_QUEUE=false`: one role at a time, with its boards in parallel.
3. Jobs are saved. A job seen before is refreshed, and the same job on two boards is marked as a
   duplicate.
4. The visa matcher copies each company's visa data onto its jobs.
5. Jobs that no scrape has seen for `SCRAPE_INACTIVE_AFTER_DAYS` are marked inactive (never deleted).

At the end, a table shows every role and board: jobs counted, fetched, saved, duplicates, cost and
a note on why a board gave what it gave. Under it are the totals per board and the total Apify
cost of the run.

### Apify cost

Every board search writes one row to `scraping_log` with:
- `apify_run_id`: the last Apify run of that search (open it in the Apify Console to see its log),
- `apify_runs`: how many Apify runs the search used (pages and retries),
- `apify_cost_usd`: what Apify charged for all of them.

The cost is Apify's own figure for each run, or the actor's charges (price per result or per
event x how many) when Apify hasn't added those to the run yet. If an actor's price depends on your
Apify plan, only Apify's figure is used, which can be too low right after the run. It is worth
comparing one run's total with the Apify Console once.

---

## Visa data

`visa_refresh` reads one file per folder (the first by name), except E-Verify where every file is read:

| Folder | File | Fills |
|---|---|---|
| `data/lca` | DOL LCA disclosure (.xlsx) | H-1B filings and average salary |
| `data/perm` | DOL PERM disclosure (.xlsx) | green card filings |
| `data/uscis-hub` | USCIS H-1B Employer Data Hub (.xlsx export) | H-1B approvals and denials |
| `data/everify` | E-Verify employer list (all files in the folder) | E-Verify enrolled yes / no |

Download the DOL files from https://www.dol.gov/agencies/eta/foreign-labor/performance. They are
cumulative for the fiscal year, so each load replaces the previous numbers instead of adding to
them. Running `visa_refresh` twice with the same files gives the same result.

After `visa_refresh`, run `python manage.py run_match --rematch-all` so jobs already saved get the
new data.

### Company aliases

Job postings often use a brand name ("Kforce Technology Staffing") while the visa data has the
legal filing name ("Kforce Inc"). The matcher can't safely guess these, so they are added by hand
after checking them (for example on myvisajobs.com):

```
python manage.py add_alias "Kforce Technology Staffing" "Kforce" "same company"
python manage.py run_match
```

`add_alias` checks that the filing name exists and saves the alias in the database. To keep it for
new installations too, also add it to the alias list at the end of `schema.sql`. That list
currently has 39 aliases, which `setup_db` loads into every new database.

A good time to look for missing aliases: after a run, check which big companies are still
unmatched (`visa_matched = false` in `us_jobs`).

---

## Using it from code (for the API later)

The commands are thin wrappers, so an API or a background task can call the same functions:

```python
from job_scraper.config.scrape_config import load_scrape_config, ScrapeConfigOverrides
from job_scraper.core.roles import distinct_roles
from job_scraper.jobs.role_scrape import run_role_scrape
from job_scraper.jobs.mark_inactive import mark_stale_jobs_inactive

cfg = load_scrape_config(ScrapeConfigOverrides(max='20', sources='linkedin,dice'))
roles = distinct_roles(['data analyst', 'iot engineer']).roles
summary = run_role_scrape(roles, cfg)        # scrape + visa matcher
mark_stale_jobs_inactive(cfg.inactiveAfterDays)
```

- A run takes minutes to hours, so an API should start it in the background and return right away.
- `core.log.set_streams(stdout=..., stderr=...)` sends a run's log somewhere else, e.g. a file.
- Read-only models are available for queries: `UsJob`, `CompanyVisaHistory`, `CompanyAlias`,
  `ScrapingLog`, e.g. `UsJob.objects.filter(h1b_sponsor=True, state_code='NY')`.
  Timestamps are stored as local time (`settings.TIME_ZONE`) without a zone.

---

## Folder layout

| Path | Contents |
|---|---|
| `management/commands/` | the `manage.py` commands |
| `config/` | board list and scrape settings |
| `core/` | database access, all SQL (`sql.py`), logging, roles file, helpers |
| `scraper/` | Apify client, one parser per board, the scrape loop (`job_scraper.py`) |
| `visa/` | government file loaders and scoring |
| `matcher/` | company name normalizing and the visa matcher |
| `jobs/` | daily scrape, visa refresh, mark inactive |
| `schema.sql` | table definitions |
| `models.py`, `routers.py`, `migrations/` | Django integration |
