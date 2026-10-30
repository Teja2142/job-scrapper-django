"""
Creates the tables by running schema.sql (the same as python manage.py setup_db). Every statement
is IF NOT EXISTS / ON CONFLICT DO NOTHING, so existing data is never changed.

Only runs on the database named by JOB_SCRAPER_DB_ALIAS (default "default") and only on
PostgreSQL. For another alias: python manage.py migrate job_scraper --database=<alias>
"""
import os

from django.db import migrations, models


def create_schema(apps, schema_editor):
    from job_scraper.core import conf

    connection = schema_editor.connection
    if connection.alias != conf.db_alias():
        return
    if connection.vendor != 'postgresql':
        raise RuntimeError(
            f'job_scraper needs PostgreSQL, but database "{connection.alias}" is {connection.vendor}. '
            'Point JOB_SCRAPER_DB_ALIAS at a PostgreSQL entry in DATABASES.'
        )
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'schema.sql')
    with open(path, encoding='utf-8') as f:
        sql = f.read()
    with connection.cursor() as cur:
        cur.execute(sql)


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.RunPython(create_schema, reverse_code=migrations.RunPython.noop),
        # The models are unmanaged, so these only record state; no SQL runs.
        migrations.CreateModel(
            name='CompanyAlias',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('alias_norm', models.CharField(max_length=300, unique=True)),
                ('target_norm', models.CharField(max_length=300)),
                ('notes', models.CharField(max_length=500, null=True)),
                ('created_at', models.DateTimeField(null=True)),
            ],
            options={
                'db_table': 'company_aliases',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='CompanyVisaHistory',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('company_name', models.CharField(max_length=300)),
                ('company_name_norm', models.CharField(max_length=300, unique=True)),
                ('h1b_total_filings', models.IntegerField(default=0, null=True)),
                ('h1b_approvals', models.IntegerField(default=0, null=True)),
                ('h1b_denials', models.IntegerField(default=0, null=True)),
                ('h1b_approval_rate', models.DecimalField(decimal_places=2, max_digits=5, null=True)),
                ('h1b_avg_salary', models.IntegerField(null=True)),
                ('h1b_last_filing_year', models.IntegerField(null=True)),
                ('perm_total_filings', models.IntegerField(default=0, null=True)),
                ('perm_last_year', models.IntegerField(null=True)),
                ('green_card_sponsor', models.BooleanField(default=False, null=True)),
                ('everify_enrolled', models.BooleanField(null=True)),
                ('visa_friendly_score', models.IntegerField(default=0, null=True)),
                ('visa_grade', models.CharField(max_length=2, null=True)),
                ('updated_at', models.DateTimeField(null=True)),
            ],
            options={
                'db_table': 'company_visa_history',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='ScrapingLog',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('run_type', models.CharField(max_length=50, null=True)),
                ('source', models.CharField(max_length=50, null=True)),
                ('search_query', models.CharField(max_length=300, null=True)),
                ('started_at', models.DateTimeField(null=True)),
                ('ended_at', models.DateTimeField(null=True)),
                ('jobs_found', models.IntegerField(default=0, null=True)),
                ('jobs_inserted', models.IntegerField(default=0, null=True)),
                ('jobs_updated', models.IntegerField(default=0, null=True)),
                ('jobs_duplicate', models.IntegerField(default=0, null=True)),
                ('status', models.CharField(max_length=20, null=True)),
                ('error_message', models.TextField(null=True)),
                ('apify_run_id', models.CharField(max_length=200, null=True)),
                ('created_at', models.DateTimeField(null=True)),
            ],
            options={
                'db_table': 'scraping_log',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='UsJob',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('job_id', models.CharField(max_length=300, unique=True)),
                ('content_hash', models.CharField(max_length=64, null=True)),
                ('is_duplicate', models.BooleanField(default=False, null=True)),
                ('canonical_job_id', models.BigIntegerField(null=True)),
                ('title', models.CharField(max_length=500)),
                ('company_name', models.CharField(max_length=300)),
                ('company_name_norm', models.CharField(max_length=300, null=True)),
                ('job_url', models.TextField(null=True)),
                ('job_description', models.TextField(null=True)),
                ('location_raw', models.CharField(max_length=500, null=True)),
                ('city', models.CharField(max_length=150, null=True)),
                ('state', models.CharField(max_length=100, null=True)),
                ('state_code', models.CharField(max_length=5, null=True)),
                ('is_remote', models.BooleanField(default=False, null=True)),
                ('is_hybrid', models.BooleanField(default=False, null=True)),
                ('country', models.CharField(default='USA', max_length=50, null=True)),
                ('salary_min', models.IntegerField(null=True)),
                ('salary_max', models.IntegerField(null=True)),
                ('salary_currency', models.CharField(default='USD', max_length=10, null=True)),
                ('salary_period', models.CharField(max_length=20, null=True)),
                ('salary_present', models.BooleanField(default=False, null=True)),
                ('experience_min_years', models.IntegerField(null=True)),
                ('employment_type', models.CharField(max_length=50, null=True)),
                ('source', models.CharField(max_length=50)),
                ('posting_date', models.DateTimeField(null=True)),
                ('posted_days_ago', models.IntegerField(null=True)),
                ('visa_matched', models.BooleanField(default=False, null=True)),
                ('h1b_sponsor', models.BooleanField(null=True)),
                ('h1b_filings_recent', models.IntegerField(null=True)),
                ('everify_enrolled', models.BooleanField(null=True)),
                ('green_card_sponsor', models.BooleanField(null=True)),
                ('visa_grade', models.CharField(max_length=2, null=True)),
                ('visa_friendly_score', models.IntegerField(null=True)),
                ('is_active', models.BooleanField(default=True, null=True)),
                ('first_seen_at', models.DateTimeField(null=True)),
                ('last_scraped_at', models.DateTimeField(null=True)),
                ('updated_at', models.DateTimeField(null=True)),
            ],
            options={
                'db_table': 'us_jobs',
                'managed': False,
            },
        ),
    ]
