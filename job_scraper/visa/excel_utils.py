"""
Excel helpers for the government files. The big DOL files are read row by row (openpyxl read-only
mode), so memory stays low whatever the file size.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterator

from ..core import log
from ..core.jscompat import js_string, js_trim, json_stringify, jsre, parse_js_date, truthy

# Excel counts days from Dec 30, 1899 (it wrongly treats 1900 as a leap year).
EXCEL_EPOCH_UTC = datetime(1899, 12, 30, tzinfo=timezone.utc)


def resolve_excel_date(raw) -> datetime | None:
    """Turns a cell value (date, Excel day number or text) into a UTC datetime."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    if isinstance(raw, time):
        return EXCEL_EPOCH_UTC + timedelta(hours=raw.hour, minutes=raw.minute, seconds=raw.second)
    if isinstance(raw, bool):
        return parse_js_date(raw)
    if isinstance(raw, (int, float)):
        try:
            return EXCEL_EPOCH_UTC + timedelta(milliseconds=round(raw * 86400000))
        except (OverflowError, ValueError):
            return None
    return parse_js_date(js_string(raw))


def iter_xlsx_rows(file_path: str) -> Iterator[list]:
    """
    Every row of every sheet, as a list of cell values (index 0 = column A). The sheet's stored size
    is ignored because DOL files don't always set it correctly.
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            try:
                ws.reset_dimensions()
            except AttributeError:
                pass
            for row in ws.iter_rows(values_only=True):
                yield list(row)
    finally:
        wb.close()


def read_header_row(values: list) -> list:
    """Header names, trimmed (the files have headers like "Fiscal Year   ")."""
    return [js_trim(js_string(v)) if truthy(v) else None for v in values]


def index_of(headers: list, name: str) -> int:
    """Position of `name` in headers, or -1."""
    try:
        return headers.index(name)
    except ValueError:
        return -1


def cell(values: list, col: int):
    """The value in column `col` (None if the column was not found or the row is shorter)."""
    if col < 0 or col >= len(values):
        return None
    return values[col]


_MISSING = object()


def _sheetjs_values(cells) -> list:
    """Cell values, keeping the difference between an empty cell ('') and a missing cell."""
    out = []
    for c in cells:
        if type(c).__name__ == 'EmptyCell':
            out.append(_MISSING)
        elif c.value is None and getattr(c, 'data_type', None) in ('s', 'inlineStr', 'str'):
            out.append('')
        else:
            out.append(c.value)
    return out


def read_excel_rows(file_path: str) -> list:
    """
    Reads a small .xlsx file's first sheet into a list of dicts keyed by the header row. Don't use it
    for the big DOL files; use iter_xlsx_rows() instead. A repeated header gets "_1", "_2", ... and an
    empty header becomes "__EMPTY", "__EMPTY_1", ...
    """
    import openpyxl

    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        try:
            ws.reset_dimensions()
        except AttributeError:
            pass
        rows_iter = (_sheetjs_values(r) for r in ws.iter_rows())
        header_row = None
        for r in rows_iter:
            if any(v is not _MISSING for v in r):
                header_row = list(r)
                break
        if header_row is None:
            return []
        while header_row and header_row[-1] is _MISSING:
            header_row.pop()

        keys: list = []
        seen: dict = {}
        for v in header_row:
            base = '__EMPTY' if v is _MISSING or v is None else js_string(v)
            if base in seen:
                seen[base] += 1
                key = f'{base}_{seen[base]}'
            else:
                seen[base] = 0
                key = base
            keys.append(key)

        out: list = []
        for r in rows_iter:
            if all(v is _MISSING for v in r):  # a row with no cells at all
                continue
            out.append({k: (r[i] if i < len(r) and r[i] is not _MISSING else None) for i, k in enumerate(keys)})
        return out
    finally:
        wb.close()


def get_field(row: dict, candidates: list):
    """
    Gets a value by trying several possible column names (case and spaces/underscores ignored),
    because DOL renames columns between fiscal years.
    """
    sep = jsre(r'[\s_]+')

    def normalize(s: str) -> str:
        return sep.sub('', s.lower())

    for candidate in candidates:
        if row.get(candidate) is not None:
            return row[candidate]

    normalized_candidates = [normalize(c) for c in candidates]
    for key in row.keys():
        if normalize(key) in normalized_candidates:
            return row[key]
    return None


def log_column_headers_streaming(file_path: str, sample_rows: int = 2) -> None:
    """
    Prints the header row and a couple of sample rows of any file, even a huge one. Use it on a new
    DOL file before trusting the parser.
    """
    row_index = 0
    samples: list = []
    for values in iter_xlsx_rows(file_path):
        row_index += 1
        if row_index == 1:
            headers = read_header_row(values)
            log.info(f'[logColumnHeadersStreaming] {file_path}')
            log.info('[logColumnHeadersStreaming] Columns found:', [h for h in headers if h])
            continue
        if len(samples) < sample_rows:
            samples.append(values)
        else:
            break
    log.info('[logColumnHeadersStreaming] Sample row(s):', json_stringify(samples, indent=2))
