"""Adds apify_runs and apify_cost_usd to scraping_log on databases created before they existed."""
from django.db import migrations, models

SQL = """
ALTER TABLE scraping_log ADD COLUMN IF NOT EXISTS apify_runs INTEGER DEFAULT 0;
ALTER TABLE scraping_log ADD COLUMN IF NOT EXISTS apify_cost_usd NUMERIC(10,4);
"""


def add_columns(apps, schema_editor):
    from job_scraper.core import conf

    connection = schema_editor.connection
    if connection.alias != conf.db_alias() or connection.vendor != 'postgresql':
        return
    with connection.cursor() as cur:
        cur.execute(SQL)


class Migration(migrations.Migration):
    dependencies = [('job_scraper', '0001_initial')]
    operations = [
        migrations.RunPython(add_columns, reverse_code=migrations.RunPython.noop),
        migrations.AddField(model_name='scrapinglog', name='apify_runs',
                            field=models.IntegerField(default=0, null=True)),
        migrations.AddField(model_name='scrapinglog', name='apify_cost_usd',
                            field=models.DecimalField(decimal_places=4, max_digits=10, null=True)),
    ]
