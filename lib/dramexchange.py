"""DRAMeXchange / TrendForce public spot board scrape.

Public homepage prints **latest session only** (die / module / flash / GDDR).
We scrape session average + board as_of, then append to local/R2 history
(AISI-style). Not a TrendForce Gold redistribution — public HTML print.

Source: https://www.dramexchange.com/
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+lib.dramexchange)"
URL = "https://www.dramexchange.com/"

# Catalog key → product label on the public board (whitespace-normalized match).
PRODUCTS: dict[str, str] = {
    "ddr5_16gb_die_spot": "DDR5 16Gb (2Gx8) 4800/5600",
    "ddr5_rdimm_32gb_spot": "DDR5 RDIMM 32GB 4800/5600",
    "ddr4_16gb_die_spot": "DDR4 16Gb (2Gx8) 3200",
    "ddr5_udimm_16gb_spot": "DDR5 UDIMM 16GB 4800/5600",
    "nand_512gb_tlc_spot": "512Gb TLC",
    "nand_256gb_tlc_spot": "256Gb TLC",
    "microsd_128gb_spot": "MicroSD 128GB",
    # Homepage prints double-space labels ("GDDR5  8Gb"); matcher collapses ws.
    "gddr5_8gb_spot": "GDDR5 8Gb",
    "gddr6_8gb_spot": "GDDR6 8Gb",
}

_MONTH = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _parse_board_date(raw: str) -> str:
    """``Oct.19 2026 18:10 (GMT+8)`` → ``YYYY-MM-DD``."""
    m = re.search(
        r"([A-Za-z]+)\.?\s*(\d{1,2})\s+(\d{4})",
        (raw or "").strip(),
    )
    if not m:
        raise ValueError(f"unparseable DRAMeXchange date: {raw!r}")
    mon = _MONTH.get(m.group(1).lower().rstrip("."))
    if not mon:
        raise ValueError(f"unknown month in DRAMeXchange date: {raw!r}")
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"


def fetch_html(*, timeout: float = 60) -> str:
    req = Request(
        URL,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _product_pattern(product: str) -> str:
    """Escape product label; allow flexible whitespace (board uses double spaces)."""
    parts = [re.escape(p) for p in str(product).split() if p]
    if not parts:
        raise ValueError(f"empty DRAMeXchange product label: {product!r}")
    return r"\s+".join(parts)


def _find_product_row(html: str, product: str) -> re.Match[str]:
    m = re.search(rf"{_product_pattern(product)}.*?</tr>", html, re.I | re.S)
    if not m:
        raise RuntimeError(f"DRAMeXchange row not found: {product!r}")
    return m


def _row_session_avg(html: str, product: str) -> float:
    """Find product row; session average is the 5th numeric cell."""
    m = _find_product_row(html, product)
    nums = re.findall(r">\s*(\d+(?:\.\d+)?)\s*<", m.group(0))
    if len(nums) < 5:
        raise RuntimeError(
            f"DRAMeXchange row {product!r} expected ≥5 nums, got {nums}"
        )
    return float(nums[4])


def _as_of_near_product(html: str, product: str) -> str:
    """Use the nearest preceding ``Last Update`` stamp for this product row."""
    m = re.search(_product_pattern(product), html, re.I)
    if not m:
        raise RuntimeError(f"product not in HTML: {product!r}")
    pos = m.start()
    window = html[max(0, pos - 2500) : pos]
    stamps = list(
        re.finditer(
            r"Last\s*Update[:\s]*([A-Za-z]+\.?\s*\d{1,2}\s+\d{4}\s+\d{1,2}:\d{2})",
            window,
            re.I,
        )
    )
    if not stamps:
        # Fallback: first stamp on page.
        m = re.search(
            r"Last\s*Update[:\s]*([A-Za-z]+\.?\s*\d{1,2}\s+\d{4}\s+\d{1,2}:\d{2})",
            html,
            re.I,
        )
        if not m:
            raise RuntimeError("no Last Update stamp on DRAMeXchange page")
        return _parse_board_date(m.group(1))
    return _parse_board_date(stamps[-1].group(1))


def scrape_spot_snapshot(
    *,
    html: str | None = None,
    products: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return ``{series_key: {date, value, product, unit}, ..., meta}``."""
    body = html if html is not None else fetch_html()
    wanted = products or PRODUCTS
    fetched_at = datetime.now(timezone.utc).isoformat()
    out: dict[str, Any] = {
        "schema_version": 0,
        "source": URL,
        "fetched_at": fetched_at,
        "spots": {},
    }
    for key, label in wanted.items():
        avg = _row_session_avg(body, label)
        as_of = _as_of_near_product(body, label)
        out["spots"][key] = {
            "date": as_of,
            "value": avg,
            "product": label,
            "field": "session_average",
            "unit": "usd",
        }
    return out


def load_history(path: Path) -> dict[str, dict[str, float]]:
    """``date → value`` from jsonl rows ``{date, value, ...}``."""
    if not path.exists():
        return {}
    by: dict[str, dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        d = str(row.get("date") or "")
        if not d or row.get("value") is None:
            continue
        by[d] = {"date": d, "value": float(row["value"])}
    return by


def write_history(path: Path, by_date: dict[str, dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [by_date[k] for k in sorted(by_date)]
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def append_spot(
    path: Path,
    *,
    date: str,
    value: float,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, float]]:
    """Upsert one as_of print into history; return sorted points."""
    by = load_history(path)
    row: dict[str, Any] = {"date": date, "value": float(value)}
    if meta:
        for k, v in meta.items():
            if k not in ("date", "value") and v is not None:
                row[k] = v
    by[date] = row  # type: ignore[assignment]
    write_history(path, by)  # type: ignore[arg-type]
    return [{"date": d, "value": float(by[d]["value"])} for d in sorted(by)]


def points_from_history(path: Path) -> list[dict[str, float]]:
    by = load_history(path)
    return [{"date": d, "value": float(by[d]["value"])} for d in sorted(by)]
