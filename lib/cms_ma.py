"""CMS Medicare Advantage enrollment helpers.

Primary: data.cms.gov *Medicare Monthly Enrollment* API — national
``MA_AND_OTH_BENES`` monthly history (2013→).

Secondary: CMS Monthly Contract Summary XLSX zips — MA subtotal contract
count (plan-footprint proxy for exits / shopping intensity). Append-only
local history; refresh recent months on each run.
"""

from __future__ import annotations

import calendar
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

UA = "mosaicbot_themedata/0.1 (+cms_ma)"
_ENROLL_API = (
    "https://data.cms.gov/data-api/v1/dataset/"
    "d7fabe1e-d19b-4333-9eff-e80e0643f2fd/data"
)
_ZIP_TMPL = (
    "https://www.cms.gov/files/zip/monthly-contract-summary-report-{slug}.zip"
)
_MONTH_SLUGS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def _get_json(url: str, *, timeout: int = 90) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _get_bytes(url: str, *, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _month_to_num(month: str) -> int | None:
    m = str(month or "").strip()
    if not m or m.lower() == "year":
        return None
    if m.isdigit():
        n = int(m)
        return n if 1 <= n <= 12 else None
    try:
        return list(calendar.month_name).index(m)
    except ValueError:
        pass
    try:
        return list(calendar.month_abbr).index(m[:3].title())
    except ValueError:
        return None


def _num(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def fetch_national_ma_enrollment(
    *,
    field: str = "MA_AND_OTH_BENES",
    start: str = "2013-01-01",
) -> list[dict[str, Any]]:
    """National monthly Medicare Advantage & Other Health Plans enrollment.

    Returns points dated YYYY-MM-01 with ``value`` = beneficiary count.
    """
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        q = urllib.parse.urlencode(
            {
                "filter[BENE_GEO_LVL]": "National",
                "size": "1000",
                "offset": str(offset),
            }
        )
        batch = _get_json(f"{_ENROLL_API}?{q}")
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
        time.sleep(0.15)
        if offset > 20000:
            break

    by_date: dict[str, float] = {}
    for r in rows:
        if str(r.get("MONTH") or "").strip().lower() == "year":
            continue
        mi = _month_to_num(str(r.get("MONTH") or ""))
        try:
            yi = int(str(r.get("YEAR") or ""))
        except (TypeError, ValueError):
            continue
        if not mi or not yi:
            continue
        v = _num(r.get(field))
        if v is None:
            continue
        d = f"{yi:04d}-{mi:02d}-01"
        if d < start:
            continue
        by_date[d] = v
    return [{"date": d, "value": by_date[d]} for d in sorted(by_date)]


def fetch_national_ma_penetration(
    *,
    start: str = "2013-01-01",
) -> list[dict[str, Any]]:
    """MA & Other / Total Medicare beneficiaries (%), national monthly."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        q = urllib.parse.urlencode(
            {
                "filter[BENE_GEO_LVL]": "National",
                "size": "1000",
                "offset": str(offset),
            }
        )
        batch = _get_json(f"{_ENROLL_API}?{q}")
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
        time.sleep(0.15)
        if offset > 20000:
            break

    by_date: dict[str, float] = {}
    for r in rows:
        if str(r.get("MONTH") or "").strip().lower() == "year":
            continue
        mi = _month_to_num(str(r.get("MONTH") or ""))
        try:
            yi = int(str(r.get("YEAR") or ""))
        except (TypeError, ValueError):
            continue
        if not mi or not yi:
            continue
        ma = _num(r.get("MA_AND_OTH_BENES"))
        tot = _num(r.get("TOT_BENES"))
        if ma is None or not tot:
            continue
        d = f"{yi:04d}-{mi:02d}-01"
        if d < start:
            continue
        by_date[d] = round(100.0 * ma / tot, 4)
    return [{"date": d, "value": by_date[d]} for d in sorted(by_date)]


def _parse_contract_summary_xlsx(raw: bytes) -> dict[str, Any] | None:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), data_only=True)
    ws = wb.worksheets[0]
    contracts = None
    ma_enrollees = None
    snp = None
    for row in ws.iter_rows(values_only=True):
        label = str(row[1] or "").strip() if len(row) > 1 else ""
        if label == "MA Subtotal":
            # Number of Contracts, MA Only, Drug, Total Enrollees, SNP...
            contracts = _num(row[2] if len(row) > 2 else None)
            ma_enrollees = _num(row[5] if len(row) > 5 else None)
            snp = _num(row[6] if len(row) > 6 else None)
            break
    if contracts is None and ma_enrollees is None:
        return None
    return {
        "ma_contracts": contracts,
        "ma_enrollees": ma_enrollees,
        "snp_enrollees": snp,
    }


def _month_iter(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def refresh_contract_summary_history(
    history_path: Path,
    *,
    backfill_months: int = 36,
    refresh_recent: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Append CMS Monthly Contract Summary metrics to a local JSON history.

    Returns (points for ``ma_contracts`` dated YYYY-MM-01, meta).
    """
    history_path.parent.mkdir(parents=True, exist_ok=True)
    by: dict[str, dict[str, Any]] = {}
    if history_path.exists():
        try:
            raw = json.loads(history_path.read_text(encoding="utf-8"))
            for row in raw.get("points") or []:
                d = str(row.get("date") or "")[:10]
                if d:
                    by[d] = row
        except Exception:
            pass

    today = date.today()
    # Aim for last closed CMS month (file often lags ~1 month).
    end = date(today.year, today.month, 1)
    if today.day < 15:
        # previous month more likely published
        if end.month == 1:
            end = date(end.year - 1, 12, 1)
        else:
            end = date(end.year, end.month - 1, 1)

    start_y = end.year
    start_m = end.month - max(backfill_months, refresh_recent) + 1
    while start_m <= 0:
        start_m += 12
        start_y -= 1
    start = date(start_y, start_m, 1)

    notes: list[str] = []
    fetched = 0
    skipped = 0
    failures = 0
    # Only fetch missing months + last refresh_recent even if present.
    recent_cutoff = end
    for _ in range(refresh_recent - 1):
        if recent_cutoff.month == 1:
            recent_cutoff = date(recent_cutoff.year - 1, 12, 1)
        else:
            recent_cutoff = date(
                recent_cutoff.year, recent_cutoff.month - 1, 1
            )

    for y, m in _month_iter(start, end):
        d = f"{y:04d}-{m:02d}-01"
        need = d not in by or d >= recent_cutoff.isoformat()
        if not need:
            skipped += 1
            continue
        slug = f"{_MONTH_SLUGS[m - 1]}-{y}"
        url = _ZIP_TMPL.format(slug=slug)
        try:
            blob = _get_bytes(url)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                xlsx_name = next(
                    (
                        n
                        for n in zf.namelist()
                        if n.lower().endswith(".xlsx")
                    ),
                    None,
                )
                if not xlsx_name:
                    raise RuntimeError("no xlsx in zip")
                parsed = _parse_contract_summary_xlsx(zf.read(xlsx_name))
            if not parsed:
                raise RuntimeError("could not parse MA Subtotal row")
            by[d] = {"date": d, **parsed, "source_url": url}
            fetched += 1
            time.sleep(0.35)
        except urllib.error.HTTPError as e:
            failures += 1
            notes.append(f"{d}: HTTP {e.code}")
        except Exception as e:
            failures += 1
            notes.append(f"{d}: {e}")

    points = [by[d] for d in sorted(by)]
    history_path.write_text(
        json.dumps(
            {
                "schema_version": 0,
                "source": "cms_monthly_contract_summary",
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "points": points,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    contract_pts = [
        {"date": p["date"], "value": float(p["ma_contracts"])}
        for p in points
        if p.get("ma_contracts") is not None
    ]
    meta = {
        "fetched": fetched,
        "skipped_cached": skipped,
        "failures": failures,
        "n_history": len(points),
        "notes": notes[-12:],
    }
    return contract_pts, meta
