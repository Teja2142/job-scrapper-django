"""
Read-only models over the app's tables, for the project's own code and the future API, e.g.
UsJob.objects.filter(h1b_sponsor=True, state_code='NY').

The tables are created by schema.sql, so these models are unmanaged: Django never changes the
tables. If the tables are in a database other than "default", use .using(<alias>) or the router
in routers.py.
"""
from django.db import models


class UsJob(models.Model):
    id = models.BigAutoField(primary_key=True)
    job_id = models.CharField(max_length=300, unique=True)
    content_hash = models.CharField(max_length=64, null=True)
    is_duplicate = models.BooleanField(null=True, default=False)
    canonical_job_id = models.BigIntegerField(null=True)
    title = models.CharField(max_length=500)
    company_name = models.CharField(max_length=300)
    company_name_norm = models.CharField(max_length=300, null=True)
    job_url = models.TextField(null=True)
    job_description = models.TextField(null=True)
    location_raw = models.CharField(max_length=500, null=True)
    city = models.CharField(max_length=150, null=True)
    state = models.CharField(max_length=100, null=True)
    state_code = models.CharField(max_length=5, null=True)
    is_remote = models.BooleanField(null=True, default=False)
    is_hybrid = models.BooleanField(null=True, default=False)
    country = models.CharField(max_length=50, null=True, default='USA')
    salary_min = models.IntegerField(null=True)
    salary_max = models.IntegerField(null=True)
    salary_currency = models.CharField(max_length=10, null=True, default='USD')
    salary_period = models.CharField(max_length=20, null=True)
    salary_present = models.BooleanField(null=True, default=False)
    experience_min_years = models.IntegerField(null=True)
    employment_type = models.CharField(max_length=50, null=True)
    source = models.CharField(max_length=50)
    posting_date = models.DateTimeField(null=True)
    posted_days_ago = models.IntegerField(null=True)
    visa_matched = models.BooleanField(null=True, default=False)
    h1b_sponsor = models.BooleanField(null=True)
    h1b_filings_recent = models.IntegerField(null=True)
    everify_enrolled = models.BooleanField(null=True)
    green_card_sponsor = models.BooleanField(null=True)
    visa_grade = models.CharField(max_length=2, null=True)
    visa_friendly_score = models.IntegerField(null=True)
    is_active = models.BooleanField(null=True, default=True)
    first_seen_at = models.DateTimeField(null=True)
    last_scraped_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = 'us_jobs'

    def __str__(self):
        return f'{self.title} @ {self.company_name}'


class CompanyVisaHistory(models.Model):
    id = models.BigAutoField(primary_key=True)
    company_name = models.CharField(max_length=300)
    company_name_norm = models.CharField(max_length=300, unique=True)
    h1b_total_filings = models.IntegerField(null=True, default=0)
    h1b_approvals = models.IntegerField(null=True, default=0)
    h1b_denials = models.IntegerField(null=True, default=0)
    h1b_approval_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    h1b_avg_salary = models.IntegerField(null=True)
    h1b_last_filing_year = models.IntegerField(null=True)
    perm_total_filings = models.IntegerField(null=True, default=0)
    perm_last_year = models.IntegerField(null=True)
    green_card_sponsor = models.BooleanField(null=True, default=False)
    everify_enrolled = models.BooleanField(null=True)
    visa_friendly_score = models.IntegerField(null=True, default=0)
    visa_grade = models.CharField(max_length=2, null=True)
    updated_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = 'company_visa_history'

    def __str__(self):
        return self.company_name


class CompanyAlias(models.Model):
    id = models.BigAutoField(primary_key=True)
    alias_norm = models.CharField(max_length=300, unique=True)
    target_norm = models.CharField(max_length=300)
    notes = models.CharField(max_length=500, null=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = 'company_aliases'

    def __str__(self):
        return f'{self.alias_norm} -> {self.target_norm}'


class ScrapingLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    run_type = models.CharField(max_length=50, null=True)
    source = models.CharField(max_length=50, null=True)
    search_query = models.CharField(max_length=300, null=True)
    started_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField(null=True)
    jobs_found = models.IntegerField(null=True, default=0)
    jobs_inserted = models.IntegerField(null=True, default=0)
    jobs_updated = models.IntegerField(null=True, default=0)
    jobs_duplicate = models.IntegerField(null=True, default=0)
    status = models.CharField(max_length=20, null=True)
    error_message = models.TextField(null=True)
    apify_run_id = models.CharField(max_length=200, null=True)
    apify_runs = models.IntegerField(null=True, default=0)
    apify_cost_usd = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = 'scraping_log'
