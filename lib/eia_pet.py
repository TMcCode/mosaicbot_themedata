"""EIA Weekly Petroleum Status Report series via public hist_xls downloads.

No API key required — each series is a small Excel workbook under
``https://www.eia.gov/dnav/pet/hist_xls/{SERIES}w.xls``.
"""

from __future__ import annotations

import io
import urllib.request
from datetime import datetime, timedelta
from typing import Any

UA = "mosaicbot_themedata/0.1 (+eia_pet; research read-only)"
_BASE = "https://www.eia.gov/dnav/pet/hist_xls"

# Common WPSR weekly series used by Energy Bottleneck / US refiners.
EIA_WEEKLY: dict[str, dict[str, str]] = {
    "WCESTUS1": {
        "label": "US crude stocks ex-SPR",
        "unit": "mbbl",
        "scale": "1e3",  # published as thousand barrels → convert to mmbbl below
    },
    "WGTSTUS1": {
        "label": "US total gasoline stocks",
        "unit": "mbbl",
        "scale": "1e3",
    },
    "WDISTUS1": {
        "label": "US distillate stocks",
        "unit": "mbbl",
        "scale": "1e3",
    },
    "WKJSTUS1": {
        "label": "US jet fuel stocks",
        "unit": "mbbl",
        "scale": "1e3",
    },
    "WPULEUS3": {
        "label": "US refinery utilization",
        "unit": "pct",
        "scale": "1",
    },
    "WCRRIUS2": {
        "label": "US refiner crude net inputs",
        "unit": "kbpd",
        "scale": "1",
    },
    "WDIEXUS2": {
        "label": "US distillate exports",
        "unit": "kbpd",
        "scale": "1",
    },
    "WDIUPUS2": {
        "label": "US distillate product supplied",
        "unit": "kbpd",
        "scale": "1",
    },
    "WGFUPUS2": {
        "label": "US gasoline product supplied",
        "unit": "kbpd",
        "scale": "1",
    },
    "WDIRPUS2": {
        "label": "US distillate production",
        "unit": "kbpd",
        "scale": "1",
    },
}


def _http_bytes(url: str, *, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _excel_date(serial: float) -> str:
    # Excel / xlrd 1900 date system
    dt = datetime(1899, 12, 30) + timedelta(days=int(serial))
    return dt.date().isoformat()


def fetch_weekly_series(
    series_id: str,
    *,
    start: str | None = None,
    to_mmbbl: bool = True,
) -> list[dict[str, Any]]:
    """Return ``[{date, value}, ...]`` for an EIA weekly PET series.

    Stock series (thousand barrels) are converted to million barrels when
    ``to_mmbbl`` is True and meta scale is 1e3.
    """
    sid = series_id.strip().upper()
    meta = EIA_WEEKLY.get(sid) or {}
    url = f"{_BASE}/{sid}w.xls"
    raw = _http_bytes(url)
    try:
        import xlrd
    except ImportError as e:
        raise RuntimeError("xlrd required for EIA hist_xls") from e

    wb = xlrd.open_workbook(file_contents=raw)
    sh = wb.sheet_by_name("Data 1") if "Data 1" in wb.sheet_names() else wb.sheet_by_index(1)
    scale = float(meta.get("scale") or "1")
    # Stocks: publish as mmbbl for chart readability
    if to_mmbbl and scale == 1e3:
        scale = 1e3  # divide by 1000 → mmbbl
        divisor = 1000.0
    else:
        divisor = 1.0

    pts: list[dict[str, Any]] = []
    for r in range(3, sh.nrows):
        d_cell = sh.cell_value(r, 0)
        v_cell = sh.cell_value(r, 1)
        if d_cell in ("", None) or v_cell in ("", None):
            continue
        if isinstance(d_cell, float):
            d = _excel_date(d_cell)
        else:
            d = str(d_cell)[:10]
        try:
            v = float(v_cell) / divisor
        except (TypeError, ValueError):
            continue
        if start and d < start:
            continue
        pts.append({"date": d, "value": round(v, 3)})
    pts.sort(key=lambda p: p["date"])
    return pts


def fetch_many(
    series_ids: list[str],
    *,
    start: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    out: dict[str, list[dict[str, Any]]] = {}
    notes: list[str] = []
    for sid in series_ids:
        try:
            pts = fetch_weekly_series(sid, start=start)
            out[sid] = pts
            notes.append(f"EIA {sid}: {len(pts)} weeks")
        except Exception as e:
            notes.append(f"EIA {sid}: fail ({e})")
    return out, notes
