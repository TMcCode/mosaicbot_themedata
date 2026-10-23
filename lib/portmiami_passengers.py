"""PortMiami monthly cruise passenger counts (GMCVB Research PDFs).

Source: GMCVB Research & Statistics → PortMiami Passengers PDF
  https://www.gmcvb.com/partners/research-statistics

Each PDF is a year×month grid. We discover the latest link on the research
page, merge with known archive URLs (denser / newer PDF wins per YYYY-MM).
"""

from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "mosaicbot_themedata/0.1 (+lib.portmiami_passengers)"
)

RESEARCH_PAGES = (
    "https://www.gmcvb.com/partners/research-statistics",
    "https://www.miamiandbeaches.com/gmcvb-partners/research-statistics-reporting",
)

# Archive + fallback grids (newest-first). Research-page discovery prepends
# the current PortMiami PDF so we do not stall on a stale March release.
CANDIDATE_PDFS = (
    "https://www.gmcvb.com/getContentAsset/0f207298-5565-4bcc-af01-8f8e936c06da/d2f5d494-bd4d-4e38-980b-01592bc8e1cd/2026_Sep_PortMiami.pdf?language=en-US",
    "https://www.miamiandbeaches.com/getmedia/e30f487c-b099-4cdc-bfa0-9710f947dd10/2026_March_Port_of_Miami.pdf",
    "https://www.miamiandbeaches.com/getmedia/d892a66b-eef3-4a74-b136-86b2d07c5799/2025_Dec_Port_of_Miami.pdf",
    "https://www.miamiandbeaches.com/getmedia/7ac4f49a-abc9-4f3d-90e6-6e3d9b6bc6c7/2025_March_PortMiami.pdf",
    "https://www.miamiandbeaches.com/getmedia/ab32e68a-472c-4475-b46d-a09ec2200df9/April_2024_Port-of-Miami.pdf",
)

_PORTMIAMI_HREF = re.compile(
    r"""href=["']([^"']*(?:PortMiami|Port_of_Miami|Port-of-Miami)[^"']*\.pdf[^"']*)["']""",
    re.I,
)

_MONTHS = (
    "JANUARY",
    "FEBRUARY",
    "MARCH",
    "APRIL",
    "MAY",
    "JUNE",
    "JULY",
    "AUGUST",
    "SEPTEMBER",
    "OCTOBER",
    "NOVEMBER",
    "DECEMBER",
)
_MONTH_NUM = {m: i + 1 for i, m in enumerate(_MONTHS)}
_YEAR_HDR = re.compile(
    r"^(20\d{2})(?:\s+20\d{2})+\s+(?:\d{2}\s+vs\s+\d{2})?\s*$",
    re.M,
)
_YEAR_TOK = re.compile(r"\b(20\d{2})\b")
HISTORY_NAME = "portmiami_passengers_history.json"


def _get_bytes(url: str, *, timeout: float = 60) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/pdf,*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _get_text(url: str, *, timeout: float = 45) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "text/html,*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def discover_portmiami_pdf_urls() -> list[str]:
    """Pull current PortMiami passenger PDF hrefs from GMCVB research pages."""
    found: list[str] = []
    seen: set[str] = set()
    for page in RESEARCH_PAGES:
        try:
            html = _get_text(page)
        except Exception:
            continue
        base = f"{urllib.parse.urlparse(page).scheme}://{urllib.parse.urlparse(page).netloc}"
        for m in _PORTMIAMI_HREF.finditer(html):
            href = m.group(1).strip()
            if href.startswith("/"):
                href = base + href
            # Drop query noise for dedupe; keep full URL for fetch.
            key = href.split("?")[0]
            if key in seen:
                continue
            seen.add(key)
            found.append(href)
    return found


def candidate_urls(
    extra: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    """Newest discovery links first, then static archive fallbacks."""
    out: list[str] = []
    seen: set[str] = set()
    for url in list(discover_portmiami_pdf_urls()) + list(extra or CANDIDATE_PDFS):
        key = url.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def parse_pdf_bytes(raw: bytes) -> dict[str, float]:
    """Return ``{YYYY-MM-01: passengers}`` from one GMCVB Port Miami PDF."""
    text = ""
    for page in PdfReader(io.BytesIO(raw)).pages:
        text += (page.extract_text() or "") + "\n"
    years_m = _YEAR_HDR.search(text)
    if not years_m:
        # Fallback: first line of years after BUREAU header
        for line in text.splitlines():
            toks = _YEAR_TOK.findall(line)
            if len(toks) >= 4:
                years = [int(y) for y in toks]
                break
        else:
            raise RuntimeError("no year header in Port Miami PDF")
    else:
        years = [int(y) for y in _YEAR_TOK.findall(years_m.group(0))]

    out: dict[str, float] = {}
    for month in _MONTHS:
        # Capture integers before the trailing YoY % (optional).
        pat = re.compile(
            rf"^{month}\s+((?:\d[\d,]*(?:\s+|$))+?)(-?\d+\.\d+%|)$",
            re.M,
        )
        m = pat.search(text)
        if not m:
            # looser: month + numbers
            m2 = re.search(
                rf"^{month}\s+((?:\d[\d,]*\s+)+)",
                text,
                re.M,
            )
            if not m2:
                continue
            nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", m2.group(1))]
            yoy = None
        else:
            nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", m.group(1))]
            yoy = m.group(2) or None

        # When current-year month is blank, PDF omits the cell and numbers
        # shift left — detect via -100.0% YoY (GMCVB placeholder).
        align_years = years
        if yoy == "-100.0%" and len(nums) == len(years) - 1:
            align_years = years[1:]
        elif len(nums) < len(years):
            align_years = years[: len(nums)]
        elif len(nums) > len(years):
            nums = nums[: len(years)]

        for y, v in zip(align_years, nums):
            # Skip obvious empty/current placeholders already handled;
            # keep real zeros (COVID months).
            d = f"{y:04d}-{_MONTH_NUM[month]:02d}-01"
            out[d] = float(v)
    if not out:
        raise RuntimeError("no month rows parsed from Port Miami PDF")
    return out


def fetch_from_candidates(
    urls: tuple[str, ...] | list[str] | None = None,
) -> tuple[dict[str, float], list[str]]:
    """Fetch+parse candidate PDFs; merge with newer PDFs winning per month.

    URLs are newest-first; we apply oldest→newest so the latest published
    grid overwrites stale cells.
    """
    url_list = list(urls) if urls is not None else candidate_urls()
    merged: dict[str, float] = {}
    notes: list[str] = []
    parsed: list[tuple[str, dict[str, float]]] = []
    for url in url_list:
        name = urllib.parse.unquote(url.split("/")[-1].split("?")[0])
        try:
            raw = _get_bytes(url)
            pts = parse_pdf_bytes(raw)
            parsed.append((url, pts))
            notes.append(f"ok {name} months={len(pts)}")
        except Exception as e:
            notes.append(f"skip {name}: {e}")
    # urls listed newest-first → apply reversed so newest wins.
    for _url, pts in reversed(parsed):
        merged.update(pts)
    if not merged:
        raise RuntimeError("PortMiami PDF scrape empty: " + "; ".join(notes[:4]))
    return merged, notes


def load_history(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    pts = data.get("points") or {}
    return {str(k): float(v) for k, v in pts.items()}


def save_history(path: Path, points: dict[str, float], *, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "points": {k: points[k] for k in sorted(points)},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def refresh_monthly(
    *,
    history_path: Path,
    start: str = "2014-01-01",
    urls: tuple[str, ...] | list[str] | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Merge PDF scrape into local history; return ascending point list."""
    prior = load_history(history_path)
    scraped, notes = fetch_from_candidates(urls)
    # Prefer scraped values for overlapping months (newer PDF).
    merged = {**prior, **scraped}
    meta = {
        "n_months": len(merged),
        "scrape_notes": notes,
        "source": "GMCVB Port Miami monthly passenger count PDF",
        "as_of": max(merged) if merged else None,
    }
    save_history(history_path, merged, meta=meta)
    pts = [
        {"date": d, "value": v}
        for d, v in sorted(merged.items())
        if d >= start
    ]
    return pts, meta
