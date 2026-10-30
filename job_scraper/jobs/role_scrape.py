from __future__ import annotations

from dataclasses import dataclass, field

from ..core import log
from ..matcher.match_jobs_to_visa import match_jobs_to_visa
from ..scraper.job_scraper import SHARED_QUEUE, ScrapeRoleResult, scrape_role, scrape_roles_shared


@dataclass
class RoleScrapeSummary:
    results: list = field(default_factory=list)
    totalJobs: int = 0


def run_role_scrape(roles: list, cfg, run_matcher: bool = True) -> RoleScrapeSummary:
    """
    Scrapes every role (already distinct) with the given settings, then runs the visa matcher
    once. A role that fails is logged and the rest still run.
    """
    summary = RoleScrapeSummary()
    counted = 'new ' if cfg.countMode == 'inserted' else ''

    if SHARED_QUEUE and cfg.limitScope == 'per_board':
        for res in scrape_roles_shared(roles, cfg):
            summary.results.append(res)
            summary.totalJobs += res.total
            log.info(
                f'[roleScrape] "{res.role}": {res.total}/{res.cap} {counted}job(s)'
                + ('' if res.capReached else ' (limit not reached -- boards ran out of fresh jobs within the fetch limit)')
            )
        roles_to_run = []
    else:
        roles_to_run = roles

    for role in roles_to_run:
        log.info(f'\n--- role: "{role}" ---')
        try:
            res = scrape_role(role, cfg)
            summary.results.append(res)
            summary.totalJobs += res.total
            log.info(
                f'[roleScrape] "{role}": {res.total}/{res.cap} {counted}job(s)'
                + ('' if res.capReached else ' (limit not reached -- boards ran out of fresh jobs within the fetch limit)')
            )
        except Exception as e:
            log.error(f'[roleScrape] "{role}" failed:', str(e))
            summary.results.append(ScrapeRoleResult(
                role=role, total=0, cap=0, capReached=False, bySource={}, capBySource={},
                failedSources=list(cfg.sources), details={},
            ))

    if run_matcher:
        match_jobs_to_visa()
    log.info(f'\n[roleScrape] done: {summary.totalJobs} {counted}job(s) across {len(roles)} role(s)')
    return summary
