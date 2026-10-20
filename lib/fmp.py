"""Financial Modeling Prep — CapEx, revenue, and earnings-transcript helpers.

Uses stable endpoints (same family as MosaicBotMain):
- cash-flow-statement → CapEx (outflow; we publish abs() spend)
- income-statement → revenue
- earning-call-transcript-dates / earning-call-transcript → keyword counts

Oracle (and some others) file on fiscal quarter-ends that are not calendar
quarter-ends. Aggregates and stacks map each print onto a calendar quarter
end (Mar/Jun/Sep/Dec) so series share dates.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from lib.env import load_env, require_env

FMP_BASE = "https://financialmodelingprep.com/stable"
UA = "mosaicbot_themedata/0.1 (+fmp)"

# Cloud CapEx proxy for optical & fiber demand (Big-4 + Oracle OCI).
DEFAULT_HYPERSCALERS = ("MSFT", "GOOGL", "META", "AMZN", "ORCL")

# Revenue stacks: drop sparse pre-coverage quarters (many names lack full panels).
REVENUE_HISTORY_START = "2017-01-01"


def revenue_history_start(start: str, meta: dict[str, Any] | None = None) -> str:
    """Effective floor for FMP revenue series (catalog may override via history_start)."""
    floor = str((meta or {}).get("history_start") or REVENUE_HISTORY_START)[:10]
    s = str(start or "")[:10] or floor
    return max(s, floor)


def _api_key() -> str:
    load_env()
    return require_env("FMP_API_KEY")["FMP_API_KEY"].strip()


def _get(endpoint: str, params: dict[str, Any]) -> Any:
    q = dict(params)
    q["apikey"] = _api_key()
    url = f"{FMP_BASE}/{endpoint}?{urlencode(q)}"
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=45) as resp:
        raw = resp.read()
        status = getattr(resp, "status", None) or resp.getcode()
    if status != 200:
        raise RuntimeError(f"FMP HTTP {status} for {endpoint}")
    return json.loads(raw.decode("utf-8"))


def calendar_quarter_end(iso_date: str) -> str:
    """Map any period-end onto the nearest calendar quarter end (Mar/Jun/Sep/Dec).

    Month-truncated buckets break 52/53-week filers (e.g. TER Q1 ending 2023-04-02
    used to land in Jun instead of Mar, leaving a hole with only AEHR on the
    stack). Nearest-neighbor keeps fiscal prints aligned for stacked charts.
    """
    from datetime import date

    d = date.fromisoformat(str(iso_date)[:10])
    candidates: list[date] = []
    for y in (d.year - 1, d.year, d.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            candidates.append(date(y, month, day))
    best = min(candidates, key=lambda c: abs((c - d).days))
    return best.isoformat()


def fetch_quarterly_capex(
    symbol: str,
    *,
    limit: int = 40,
    calendar_bucket: bool = True,
    scale: float = 1.0,
) -> list[dict[str, Any]]:
    """Quarterly CapEx points for one ticker (abs spend).

    When calendar_bucket=True, dates are remapped to calendar Q ends and
    multiple prints in the same bucket are summed (rare).
    """
    rows = _get(
        "cash-flow-statement",
        {"symbol": symbol.upper(), "period": "quarter", "limit": int(limit)},
    )
    if not isinstance(rows, list):
        raise RuntimeError(f"FMP cash-flow unexpected for {symbol}: {type(rows)}")

    by_date: dict[str, float] = defaultdict(float)
    for row in rows:
        d = row.get("date")
        cap = row.get("capitalExpenditure")
        if not d or cap is None or cap == "":
            continue
        key = calendar_quarter_end(str(d)[:10]) if calendar_bucket else str(d)[:10]
        by_date[key] += abs(float(cap))

    out = [
        {
            "date": d,
            "value": round(v / scale, 4) if scale else round(v, 4),
            "symbol": symbol.upper(),
        }
        for d, v in sorted(by_date.items())
    ]
    return out


def fetch_capex_by_symbol(
    symbols: list[str] | tuple[str, ...] | None = None,
    *,
    limit: int = 40,
    scale: float = 1e9,
    calendar_bucket: bool = True,
    sleep_s: float = 0.15,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch CapEx for each symbol (usd_billions when scale=1e9)."""
    tickers = [str(s).upper() for s in (symbols or DEFAULT_HYPERSCALERS)]
    out: dict[str, list[dict[str, Any]]] = {}
    for i, sym in enumerate(tickers):
        out[sym] = fetch_quarterly_capex(
            sym, limit=limit, calendar_bucket=calendar_bucket, scale=scale
        )
        if sleep_s and i + 1 < len(tickers):
            time.sleep(sleep_s)
    return out


def aggregate_capex_points(
    by_symbol: dict[str, list[dict[str, Any]]],
    *,
    require_all: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sum CapEx across symbols on shared dates."""
    tickers = list(by_symbol.keys())
    by_date: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    per_ticker = {sym: len(pts) for sym, pts in by_symbol.items()}

    for sym, pts in by_symbol.items():
        for p in pts:
            d = p["date"]
            by_date[d] += float(p["value"])
            counts[d] += 1

    n = len(tickers)
    points: list[dict[str, Any]] = []
    for d in sorted(by_date):
        if require_all and counts[d] < n:
            continue
        points.append({"date": d, "value": round(by_date[d], 4)})

    meta = {
        "symbols": tickers,
        "require_all": require_all,
        "per_ticker_points": per_ticker,
        "quarters": len(points),
        "source": "fmp_cash_flow_statement",
        "unit": "usd_billions",
        "calendar_bucket": True,
    }
    return points, meta


def fetch_hyperscaler_capex_aggregate(
    symbols: list[str] | tuple[str, ...] | None = None,
    *,
    limit: int = 40,
    require_all: bool = True,
    scale: float = 1e9,
    sleep_s: float = 0.15,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sum quarterly CapEx across hyperscalers (calendar-quarter aligned)."""
    by_sym = fetch_capex_by_symbol(
        symbols, limit=limit, scale=scale, sleep_s=sleep_s
    )
    return aggregate_capex_points(by_sym, require_all=require_all)


# ---------------------------------------------------------------------------
# Revenue (income statement)
# ---------------------------------------------------------------------------


def fetch_quarterly_revenue(
    symbol: str,
    *,
    limit: int = 40,
    calendar_bucket: bool = True,
    scale: float = 1.0,
) -> list[dict[str, Any]]:
    """Quarterly revenue points for one ticker."""
    rows = _get(
        "income-statement",
        {"symbol": symbol.upper(), "period": "quarter", "limit": int(limit)},
    )
    if not isinstance(rows, list):
        raise RuntimeError(f"FMP income-statement unexpected for {symbol}: {type(rows)}")

    by_date: dict[str, float] = defaultdict(float)
    for row in rows:
        d = row.get("date")
        rev = row.get("revenue")
        if not d or rev is None or rev == "":
            continue
        key = calendar_quarter_end(str(d)[:10]) if calendar_bucket else str(d)[:10]
        by_date[key] += float(rev)

    return [
        {
            "date": d,
            "value": round(v / scale, 4) if scale else round(v, 4),
            "symbol": symbol.upper(),
        }
        for d, v in sorted(by_date.items())
    ]


def fetch_revenue_by_symbol(
    symbols: list[str] | tuple[str, ...] | None,
    *,
    limit: int = 40,
    scale: float = 1e9,
    calendar_bucket: bool = True,
    sleep_s: float = 0.15,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch revenue for each symbol (usd_billions when scale=1e9)."""
    tickers = [str(s).upper() for s in (symbols or ())]
    out: dict[str, list[dict[str, Any]]] = {}
    for i, sym in enumerate(tickers):
        out[sym] = fetch_quarterly_revenue(
            sym, limit=limit, calendar_bucket=calendar_bucket, scale=scale
        )
        if sleep_s and i + 1 < len(tickers):
            time.sleep(sleep_s)
    return out


def aggregate_revenue_points(
    by_symbol: dict[str, list[dict[str, Any]]],
    *,
    require_all: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sum revenue across symbols on shared dates (default: allow partial)."""
    pts, meta = aggregate_capex_points(by_symbol, require_all=require_all)
    meta = {
        **meta,
        "source": "fmp_income_statement",
        "unit": "usd_billions",
    }
    return pts, meta


# ---------------------------------------------------------------------------
# Product-segment revenue (revenue-product-segmentation)
# ---------------------------------------------------------------------------


def _pick_segment_value(
    data: dict[str, Any], aliases: list[str] | tuple[str, ...]
) -> tuple[str | None, float | None]:
    """Return (matched_label, value) for the first alias hit (case-insensitive).

    Prefer exact match, then substring either way.
    """
    if not isinstance(data, dict) or not aliases:
        return None, None
    keys = list(data.keys())
    lower_map = {str(k).lower(): k for k in keys}
    for alias in aliases:
        a = str(alias or "").strip()
        if not a:
            continue
        al = a.lower()
        if al in lower_map:
            k = lower_map[al]
            v = data.get(k)
            if v is not None and v != "":
                return k, float(v)
        for k in keys:
            kl = str(k).lower()
            if al in kl or kl in al:
                v = data.get(k)
                if v is not None and v != "":
                    return k, float(v)
    return None, None


def _quarterly_revenue_by_date(
    symbol: str, *, limit: int = 80, calendar_bucket: bool = True
) -> dict[str, float]:
    """Map calendar-quarter date → company total revenue (USD, unscaled)."""
    rows = _get(
        "income-statement",
        {"symbol": symbol.upper(), "period": "quarter", "limit": int(limit)},
    )
    if not isinstance(rows, list):
        return {}
    by_date: dict[str, float] = defaultdict(float)
    for row in rows:
        d = row.get("date")
        rev = row.get("revenue")
        if not d or rev is None or rev == "":
            continue
        key = calendar_quarter_end(str(d)[:10]) if calendar_bucket else str(d)[:10]
        by_date[key] += float(rev)
    return dict(by_date)


def fetch_quarterly_segment_revenue(
    symbol: str,
    segment_aliases: list[str] | tuple[str, ...],
    *,
    limit: int = 40,
    calendar_bucket: bool = True,
    scale: float = 1e9,
    max_segment_sum_vs_revenue: float = 1.15,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Quarterly product-segment revenue for one ticker.

    ``segment_aliases`` is an ordered prefer list (first match wins per print).

    Drops prints where the sum of all product segments exceeds company income-
    statement revenue by ``max_segment_sum_vs_revenue`` (FMP sometimes doubles
    Q4 brand splits — e.g. YUM 2024-12-31).
    """
    aliases = [str(a).strip() for a in segment_aliases if str(a).strip()]
    if not aliases:
        raise RuntimeError("segment_aliases required")
    rows = _get(
        "revenue-product-segmentation",
        {
            "symbol": symbol.upper(),
            "structure": "flat",
            "period": "quarter",
        },
    )
    if not isinstance(rows, list):
        raise RuntimeError(
            f"FMP revenue-product-segmentation unexpected for {symbol}: {type(rows)}"
        )

    company_rev = _quarterly_revenue_by_date(
        symbol, limit=max(80, int(limit) * 2), calendar_bucket=calendar_bucket
    )

    by_date: dict[str, float] = defaultdict(float)
    labels_used: dict[str, int] = defaultdict(int)
    matched = 0
    skipped_inflated: list[str] = []
    for row in rows:
        d = row.get("date")
        data = row.get("data")
        if not d or not isinstance(data, dict):
            continue
        label, val = _pick_segment_value(data, aliases)
        if label is None or val is None:
            continue
        key = calendar_quarter_end(str(d)[:10]) if calendar_bucket else str(d)[:10]
        seg_sum = sum(float(v) for v in data.values() if v is not None and v != "")
        total = company_rev.get(key)
        if (
            total
            and total > 0
            and seg_sum > total * float(max_segment_sum_vs_revenue)
        ):
            skipped_inflated.append(key)
            continue
        by_date[key] += float(val)
        labels_used[label] += 1
        matched += 1
    dates_sorted = sorted(by_date.keys())
    if len(dates_sorted) > int(limit):
        keep = set(dates_sorted[-int(limit) :])
        by_date = {d: by_date[d] for d in dates_sorted if d in keep}

    pts = [
        {
            "date": d,
            "value": round(v / scale, 4) if scale else round(v, 4),
            "symbol": symbol.upper(),
        }
        for d, v in sorted(by_date.items())
    ]
    meta = {
        "symbol": symbol.upper(),
        "segment_aliases": aliases,
        "labels_used": dict(labels_used),
        "matched_prints": matched,
        "skipped_inflated_dates": sorted(set(skipped_inflated)),
        "source": "fmp_revenue_product_segmentation",
        "unit": "usd_billions",
    }
    return pts, meta


# ---------------------------------------------------------------------------
# Earnings transcripts → keyword hit counts
# ---------------------------------------------------------------------------


def list_transcript_dates(symbol: str) -> list[dict[str, Any]]:
    """Newest-first list of {quarter, fiscalYear, date} for available transcripts."""
    rows = _get("earning-call-transcript-dates", {"symbol": symbol.upper()})
    if not isinstance(rows, list):
        raise RuntimeError(
            f"FMP transcript-dates unexpected for {symbol}: {type(rows)}"
        )
    return rows


def fetch_transcript(
    symbol: str, *, year: int, quarter: int
) -> dict[str, Any] | None:
    """One earnings-call transcript (content + date) or None if missing."""
    rows = _get(
        "earning-call-transcript",
        {
            "symbol": symbol.upper(),
            "year": int(year),
            "quarter": int(quarter),
        },
    )
    if not isinstance(rows, list) or not rows:
        return None
    return rows[0] if isinstance(rows[0], dict) else None


def count_keyword_hits(text: str, keywords: Iterable[str]) -> dict[str, int]:
    """Case-insensitive phrase counts; multi-word keywords allowed."""
    blob = text or ""
    out: dict[str, int] = {}
    for raw in keywords:
        kw = str(raw or "").strip()
        if not kw:
            continue
        # Word-ish boundaries for single tokens; substring for multi-word phrases.
        if " " in kw or "-" in kw:
            pat = re.compile(re.escape(kw), re.I)
        else:
            pat = re.compile(rf"\b{re.escape(kw)}\b", re.I)
        out[kw] = len(pat.findall(blob))
    return out


def fetch_transcript_keyword_points(
    symbol: str,
    keywords: list[str] | tuple[str, ...],
    *,
    limit: int = 16,
    calendar_bucket: bool = True,
    sleep_s: float = 0.12,
) -> list[dict[str, Any]]:
    """Quarterly total keyword-hit counts for one ticker (sum across keywords)."""
    kws = [str(k).strip() for k in keywords if str(k).strip()]
    if not kws:
        raise RuntimeError("keywords required")
    dates = list_transcript_dates(symbol)[: max(int(limit), 1)]
    by_date: dict[str, float] = defaultdict(float)
    for i, row in enumerate(dates):
        year = int(row.get("fiscalYear") or row.get("year") or 0)
        quarter = int(row.get("quarter") or 0)
        if not year or not quarter:
            continue
        tr = fetch_transcript(symbol, year=year, quarter=quarter)
        if sleep_s:
            time.sleep(sleep_s)
        if not tr:
            continue
        content = str(tr.get("content") or "")
        hits = count_keyword_hits(content, kws)
        total = float(sum(hits.values()))
        d_raw = str(tr.get("date") or row.get("date") or "")[:10]
        if not d_raw:
            continue
        key = calendar_quarter_end(d_raw) if calendar_bucket else d_raw
        by_date[key] += total
    return [
        {"date": d, "value": round(v, 4), "symbol": symbol.upper()}
        for d, v in sorted(by_date.items())
    ]


def fetch_transcript_keywords_by_symbol(
    symbols: list[str] | tuple[str, ...],
    keywords: list[str] | tuple[str, ...],
    *,
    limit: int = 16,
    sleep_s: float = 0.12,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for i, sym in enumerate(symbols):
        out[str(sym).upper()] = fetch_transcript_keyword_points(
            str(sym), keywords, limit=limit, sleep_s=sleep_s
        )
        if sleep_s and i + 1 < len(symbols):
            time.sleep(sleep_s)
    return out


def aggregate_transcript_keyword_points(
    by_symbol: dict[str, list[dict[str, Any]]],
    *,
    require_all: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Sum keyword-hit counts across symbols per calendar quarter."""
    pts, meta = aggregate_capex_points(by_symbol, require_all=require_all)
    meta = {
        **meta,
        "source": "fmp_earning_call_transcript",
        "unit": "keyword_hits",
    }
    return pts, meta
