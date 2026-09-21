"""Caldara–Iacoviello Geopolitical Risk (GPR) monthly fetch."""

from __future__ import annotations

from typing import Any
from urllib.request import Request, urlopen

GPR_XLS_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls"
UA = "mosaicbot_themedata/0.1 (+gpr)"


def fetch_gpr_monthly(*, start: str = "2015-01-01") -> list[dict[str, Any]]:
    """Return [{date, value}] for monthly GPR (classic index column)."""
    try:
        import xlrd  # type: ignore
    except ImportError as e:
        raise RuntimeError("xlrd required for GPR .xls — pip install xlrd") from e

    req = Request(GPR_XLS_URL, headers={"User-Agent": UA})
    with urlopen(req, timeout=90) as resp:
        raw = resp.read()
    book = xlrd.open_workbook(file_contents=raw)
    sheet = book.sheet_by_index(0)
    headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
    try:
        i_month = headers.index("month")
        i_gpr = headers.index("GPR")
    except ValueError as e:
        raise RuntimeError(f"GPR sheet missing columns: {headers[:10]}") from e

    points: list[dict[str, Any]] = []
    for r in range(1, sheet.nrows):
        month_cell = sheet.cell_value(r, i_month)
        gpr = sheet.cell_value(r, i_gpr)
        if gpr in ("", None) or month_cell in ("", None):
            continue
        try:
            y, m, *_ = xlrd.xldate_as_tuple(float(month_cell), book.datemode)
            date = f"{y:04d}-{m:02d}-01"
        except Exception:
            continue
        if date < start:
            continue
        points.append({"date": date, "value": float(gpr)})
    points.sort(key=lambda p: p["date"])
    return points
