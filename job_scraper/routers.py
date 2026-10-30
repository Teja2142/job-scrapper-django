"""
Optional. Only needed when the app's tables are in a separate database (JOB_SCRAPER_DB_ALIAS is
not "default") and the project's own code queries the models. Add to settings:

    DATABASE_ROUTERS = ['job_scraper.routers.JobScraperRouter']

The scraper itself doesn't need it.
"""
from .core import conf

APP_LABEL = 'job_scraper'


class JobScraperRouter:
    def db_for_read(self, model, **hints):
        return conf.db_alias() if model._meta.app_label == APP_LABEL else None

    def db_for_write(self, model, **hints):
        return conf.db_alias() if model._meta.app_label == APP_LABEL else None

    def allow_relation(self, obj1, obj2, **hints):
        if obj1._meta.app_label == APP_LABEL and obj2._meta.app_label == APP_LABEL:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == APP_LABEL:
            return db == conf.db_alias()
        return None
