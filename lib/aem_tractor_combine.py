"""AEM US Ag Tractor & Combine monthly retail units (trade-press PDF).

Source page:
  https://www.aem.org/market-share-statistics/us-ag-tractor-and-combine-reports

Public trade-press PDF (acknowledge AEM). History reprints are paid; we append
each month's print going forward and merge into local/R2 history.
"""

from __future__ import annotations

import io
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
    "mosaicbot_themedata/0.1 (+lib.aem_tractor_combine)"
)

REPORT_PAGE = (
    "https://www.aem.org/market-share-statistics/us-ag-tractor-and-combine-reports"
)

# Fallback when discovery fails (newest known trade-press PDF).
CANDIDATE_PDFS = (
    "https://www.aem.org/getattachment/dd9b4c30-f615-4bad-b1a0-809fe96aceed/"
    "September-2026-Farm_Flash_Trade_Press_With_Chart_PreRelease-United-States.pdf",
)

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# Catalog metric_key → PDF row label (first match wins).
ROW_LABELS: dict[str, tuple[str, ...]] = {
    "under_40_hp": ("< 40 HP", "<40 HP"),
    "hp_40_100": ("40 < 100 HP", "40<100 HP", "40 - 100 HP"),
    "hp_100_plus": ("100+ HP", "100 + HP"),
    "total_2wd": ("Total 2WD Farm Tractors", "Total 2WD"),
    "total_4wd": ("4WD Farm Tractors",),
    "total_farm_tractors": ("Total Farm Tractors",),
    "combines": ("Self-Prop Combines", "Self-Propelled Combines"),
}

HISTORY_NAME = "aem_tractor_combine_history.json"

_NUM = re.compile(r"^-?[\d,]+(?:\.\d+)?$")
_PDF_HREF = re.compile(
    r"""href=["']([^"']*(?:Farm_Flash|Tractor|Combine)[^"']*\.pdf[^"']*)["']""",
    re.I,
)


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


def discover_pdf_urls() -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    try:
        html = _get_text(REPORT_PAGE)
    except Exception:
        html = ""
    base = "https://www.aem.org"
    for m in _PDF_HREF.finditer(html):
        href = m.group(1).strip()
        if href.startswith("/"):
            href = base + href
        key = href.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        found.append(href)
    for url in CANDIDATE_PDFS:
        key = url.split("?")[0]
        if key not in seen:
            seen.add(key)
            found.append(url)
    return found


def _parse_num(tok: str) -> float:
    return float(tok.replace(",", ""))


def _month_end(year: int, month: int) -> str:
    if month == 12:
        return f"{year:04d}-12-01"
    return f"{year:04d}-{month:02d}-01"


def parse_pdf_bytes(raw: bytes) -> tuple[str, dict[str, float], dict[str, Any]]:
    """Parse one AEM trade-press PDF.

    Returns ``(YYYY-MM-01, {metric_key: monthly_units}, meta)``.
    Uses the current-month unit column (not YTD).
    """
    text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages)
    # Header: "September" / "2026" often on separate lines.
    month = None
    year = None
    for line in text.splitlines():
        low = line.strip().lower()
        if month is None and low in _MONTHS:
            month = _MONTHS[low]
            continue
        if month is not None and year is None:
            m = re.fullmatch(r"(20\d{2})", line.strip())
            if m:
                year = int(m.group(1))
                break
    if month is None or year is None:
        # Filename / released line fallback
        m = re.search(
            r"(January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+(20\d{2})",
            text,
            re.I,
        )
        if not m:
            raise RuntimeError("AEM PDF: could not find report month/year")
        month = _MONTHS[m.group(1).lower()]
        year = int(m.group(2))

    # Flatten to tokens (labels + numbers) so line breaks don't break rows.
    tokens: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        tokens.append(s)

    def _row_values(label: str) -> list[float] | None:
        for i, tok in enumerate(tokens):
            if tok.lower() != label.lower() and tok != label:
                continue
            nums: list[float] = []
            for t in tokens[i + 1 : i + 12]:
                if _NUM.match(t):
                    nums.append(_parse_num(t))
                    if len(nums) >= 7:
                        break
                elif nums:
                    # stop at next non-number after starting
                    break
            if len(nums) >= 1:
                return nums
        return None

    metrics: dict[str, float] = {}
    labels_used: dict[str, str] = {}
    for key, aliases in ROW_LABELS.items():
        for alias in aliases:
            vals = _row_values(alias)
            if vals:
                # Column 0 = current month units
                metrics[key] = float(vals[0])
                labels_used[key] = alias
                break

    if "total_farm_tractors" not in metrics:
        raise RuntimeError(
            "AEM PDF: missing Total Farm Tractors row; keys="
            + ",".join(sorted(metrics))
        )

    as_of = _month_end(year, month)
    meta = {
        "report_month": as_of,
        "year": year,
        "month": month,
        "labels_used": labels_used,
        "n_metrics": len(metrics),
    }
    return as_of, metrics, meta


def load_history(path: Path) -> dict[str, dict[str, float]]:
    """``{YYYY-MM-01: {metric_key: value}}``."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    for d, row in (data.get("months") or {}).items():
        if isinstance(row, dict):
            out[str(d)] = {str(k): float(v) for k, v in row.items()}
    return out


def save_history(
    path: Path,
    months: dict[str, dict[str, float]],
    *,
    meta: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "months": {k: months[k] for k in sorted(months)},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def refresh_bundle(
    *,
    history_path: Path,
    start: str = "2020-01-01",
    urls: list[str] | None = None,
) -> tuple[dict[str, list[dict[str, float]]], dict[str, Any]]:
    """Fetch latest PDF once, merge history, return points by metric_key."""
    prior = load_history(history_path)
    url_list = list(urls) if urls is not None else discover_pdf_urls()
    notes: list[str] = []
    scraped_months: dict[str, dict[str, float]] = {}
    for url in url_list:
        name = urllib.parse.unquote(url.split("/")[-1].split("?")[0])
        try:
            raw = _get_bytes(url)
            as_of, metrics, _mm = parse_pdf_bytes(raw)
            scraped_months[as_of] = metrics
            notes.append(
                f"ok {name} as_of={as_of} metrics={sorted(metrics)} "
                f"total={metrics.get('total_farm_tractors')}"
            )
            break
        except Exception as e:
            notes.append(f"skip {name}: {e}")

    merged = {**prior, **scraped_months}
    meta = {
        "n_months": len(merged),
        "scrape_notes": notes,
        "source": "AEM US Ag Tractor and Combine Report (trade press PDF)",
        "as_of": max(merged) if merged else None,
    }
    save_history(history_path, merged, meta=meta)

    keys = sorted({k for row in merged.values() for k in row})
    bundle: dict[str, list[dict[str, float]]] = {}
    for metric_key in keys:
        bundle[metric_key] = [
            {"date": d, "value": float(row[metric_key])}
            for d, row in sorted(merged.items())
            if d >= start and metric_key in row
        ]
    if not any(bundle.values()):
        raise RuntimeError("AEM scrape empty: " + "; ".join(notes[:4]))
    return bundle, meta


def refresh_monthly(
    *,
    history_path: Path,
    metric_key: str,
    start: str = "2020-01-01",
    urls: list[str] | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Fetch latest PDF(s), merge into history, return one metric's points."""
    bundle, meta = refresh_bundle(
        history_path=history_path, start=start, urls=urls
    )
    pts = bundle.get(metric_key) or []
    if not pts:
        raise RuntimeError(
            f"AEM metric {metric_key} empty: "
            + "; ".join((meta.get("scrape_notes") or [])[:4])
        )
    meta = {**meta, "metric_key": metric_key}
    return pts, meta
