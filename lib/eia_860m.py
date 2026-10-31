"""EIA-860M monthly generator inventory — utility-scale battery MW + duration.

Downloads monthly Excel workbooks (Operating sheet). For ``Technology ==
Batteries``, aggregates nameplate MW and duration = energy_MWh / power_MW when
both are present. History is appended locally; each run refreshes recent months
and backfills gaps (empty pre-existence windows are skips, not failures).
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
from calendar import month_name
from datetime import date
from pathlib import Path
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.eia_860m; EIA-860M research use)"
_BASE = "https://www.eia.gov/electricity/data/eia860m"
HISTORY_NAME = "eia860m_battery_history.json"


def _month_urls(year: int, month: int) -> list[str]:
    label = month_name[month].lower()
    fname = f"{label}_generator{year}.xlsx"
    return [
        f"{_BASE}/archive/xls/{fname}",
        f"{_BASE}/xls/{fname}",
    ]


def _get_bytes(url: str, *, timeout: float = 180) -> bytes | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    if not raw or raw[:2] != b"PK":
        return None
    return raw


def _parse_operating_batteries(raw: bytes) -> dict[str, float]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    if "Operating" not in wb.sheetnames:
        raise RuntimeError("EIA-860M workbook missing Operating sheet")
    ws = wb["Operating"]
    rows = ws.iter_rows(values_only=True)
    header: list[str] | None = None
    for row in rows:
        if row and any(c and "Technology" in str(c) for c in row):
            header = [str(c).strip() if c else "" for c in row]
            break
    if not header:
        raise RuntimeError("EIA-860M Operating header not found")
    try:
        ti = header.index("Technology")
        mi = header.index("Nameplate Capacity (MW)")
        ei = header.index("Nameplate Energy Capacity (MWh)")
    except ValueError as e:
        raise RuntimeError(f"EIA-860M missing capacity columns: {e}") from e

    mw_tot = 0.0
    mw_ge4 = 0.0
    mw_ge6 = 0.0
    mw_ge8 = 0.0
    mw_with_dur = 0.0
    n_units = 0
    for row in rows:
        if not row or str(row[ti] or "") != "Batteries":
            continue
        try:
            mw = float(row[mi] or 0.0)
        except (TypeError, ValueError):
            continue
        if mw <= 0:
            continue
        n_units += 1
        mw_tot += mw
        mwh_raw = row[ei]
        if mwh_raw in (None, ""):
            continue
        try:
            mwh = float(mwh_raw)
        except (TypeError, ValueError):
            continue
        if mwh <= 0:
            continue
        dur = mwh / mw
        mw_with_dur += mw
        if dur >= 4:
            mw_ge4 += mw
        if dur >= 6:
            mw_ge6 += mw
        if dur >= 8:
            mw_ge8 += mw

    def _pct(part: float) -> float:
        if mw_with_dur <= 0:
            return 0.0
        return round(100.0 * part / mw_with_dur, 4)

    return {
        "operating_mw": round(mw_tot, 4),
        "share_ge_4h_pct": _pct(mw_ge4),
        "share_ge_6h_pct": _pct(mw_ge6),
        "share_ge_8h_pct": _pct(mw_ge8),
        "mw_with_duration": round(mw_with_dur, 4),
        "n_units": float(n_units),
    }


def _load_history(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 0, "months": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": 0, "months": {}}
    if not isinstance(data, dict):
        return {"schema_version": 0, "months": {}}
    data.setdefault("months", {})
    return data


def _save_history(path: Path, hist: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hist, indent=2) + "\n", encoding="utf-8")


def refresh_battery_metrics(
    *,
    history_path: Path,
    start: str = "2022-01",
    refresh_months: int = 4,
    backfill_if_empty: int = 36,
    sleep_s: float = 0.35,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Refresh EIA-860M battery metrics; return series map + meta.

    Series keys: ``operating_mw``, ``share_ge_4h_pct``, ``share_ge_6h_pct``,
    ``share_ge_8h_pct``.
    """
    hist = _load_history(history_path)
    months: dict[str, Any] = dict(hist.get("months") or {})
    today = date.today()
    # Candidate YYYY-MM from start through prior calendar month.
    start_y, start_m = int(start[:4]), int(start[5:7])
    curs = date(start_y, start_m, 1)
    end = date(today.year, today.month, 1)
    # Walk back one month — current month file is often unpublished.
    if end.month == 1:
        end = date(end.year - 1, 12, 1)
    else:
        end = date(end.year, end.month - 1, 1)

    all_keys: list[str] = []
    while curs <= end:
        all_keys.append(f"{curs.year:04d}-{curs.month:02d}")
        if curs.month == 12:
            curs = date(curs.year + 1, 1, 1)
        else:
            curs = date(curs.year, curs.month + 1, 1)

    if not months and backfill_if_empty > 0:
        want = set(all_keys[-backfill_if_empty:])
    else:
        want = set(all_keys[-max(refresh_months, 1) :])
        # Also fill any holes in the recent window.
        for k in all_keys[-max(backfill_if_empty, refresh_months) :]:
            if k not in months:
                want.add(k)

    fetched = 0
    skipped = 0
    errors: list[str] = []
    for key in sorted(want):
        y, m = int(key[:4]), int(key[5:7])
        raw = None
        used = None
        for url in _month_urls(y, m):
            raw = _get_bytes(url)
            if raw:
                used = url
                break
            time.sleep(sleep_s)
        if not raw:
            skipped += 1
            continue
        try:
            metrics = _parse_operating_batteries(raw)
        except Exception as e:
            errors.append(f"{key}: {e}")
            continue
        months[key] = {
            **metrics,
            "source_url": used,
            "fetched_at": date.today().isoformat(),
        }
        fetched += 1
        time.sleep(sleep_s)

    hist["months"] = months
    hist["updated_at"] = date.today().isoformat()
    _save_history(history_path, hist)

    series: dict[str, list[dict[str, Any]]] = {
        "operating_mw": [],
        "share_ge_4h_pct": [],
        "share_ge_6h_pct": [],
        "share_ge_8h_pct": [],
    }
    for key in sorted(months):
        row = months[key]
        # Point date = month-end-ish first of next? Use month-01 period_end.
        d = f"{key}-01"
        if d < f"{start}-01"[:7] + "-01" and key < start:
            continue
        for sk in series:
            if sk in row and row[sk] is not None:
                series[sk].append({"date": d, "value": float(row[sk])})

    meta = {
        "source": "eia.gov/electricity/data/eia860m",
        "fetched_months": fetched,
        "skipped_missing": skipped,
        "n_history": len(months),
        "errors": errors,
        "notes": [
            "Duration = Nameplate Energy Capacity (MWh) / Nameplate Capacity (MW).",
            "Shares use only units that report energy capacity.",
        ],
    }
    return series, meta
