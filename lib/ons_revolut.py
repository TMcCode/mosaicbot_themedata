"""ONS Revolut debit-card spending (real-time indicators) — monthly SA by sector.

Dataset page:
https://www.ons.gov.uk/economy/economicoutputandproductivity/output/datasets/revolutspendingondebitcards

``Restaurants`` MCC bucket includes pubs, nightclubs, restaurants and fast food
(younger / metropolitan Revolut panel — chart + score with that caveat).
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+ons_revolut)"
DATASET_PAGE = (
    "https://www.ons.gov.uk/economy/economicoutputandproductivity/output/"
    "datasets/revolutspendingondebitcards"
)
_MONTH = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _latest_xlsx_url() -> str:
    req = Request(DATASET_PAGE, headers={"User-Agent": UA, "Accept": "text/html"})
    with urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    # Prefer newest /2026/… then /2025/…
    hrefs = re.findall(
        r'href="(/file\?uri=/economy/economicoutputandproductivity/output/'
        r'datasets/revolutspendingondebitcards/\d{4}/[^"]+\.xlsx)"',
        html,
        flags=re.I,
    )
    if not hrefs:
        raise RuntimeError("ONS Revolut dataset: no xlsx href found")
    # First match on page is usually the latest edition.
    return "https://www.ons.gov.uk" + hrefs[0]


def _parse_month_label(label: str) -> str | None:
    s = (label or "").strip()
    m = re.match(r"^([A-Za-z]+)\s+(\d{4})$", s)
    if not m:
        return None
    mon = _MONTH.get(m.group(1)[:3].lower())
    if not mon:
        return None
    return f"{int(m.group(2)):04d}-{mon:02d}-01"


def fetch_monthly_sector(
    sector: str = "Restaurants",
    *,
    seasonally_adjusted: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return monthly index points (2023 avg = 100) for one sector column."""
    import openpyxl

    xlsx_url = _latest_xlsx_url()
    req = Request(xlsx_url, headers={"User-Agent": UA})
    with urlopen(req, timeout=120) as resp:
        blob = resp.read()
    wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    sheet = "5.Monthly by Sector SA" if seasonally_adjusted else "4.Monthly by Sector NSA"
    if sheet not in wb.sheetnames:
        raise RuntimeError(f"ONS Revolut workbook missing sheet {sheet!r}")
    ws = wb[sheet]
    header: list[str] | None = None
    col: int | None = None
    points: list[dict[str, Any]] = []
    for row in ws.iter_rows(values_only=True):
        cells = list(row)
        if not cells or cells[0] is None:
            continue
        if str(cells[0]).strip() == "Date":
            header = [str(c).strip() if c is not None else "" for c in cells]
            try:
                col = header.index(sector)
            except ValueError as e:
                raise RuntimeError(
                    f"sector {sector!r} not in Revolut headers {header}"
                ) from e
            continue
        if header is None or col is None:
            continue
        d = _parse_month_label(str(cells[0]))
        if not d:
            continue
        val = cells[col] if col < len(cells) else None
        if val in (None, "", "[x]"):
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        points.append({"date": d, "value": v})
    points.sort(key=lambda p: p["date"])
    meta = {
        "source": "ons_revolut",
        "xlsx_url": xlsx_url,
        "sheet": sheet,
        "sector": sector,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "n": len(points),
    }
    return points, meta
