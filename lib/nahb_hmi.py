"""NAHB/Wells Fargo Housing Market Index (HMI) — public history table scrape.

Source page publishes monthly Table 2 (national HMI history) as ``.xls``.
We download that table on each run and rebuild the monthly series (full history
available today; append/refresh going forward).

Proprietary NAHB print — chart/display with attribution; do not treat as
redistributable bulk data. Same posture as the NAR existing-home-sales archive.

Page: https://www.nahb.org/news-and-economics/housing-economics/indices/Housing-Market-Index
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import xlrd

UA = "mosaicbot_themedata/0.1 (+lib.nahb_hmi; read-only)"
PAGE_URL = (
    "https://www.nahb.org/news-and-economics/housing-economics/"
    "indices/Housing-Market-Index"
)
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def fetch_page_html(*, timeout: float = 60) -> str:
    req = Request(
        PAGE_URL,
        headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def history_xls_url(html: str | None = None) -> str:
    """Resolve absolute URL for Table 2 national HMI history ``.xls``."""
    body = html if html is not None else fetch_page_html()
    # Prefer the national history table (t2-…history…).
    m = re.search(
        r'href="([^"]*t2-national-hmi-history[^"]+\.xls[^"]*)"',
        body,
        re.I,
    )
    if not m:
        m = re.search(
            r'href="([^"]*national-hmi-history[^"]+\.xls[^"]*)"',
            body,
            re.I,
        )
    if not m:
        raise RuntimeError("NAHB HMI history .xls link not found on page")
    return urljoin(PAGE_URL, unescape(m.group(1)))


def fetch_history_xls(*, url: str | None = None, timeout: float = 60) -> bytes:
    target = url or history_xls_url()
    req = Request(
        target,
        headers={"User-Agent": UA, "Accept": "application/vnd.ms-excel,*/*"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_history_xls(data: bytes) -> list[dict[str, float]]:
    """Parse year×month grid → ``[{date: YYYY-MM-01, value}, …]`` ascending."""
    wb = xlrd.open_workbook(file_contents=data)
    sh = wb.sheet_by_index(0)
    # Header row with month labels.
    header_row = None
    for r in range(min(6, sh.nrows)):
        vals = [str(sh.cell_value(r, c)).strip() for c in range(min(13, sh.ncols))]
        if vals[1:13] == list(_MONTHS):
            header_row = r
            break
    if header_row is None:
        raise RuntimeError("NAHB HMI history: month header row not found")

    pts: list[dict[str, float]] = []
    for r in range(header_row + 1, sh.nrows):
        year_raw = sh.cell_value(r, 0)
        if year_raw in ("", None):
            continue
        try:
            year = int(float(year_raw))
        except (TypeError, ValueError):
            continue
        if year < 1980 or year > 2100:
            continue
        for mi, _label in enumerate(_MONTHS, start=1):
            cell = sh.cell_value(r, mi)
            if cell in ("", None):
                continue
            try:
                val = float(cell)
            except (TypeError, ValueError):
                continue
            pts.append({"date": f"{year:04d}-{mi:02d}-01", "value": val})
    pts.sort(key=lambda p: p["date"])
    if not pts:
        raise RuntimeError("NAHB HMI history: no monthly points parsed")
    return pts


def scrape_national_hmi(*, start: str | None = None) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Fetch + parse Table 2. Optional ``start`` (YYYY-MM-DD) filters points."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    html = fetch_page_html()
    xls_url = history_xls_url(html)
    raw = fetch_history_xls(url=xls_url)
    pts = parse_history_xls(raw)
    if start:
        pts = [p for p in pts if str(p["date"]) >= start]
    meta = {
        "source": PAGE_URL,
        "history_xls": xls_url,
        "fetched_at": fetched_at,
        "n": len(pts),
        "latest": pts[-1] if pts else None,
        "notes": (
            "NAHB/Wells Fargo HMI — public Table 2 history. "
            "Proprietary; display with attribution; not for bulk redistribution."
        ),
    }
    return pts, meta
