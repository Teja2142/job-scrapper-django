"""
Console output. info() goes to stdout, warn() and error() to stderr. Everything goes through
here, so a later API can capture a run's log with set_streams() without touching other code.
"""
from __future__ import annotations

import sys
import threading
from typing import Any

from .jscompat import inspect, js_string

_lock = threading.Lock()
_stdout = None
_stderr = None


def set_streams(stdout=None, stderr=None) -> None:
    """Redirect output (None = back to sys.stdout / sys.stderr)."""
    global _stdout, _stderr
    _stdout, _stderr = stdout, stderr


def _write(stream, args: tuple) -> None:
    text = ' '.join(inspect(a) for a in args)
    with _lock:
        stream.write(text + '\n')
        stream.flush()


def info(*args: Any) -> None:
    _write(_stdout or sys.stdout, args)


def warn(*args: Any) -> None:
    _write(_stderr or sys.stderr, args)


def error(*args: Any) -> None:
    _write(_stderr or sys.stderr, args)


class Plain(str):
    """A table value printed as-is (without the quotes strings normally get)."""


def _cell(v: Any) -> str:
    if isinstance(v, Plain):
        return str(v)
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, str):
        return "'" + v + "'"
    return js_string(v)


def table(rows: list[dict]) -> None:
    """Prints a list of dicts as a table."""
    columns: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in columns:
                columns.append(k)
    header = ['(index)'] + columns
    body = [[str(i)] + [(_cell(r[c]) if c in r else '') for c in columns] for i, r in enumerate(rows)]
    widths = [max(len(h), *(len(b[j]) for b in body)) + 2 if body else len(h) + 2 for j, h in enumerate(header)]

    def line(left, mid, right):
        return left + mid.join('─' * w for w in widths) + right

    def row(cells):
        return '│' + '│'.join(' ' + c.ljust(w - 1) for c, w in zip(cells, widths)) + '│'

    out = [line('┌', '┬', '┐'), row(header), line('├', '┼', '┤')]
    out += [row(b) for b in body]
    out.append(line('└', '┴', '┘'))
    with _lock:
        s = _stdout or sys.stdout
        s.write('\n'.join(out) + '\n')
        s.flush()
