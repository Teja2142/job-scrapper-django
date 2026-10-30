"""
JavaScript-compatible helpers.

This scraper was first written in TypeScript. Python and JavaScript give different results in a
few places (rounding, reading numbers and dates, turning values into text, regex), so the code
uses these helpers wherever that difference would change the data:

    js_round(2.5) == 3          Python's round(2.5) gives 2
    js_number('') == 0          float('') raises an error
    js_string(1.0) == '1'       str(1.0) gives '1.0'
    parse_js_date('2026-09-20') is midnight UTC, as JavaScript reads it
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

# The characters JavaScript's \s and String.prototype.trim() treat as whitespace.
JS_WS_CHARS = '\t\n\x0b\x0c\r                  　﻿'
_WS_CLASS_BODY = r'\t\n\x0b\x0c\r    -     　﻿'


def js_trim(s: str) -> str:
    """String.prototype.trim()."""
    return s.strip(JS_WS_CHARS)


def _translate_js_pattern(pattern: str) -> str:
    """
    Rewrites the regex tokens that mean something slightly different in Python:
    \\s and \\S use JavaScript's whitespace set, $ only matches at the very end, and . skips
    every line break. \\d, \\w and \\b become ASCII-only through re.ASCII (see jsre).
    """
    out: list[str] = []
    i = 0
    in_class = False
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == '\\' and i + 1 < n:
            nxt = pattern[i + 1]
            if nxt == 's':
                out.append(_WS_CLASS_BODY if in_class else f'[{_WS_CLASS_BODY}]')
            elif nxt == 'S' and not in_class:
                out.append(f'[^{_WS_CLASS_BODY}]')
            else:
                out.append(c + nxt)
            i += 2
            continue
        if in_class:
            if c == ']':
                in_class = False
            out.append(c)
        elif c == '[':
            in_class = True
            out.append(c)
        elif c == '$':
            out.append(r'\Z')
        elif c == '.':
            out.append('[^\\n\\r\\u2028\\u2029]')
        else:
            out.append(c)
        i += 1
    return ''.join(out)


_RE_CACHE: dict[tuple[str, str], re.Pattern] = {}


def jsre(pattern: str, flags: str = '') -> re.Pattern:
    """Compiles a regex written for JavaScript so it behaves the same in Python. flags: '' or 'i'."""
    key = (pattern, flags)
    compiled = _RE_CACHE.get(key)
    if compiled is None:
        f = re.ASCII
        if 'i' in flags:
            f |= re.IGNORECASE
        compiled = re.compile(_translate_js_pattern(pattern), f)
        _RE_CACHE[key] = compiled
    return compiled


def truthy(v: Any) -> bool:
    """JavaScript truthiness: only undefined/null, false, 0, NaN and '' are falsy."""
    if v is None or v is False:
        return False
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float, Decimal)):
        if isinstance(v, float) and math.isnan(v):
            return False
        return v != 0
    if isinstance(v, str):
        return v != ''
    return True  # an empty list or dict still counts as true in JavaScript


def js_or(*values: Any) -> Any:
    """a || b || c: the first truthy value, otherwise the last one."""
    for v in values[:-1]:
        if truthy(v):
            return v
    return values[-1] if values else None


def _format_js_float(x: float) -> str:
    if math.isnan(x):
        return 'NaN'
    if math.isinf(x):
        return 'Infinity' if x > 0 else '-Infinity'
    if x == 0:
        return '0'
    if x.is_integer() and abs(x) < 1e21:
        return str(int(x))
    r = repr(x)
    if 'e' in r:
        mant, exp = r.split('e')
        exp_i = int(exp)
        ax = abs(x)
        if 1e-7 <= ax < 1e21:
            return format(Decimal(r), 'f')
        return f"{mant}e{'+' if exp_i > 0 else '-'}{abs(exp_i)}"
    return r


def js_string(v: Any) -> str:
    """String(v)."""
    if v is None:
        return 'null'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, str):
        return v
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return _format_js_float(v)
    if isinstance(v, Decimal):
        return _format_js_float(float(v))
    if isinstance(v, (list, tuple)):
        return ','.join('' if x is None else js_string(x) for x in v)
    if isinstance(v, dict):
        return '[object Object]'
    if isinstance(v, datetime):
        return to_iso(v)
    return str(v)


def _as_js_int(x: float) -> int | float:
    """Whole numbers stay int, so they print and serialise as 1 rather than 1.0."""
    if isinstance(x, float) and x.is_integer() and abs(x) <= 2 ** 53:
        return int(x)
    return x


_JS_DECIMAL_RE = re.compile(r'[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)\Z', re.ASCII)


def js_number(v: Any) -> int | float:
    """Number(v). Whole numbers come back as int, others as float, unreadable text as NaN."""
    if v is None:
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return _as_js_int(v)
    if isinstance(v, Decimal):
        return _as_js_int(float(v))
    if isinstance(v, datetime):
        return date_ms(v)
    if isinstance(v, (list, tuple)):
        if len(v) == 0:
            return 0
        if len(v) == 1:
            return js_number(js_string(v[0]) if v[0] is not None else '')
        return math.nan
    if isinstance(v, str):
        s = js_trim(v)
        if s == '':
            return 0
        low = s.lower()
        try:
            if low.startswith('0x'):
                return int(s[2:], 16)
            if low.startswith('0o'):
                return int(s[2:], 8)
            if low.startswith('0b'):
                return int(s[2:], 2)
        except ValueError:
            return math.nan
        if s in ('Infinity', '+Infinity'):
            return math.inf
        if s == '-Infinity':
            return -math.inf
        if _JS_DECIMAL_RE.match(s):
            return _as_js_int(float(s))
        return math.nan
    return math.nan


def js_parse_int(v: Any) -> int | float:
    """parseInt(String(v), 10)."""
    s = js_string(v) if not isinstance(v, str) else v
    s = s.lstrip(JS_WS_CHARS)
    m = re.match(r'[+-]?\d+', s, re.ASCII)
    if not m:
        return math.nan
    return int(m.group(0))


def is_nan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


def is_finite(v: Any) -> bool:
    """Number.isFinite(v)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def js_round(x: Any) -> int | float:
    """Math.round(x): halves round up (2.5 -> 3, -2.5 -> -2)."""
    if isinstance(x, int) and not isinstance(x, bool):
        return x
    x = float(x)
    if not math.isfinite(x):
        return x
    f = math.floor(x)
    return int(f + 1) if x - f >= 0.5 else int(f)


def js_ceil(x: Any) -> int | float:
    x = float(x) if not isinstance(x, int) else x
    if isinstance(x, float) and not math.isfinite(x):
        return x
    return math.ceil(x)


def js_floor(x: Any) -> int | float:
    x = float(x) if not isinstance(x, int) else x
    if isinstance(x, float) and not math.isfinite(x):
        return x
    return math.floor(x)


def to_fixed(x: float, digits: int) -> str:
    """Number.toFixed(digits): halves round away from zero."""
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return js_string(x)
    q = Decimal(1).scaleb(-digits)
    d = Decimal(x).quantize(q, rounding=ROUND_HALF_UP)
    s = format(d, 'f')
    if s.startswith('-') and Decimal(s) == 0:
        s = s[1:]
    return s


def locale_int(x: int | float) -> str:
    """Math.ceil(x).toLocaleString('en-US')."""
    return f'{int(x):,}'


# Dates are handled as timezone-aware UTC datetimes, cut to milliseconds like a JavaScript Date.

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def now() -> datetime:
    """new Date()"""
    return _trunc_ms(datetime.now(timezone.utc))


def now_ms() -> int:
    """Date.now()"""
    return date_ms(datetime.now(timezone.utc))


def _trunc_ms(dt: datetime) -> datetime:
    return dt.replace(microsecond=(dt.microsecond // 1000) * 1000)


def date_ms(dt: datetime) -> int:
    """date.getTime()"""
    if dt.tzinfo is None:
        dt = dt.astimezone()  # a datetime without a zone is local time
    delta = dt - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def from_ms(ms: float) -> datetime | None:
    if not math.isfinite(ms) or abs(ms) > 8.64e15:
        return None
    return EPOCH + timedelta(milliseconds=int(ms))


def to_iso(dt: datetime) -> str:
    """date.toISOString()"""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{dt.microsecond // 1000:03d}Z'


def local_year(dt: datetime) -> int:
    """date.getFullYear() (local time)."""
    return dt.astimezone().year


def current_local_year() -> int:
    """new Date().getFullYear()"""
    return datetime.now().astimezone().year


def local_datetime(year: int, month0: int, day: int, hour: int = 0, minute: int = 0,
                   second: int = 0, ms: int = 0) -> datetime | None:
    """new Date(y, m, d, ...) in local time. Like JavaScript, month 12 rolls over to next January."""
    try:
        year += month0 // 12
        month0 = month0 % 12
        base = datetime(year, month0 + 1, 1)
        naive = base + timedelta(days=day - 1, hours=hour, minutes=minute, seconds=second, milliseconds=ms)
        return _trunc_ms(naive.astimezone().astimezone(timezone.utc))
    except (ValueError, OverflowError):
        return None


def _utc_datetime(year, month, day, hour=0, minute=0, second=0, ms=0) -> datetime | None:
    try:
        base = datetime(year, month, 1, tzinfo=timezone.utc)
        return base + timedelta(days=day - 1, hours=hour, minutes=minute, seconds=second, milliseconds=ms)
    except (ValueError, OverflowError):
        return None


_ISO_RE = re.compile(
    r'([+-]\d{6}|\d{4})(?:-(\d{2})(?:-(\d{2}))?)?'
    r'(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})?)?\Z',
    re.ASCII,
)

_TZINFOS = {
    'UTC': 0, 'UT': 0, 'GMT': 0, 'Z': 0,
    'EST': -5 * 3600, 'EDT': -4 * 3600, 'CST': -6 * 3600, 'CDT': -5 * 3600,
    'MST': -7 * 3600, 'MDT': -6 * 3600, 'PST': -8 * 3600, 'PDT': -7 * 3600,
}


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def _parse_iso(s: str) -> tuple[bool, datetime | None]:
    """Returns (matched, value). A date alone is read as UTC, a date and time without a zone as local."""
    m = _ISO_RE.match(s)
    if not m:
        return False, None
    y_s, mo_s, d_s, hh, mi, ss, frac, off = m.groups()
    year = int(y_s)
    month = int(mo_s) if mo_s else 1
    day = int(d_s) if d_s else 1
    if not (1 <= month <= 12) or day < 1:
        return True, None
    if day > _days_in_month(year, month):
        # JavaScript accepts up to day 31 and rolls over ("2026-02-30" becomes March 2)
        if day > 31:
            return True, None
    if hh is None:
        return True, _utc_datetime(year, month, day)
    h, mnt = int(hh), int(mi)
    sec = int(ss) if ss else 0
    ms = int((frac or '0')[:3].ljust(3, '0'))
    if h > 24 or mnt > 59 or sec > 59 or (h == 24 and (mnt or sec or ms)):
        return True, None
    if off is None:
        return True, local_datetime(year, month - 1, day, h, mnt, sec, ms)
    dt = _utc_datetime(year, month, day, h, mnt, sec, ms)
    if dt is None:
        return True, None
    if off != 'Z':
        sign = 1 if off[0] == '+' else -1
        oh, om = int(off[1:3]), int(off[4:6])
        dt = dt - sign * timedelta(hours=oh, minutes=om)
    return True, dt


def _parse_fallback(s: str) -> datetime | None:
    """Other formats ("Sep 20, 2026", "9/20/2026", "2026-09-20 10:00:00"). No zone means local time."""
    try:
        from dateutil import parser as du_parser
    except ImportError:  # pragma: no cover - python-dateutil is a requirement
        return None
    if not re.search(r'\d', s):
        return None
    if re.fullmatch(r'\d+', s):
        # How JavaScript reads a plain number: 0 is 2000, 1-12 a month of 2001, 13-31 invalid,
        # 32-49 is 20xx, 50-99 is 19xx, 100 and up is that year.
        n = int(s)
        if n == 0:
            return local_datetime(2000, 0, 1)
        if n <= 12:
            return local_datetime(2001, n - 1, 1)
        if n <= 31:
            return None
        if n <= 49:
            return local_datetime(2000 + n, 0, 1)
        if n <= 99:
            return local_datetime(1900 + n, 0, 1)
        return local_datetime(n, 0, 1) if n <= 9999 else None
    try:
        dt = du_parser.parse(s, tzinfos=_TZINFOS, default=datetime(2001, 1, 1))
    except (ValueError, OverflowError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return _trunc_ms(dt.astimezone(timezone.utc))


def parse_js_date(value: Any) -> datetime | None:
    """new Date(value), or None where JavaScript would give an invalid date."""
    if value is None or value is False or value is True:
        return None if value is not True else from_ms(1)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.astimezone()
        return _trunc_ms(value.astimezone(timezone.utc))
    if isinstance(value, date):
        return local_datetime(value.year, value.month - 1, value.day)
    if isinstance(value, (int, float, Decimal)):
        return from_ms(float(value))
    if isinstance(value, str):
        s = js_trim(value)
        if s == '':
            return None
        matched, dt = _parse_iso(s)
        if matched:
            return dt
        return _parse_fallback(s)
    return _parse_fallback(js_string(value))


def to_db_timestamp(dt: datetime | None):
    """
    The value to store in a TIMESTAMP (no time zone) column: local clock time. This is what the
    original app stored, so old and new rows can be compared directly.
    """
    if dt is None:
        return None
    return dt.astimezone().replace(tzinfo=None)


def json_stringify(v: Any, indent: int | None = None) -> str:
    """JSON.stringify(v[, null, indent])."""
    def default(o):
        if isinstance(o, datetime):
            return to_iso(o)
        if isinstance(o, Decimal):
            return _as_js_int(float(o))
        if isinstance(o, set):
            return {}
        return str(o)
    if indent is None:
        return json.dumps(v, separators=(',', ':'), ensure_ascii=False, default=default)
    return json.dumps(v, indent=indent, ensure_ascii=False, default=default)


def inspect(v: Any) -> str:
    """How a value is printed in the log, e.g. { processed: 1, skipped: 0 }."""
    if isinstance(v, str):
        return v
    return _inspect(v, top=True)


def _inspect(v: Any, top: bool = False) -> str:
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float, Decimal)):
        return js_string(v)
    if isinstance(v, str):
        return v if top else "'" + v.replace("'", "\\'") + "'"
    if isinstance(v, dict):
        if not v:
            return '{}'
        return '{ ' + ', '.join(f'{k}: {_inspect(x)}' for k, x in v.items()) + ' }'
    if isinstance(v, (list, tuple)):
        if not v:
            return '[]'
        return '[ ' + ', '.join(_inspect(x) for x in v) + ' ]'
    if isinstance(v, datetime):
        return to_iso(v)
    return str(v)


def unique(items: Iterable) -> list:
    """[...new Set(items)]"""
    return list(dict.fromkeys(items))
