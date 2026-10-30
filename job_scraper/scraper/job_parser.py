from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass
class NormalizedJob:
    """One job in the shape of the us_jobs table, whichever board it came from."""
    jobId: str
    contentHash: str
    title: str
    companyName: str
    companyNameNorm: str
    jobUrl: object
    jobDescription: object
    locationRaw: object
    city: object
    state: object
    stateCode: object
    isRemote: bool
    isHybrid: bool
    salaryMin: object
    salaryMax: object
    salaryPeriod: object
    salaryPresent: bool
    employmentType: object
    source: str
    postingDate: datetime | None

    def as_dict(self) -> dict:
        return asdict(self)


def parse_job(raw, source: str) -> NormalizedJob | None:
    """Picks the parser for the board's actor. LinkedIn, Indeed, Glassdoor, ... share the multi-board parser."""
    from .careerbuilder_parser import parse_careerbuilder_row
    from .dice_parser import parse_dice_row
    from .jobright_parser import parse_jobright_row
    from .monster_parser import parse_monster_row
    from .multi_job_board_parser import parse_multi_job_board_row
    from .ziprecruiter_parser import parse_ziprecruiter_row

    if source == 'dice':
        return parse_dice_row(raw, source)
    if source == 'monster':
        return parse_monster_row(raw, source)
    if source == 'careerbuilder':
        return parse_careerbuilder_row(raw, source)
    if source == 'ziprecruiter':
        return parse_ziprecruiter_row(raw, source)
    if source == 'jobright':
        return parse_jobright_row(raw, source)
    return parse_multi_job_board_row(raw, source)


def get(raw, key):
    """raw.key for a row from an actor (None when missing, like undefined)."""
    if isinstance(raw, dict):
        return raw.get(key)
    return None
