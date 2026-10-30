from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field

from .jscompat import js_trim, jsre


@dataclass
class DistinctRolesResult:
    # Cleaned, de-duplicated roles, in first-seen order.
    roles: list = field(default_factory=list)
    received: int = 0
    duplicatesRemoved: int = 0
    # The repeats that were dropped, for logging.
    duplicates: list = field(default_factory=list)
    # Blank or non-text entries that were dropped.
    ignored: int = 0


def distinct_roles(items: list) -> DistinctRolesResult:
    """
    Makes roles distinct, ignoring upper/lower case and extra spaces ("Data Engineer" and
    "data  engineer" are one role). The first spelling seen is kept.
    """
    seen: set = set()
    roles: list = []
    duplicates: list = []
    ignored = 0
    ws = jsre(r'\s+')

    for item in items:
        if not isinstance(item, str):
            ignored += 1
            continue
        cleaned = ws.sub(' ', js_trim(unicodedata.normalize('NFKC', item)))
        if not cleaned:
            ignored += 1
            continue
        key = cleaned.lower()
        if key in seen:
            duplicates.append(cleaned)
            continue
        seen.add(key)
        roles.append(cleaned)

    return DistinctRolesResult(roles=roles, received=len(items), duplicatesRemoved=len(duplicates),
                               duplicates=duplicates, ignored=ignored)


def read_roles_file(file_path: str) -> list:
    """
    Reads a roles file. Either a JSON array / { "roles": [...] }, or plain text with one role
    per line (blank lines and # comments skipped), whatever the file extension.
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f'Roles file not found: {abs_path}')
    with open(abs_path, 'r', encoding='utf-8', errors='replace', newline='') as f:
        body = f.read()
    body = re.sub(r'^﻿', '', body, count=1)
    trimmed = js_trim(body)

    if trimmed.startswith('[') or trimmed.startswith('{'):
        try:
            data = json.loads(trimmed)
        except ValueError as e:
            raise ValueError(f'{file_path} looks like JSON but could not be parsed: {e}') from None
        lst = data if isinstance(data, list) else (data.get('roles') if isinstance(data, dict) else None)
        if not isinstance(lst, list):
            raise ValueError(f'{file_path}: expected a JSON array of roles, or an object like {{ "roles": [...] }}')
        return lst

    lines = [js_trim(line) for line in re.split(r'\r?\n', body)]
    return [line for line in lines if line and not line.startswith('#')]
