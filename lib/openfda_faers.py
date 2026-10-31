"""openFDA FAERS — monthly adverse-event report counts for a drug brand.

Free public API. Uses per-month ``search`` + ``meta.results.total`` instead of
``count=receiptdate`` pagination (count+skip often 403s without a key).

Default query targets Epidiolex (Jazz cannabidiol) via openFDA brand_name —
post-market safety pulse for the theme’s commercial proof product.
"""

from __future__ import annotations

import calendar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

UA = "mosaicbot_themedata/0.1 (+lib.openfda_faers; openFDA research use)"
BASE = "https://api.fda.gov/drug/event.json"

DEFAULT_SEARCH = 'patient.drug.openfda.brand_name:"EPIDIOLEX"'


def _api_key() -> str | None:
    for env in ("OPENFDA_API_KEY", "FDA_API_KEY"):
        v = (os.environ.get(env) or "").strip()
        if v:
            return v
    return None


def _get(params: dict[str, str]) -> dict[str, Any]:
    key = _api_key()
    if key:
        params = {**params, "api_key": key}
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    last: Exception | None = None
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 404 or "NOT_FOUND" in body:
                return {"meta": {"results": {"total": 0}}, "results": []}
            if e.code in (403, 429):
                time.sleep(1.5 * (i + 1))
                continue
            raise RuntimeError(f"openFDA FAERS HTTP {e.code}: {body[:200]}") from e
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    assert last is not None
    raise last


def _month_ends(start: date, end: date) -> list[tuple[date, date]]:
    """Inclusive calendar months as (first, last) day pairs."""
    out: list[tuple[date, date]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = date(y, m, 1)
        last = date(y, m, calendar.monthrange(y, m)[1])
        out.append((first, last))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def monthly_ae_counts(
    *,
    search: str | None = None,
    count_field: str = "receiptdate",  # kept for catalog compat; unused
    start: str = "20180101",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Monthly FAERS report counts matching ``search``.

    ``start`` is YYYYMMDD. Each month is a separate openFDA search with
    ``limit=1``; we read ``meta.results.total`` (no report bodies downloaded).
    """
    del count_field  # API uses date-range totals, not count= field
    q = (search or DEFAULT_SEARCH).strip()
    try:
        start_d = date(int(start[:4]), int(start[4:6]), 1)
    except Exception:
        start_d = date(2018, 1, 1)
    today = date.today()
    end_d = date(today.year, today.month, 1)
    pts: list[dict[str, Any]] = []
    notes: list[str] = []
    zeros = 0
    for first, last in _month_ends(start_d, end_d):
        if first > today:
            break
        lo = first.strftime("%Y%m%d")
        hi = last.strftime("%Y%m%d")
        month_q = f"{q} AND receiptdate:[{lo} TO {hi}]"
        try:
            payload = _get({"search": month_q, "limit": "1"})
        except Exception as e:
            notes.append(f"faers {lo}: {e}")
            time.sleep(0.35)
            continue
        total = int(((payload.get("meta") or {}).get("results") or {}).get("total") or 0)
        ym = first.isoformat()
        if total > 0:
            pts.append({"date": ym, "value": float(total)})
        else:
            zeros += 1
        time.sleep(0.25 if _api_key() else 0.4)

    if zeros and not pts:
        notes.append("faers: no matches")
    meta = {
        "source": "openfda.drug.event",
        "search": q,
        "method": "monthly_meta_total",
        "months": len(pts),
        "zero_months": zeros,
        "notes": notes,
        "as_of": pts[-1]["date"] if pts else None,
        "api_key": bool(_api_key()),
    }
    return pts, meta
