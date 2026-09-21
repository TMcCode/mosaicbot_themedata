"""USGS Mineral Commodity Summaries — germanium annual prices from public PDFs."""

from __future__ import annotations

import re
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+usgs_mcs)"

# Prefer versioned then bare filenames; try several recent MCS years.
_PDF_CANDIDATES = [
    "https://pubs.usgs.gov/periodicals/mcs{y}/mcs{y}-germanium.pdf",
    "https://pubs.usgs.gov/periodicals/mcs{y}/mcs{y}-germanium_ver.1.0.pdf",
    "https://pubs.usgs.gov/periodicals/mcs{y}/mcs{y}-germanium_ver.1.1.pdf",
]


def _fetch_pdf_bytes(url: str) -> bytes | None:
    req = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except HTTPError:
        return None
    if not raw.startswith(b"%PDF"):
        return None
    return raw


def _pdf_text(raw: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as e:
        raise RuntimeError("pypdf required for USGS MCS PDFs — pip install pypdf") from e
    import io

    reader = PdfReader(io.BytesIO(raw))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _parse_metal_prices(text: str) -> dict[int, float]:
    """Parse germanium metal annual $/kg under the MCS price block only."""
    # Years header: "Salient Statistics—United States: 2021 2022 2023 2024 2025e"
    m_years = re.search(
        r"Salient Statistics[^\n]*:\s*((?:\d{4}e?\s+){4}\d{4}e?)",
        text,
        re.I,
    )
    if not m_years:
        return {}
    years = [int(re.sub(r"\D", "", y)) for y in m_years.group(1).split()]
    if len(years) != 5:
        return {}

    # Restrict to the price subsection so we don't pick import kg volumes.
    m_block = re.search(
        r"Price,\s*annual average,\s*dollars per kilogram:(.*?)(?:Net import reliance|Recycling:)",
        text,
        re.I | re.S,
    )
    if not m_block:
        return {}
    block = m_block.group(1)

    m_price = re.search(
        r"Germanium metal\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+"
        r"([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)",
        block,
        re.I,
    )
    if not m_price:
        return {}
    out: dict[int, float] = {}
    for y, raw in zip(years, m_price.groups()):
        out[y] = float(raw.replace(",", ""))
    return out


def fetch_germanium_metal_price_annual(
    *,
    start_year: int = 2015,
    end_year: int | None = None,
    mcs_years: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Annual average germanium metal price (USD/kg) from USGS MCS PDFs.

    Newer MCS editions overwrite older estimates for overlapping years.
    """
    from datetime import datetime, timezone

    now_y = datetime.now(timezone.utc).year
    end_year = end_year or now_y
    # MCS year Y contains prices for Y-4..Y (with Y often estimated)
    years_to_try = mcs_years or list(range(now_y + 1, now_y - 8, -1))

    by_year: dict[int, float] = {}
    sources: list[str] = []
    for y in years_to_try:
        for tmpl in _PDF_CANDIDATES:
            url = tmpl.format(y=y)
            raw = _fetch_pdf_bytes(url)
            if not raw:
                continue
            prices = _parse_metal_prices(_pdf_text(raw))
            if not prices:
                continue
            sources.append(url)
            for yr, px in prices.items():
                # Prefer first (newest MCS) write; don't overwrite
                by_year.setdefault(yr, px)
            break

    points = [
        {"date": f"{yr:04d}-01-01", "value": float(px)}
        for yr, px in sorted(by_year.items())
        if start_year <= yr <= end_year
    ]
    if not points:
        raise RuntimeError(
            "No germanium prices parsed from USGS MCS PDFs "
            f"(tried MCS years {years_to_try[:6]}…)"
        )
    return points
