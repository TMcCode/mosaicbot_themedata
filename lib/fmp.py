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


def _reported_currency(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        c = row.get("reportedCurrency") or row.get("currency")
        if c:
            return str(c).strip().upper()
    return "USD"


def fetch_quarterly_capex(
    symbol: str,
    *,
    limit: int = 40,
    calendar_bucket: bool = True,
    scale: float = 1.0,
    to_usd: bool = False,
    fx_rates: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Quarterly CapEx points for one ticker (abs spend).

    When calendar_bucket=True, dates are remapped to calendar Q ends and
    multiple prints in the same bucket are summed (rare).

    ``to_usd=True`` converts reported currency → USD at the *latest* FRED
    cross (constant over history) via ``lib.fx_usd`` before ``scale``.
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

    ccy = _reported_currency(rows) if to_usd else "USD"
    if to_usd and ccy != "USD":
        from lib import fx_usd as fxl

        rates = fx_rates if fx_rates is not None else fxl.latest_rates([ccy])
        by_date = {
            d: fxl.to_usd(v, ccy, rates=rates) for d, v in by_date.items()
        }

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
    to_usd: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch CapEx for each symbol (usd_billions when scale=1e9).

    Pass ``to_usd=True`` for non-USD filers (constant latest FX over history).
    """
    tickers = [str(s).upper() for s in (symbols or DEFAULT_HYPERSCALERS)]
    fx_rates = None
    if to_usd:
        from lib import fx_usd as fxl

        # Prefetch common Asian/EU crosses; per-ticker currency still wins.
        fx_rates = fxl.latest_rates(["JPY", "TWD", "KRW", "CNY", "EUR", "GBP"])
    out: dict[str, list[dict[str, Any]]] = {}
    for i, sym in enumerate(tickers):
        out[sym] = fetch_quarterly_capex(
            sym,
            limit=limit,
            calendar_bucket=calendar_bucket,
            scale=scale,
            to_usd=to_usd,
            fx_rates=fx_rates,
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
    dates_sorted = sorted(by_date)
    peak = max((int(counts[d]) for d in dates_sorted), default=0)
    # Only the latest print can be "incomplete" (current earnings season).
    # Older thinner panels are structural history, not partial reports.
    latest_d = dates_sorted[-1] if dates_sorted else None
    latest_incomplete = bool(
        latest_d
        and peak > 0
        and 0 < int(counts[latest_d]) < peak
    )

    points: list[dict[str, Any]] = []
    for d in dates_sorted:
        n_hit = int(counts[d])
        if require_all and n_hit < n:
            continue
        row: dict[str, Any] = {
            "date": d,
            "value": round(by_date[d], 4),
            "n_contributors": n_hit,
            "n_expected": n,
        }
        if latest_incomplete and d == latest_d:
            # Partial member panel (earnings season). Charts may show this;
            # scores / YoY derived metrics must ignore it.
            row["incomplete"] = True
        points.append(row)

    meta = {
        "symbols": tickers,
        "require_all": require_all,
        "per_ticker_points": per_ticker,
        "quarters": len(points),
        "incomplete_quarters": sum(1 for p in points if p.get("incomplete")),
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
    to_usd: bool = False,
    fx_rates: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Quarterly revenue points for one ticker.

    ``to_usd=True`` converts ``reportedCurrency`` → USD at the *latest* FRED
    cross (constant over history) via ``lib.fx_usd`` before ``scale``.
    """
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

    ccy = _reported_currency(rows) if to_usd else "USD"
    if to_usd and ccy != "USD":
        from lib import fx_usd as fxl

        rates = fx_rates if fx_rates is not None else fxl.latest_rates([ccy])
        by_date = {
            d: fxl.to_usd(v, ccy, rates=rates) for d, v in by_date.items()
        }

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
    to_usd: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch revenue for each symbol (usd_billions when scale=1e9).

    Pass ``to_usd=True`` for non-USD filers (constant latest FX over history).
    """
    tickers = [str(s).upper() for s in (symbols or ())]
    fx_rates = None
    if to_usd:
        from lib import fx_usd as fxl

        fx_rates = fxl.latest_rates(["JPY", "TWD", "KRW", "CNY", "EUR", "GBP"])
    out: dict[str, list[dict[str, Any]]] = {}
    for i, sym in enumerate(tickers):
        out[sym] = fetch_quarterly_revenue(
            sym,
            limit=limit,
            calendar_bucket=calendar_bucket,
            scale=scale,
            to_usd=to_usd,
            fx_rates=fx_rates,
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
        # Prefer alias ⊆ key (e.g. "Consumable" → "Consumable And Other Products").
        # Avoid key ⊆ alias for short keys ("Product" ⊆ "... Other Products").
        for k in keys:
            kl = str(k).lower()
            if al in kl:
                v = data.get(k)
                if v is not None and v != "":
                    return k, float(v)
        for k in keys:
            kl = str(k).lower()
            if len(kl) >= 10 and kl in al:
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


def _segment_sum_excluding_rollups(data: dict[str, Any]) -> float:
    """Sum segment values, dropping parent keys that equal the sum of siblings.

    FMP often nests Product = Consumable + Instrument (PACB) alongside the
    leaves; counting both inflates the print vs income-statement revenue.
    """
    items: list[tuple[str, float]] = []
    for k, v in data.items():
        if v is None or v == "":
            continue
        try:
            items.append((str(k), float(v)))
        except (TypeError, ValueError):
            continue
    if not items:
        return 0.0
    rollups: set[str] = set()
    for i, (k, v) in enumerate(items):
        if v <= 0:
            continue
        rest = [ov for j, (_ok, ov) in enumerate(items) if j != i]
        if len(rest) >= 2 and abs(sum(rest) - v) <= max(1.0, 0.02 * abs(v)):
            rollups.add(k)
            continue
        for a in range(len(rest)):
            for b in range(a + 1, len(rest)):
                if abs(rest[a] + rest[b] - v) <= max(1.0, 0.02 * abs(v)):
                    rollups.add(k)
                    break
            if k in rollups:
                break
    return sum(v for k, v in items if k not in rollups)


def fetch_quarterly_segment_revenue(
    symbol: str,
    segment_aliases: list[str] | tuple[str, ...],
    *,
    limit: int = 40,
    calendar_bucket: bool = True,
    scale: float = 1e9,
    max_segment_sum_vs_revenue: float = 1.15,
    skip_segment_sum_guard: bool = False,
    to_usd: bool = False,
    fx_rates: dict[str, Any] | None = None,
    period: str = "quarter",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Product-segment revenue for one ticker (quarterly by default; annual OK).

    ``segment_aliases`` is an ordered prefer list (first match wins per print).

    Drops prints where the sum of non-rollup product segments exceeds company
    income-statement revenue by ``max_segment_sum_vs_revenue`` (FMP sometimes
    doubles Q4 brand splits — e.g. YUM 2024-12-31). Parent+child trees
    (PACB Product = Consumable+Instrument) are de-duplicated before the sum
    check. Set ``skip_segment_sum_guard`` for messy trees (QTRX) where leaves
    still oversum after rollup stripping — single-segment ceiling still applies.

    Also drops non-positive matched segment values (FMP restatement artifacts —
    e.g. DE PPA 2022-09-30 ≈ −$14B) and single-segment values that alone exceed
    company revenue by the same inflation guard.

    ``to_usd=True`` converts at latest FRED FX *after* inflation checks (same
    currency as company revenue), then applies ``scale``.

    ``period="annual"`` uses annual segmentation + annual income-statement
    guards (no calendar-quarter bucketing).
    """
    aliases = [str(a).strip() for a in segment_aliases if str(a).strip()]
    if not aliases:
        raise RuntimeError("segment_aliases required")
    period_norm = str(period or "quarter").strip().lower()
    if period_norm not in ("quarter", "annual"):
        raise RuntimeError(f"unsupported segment period {period!r}")
    use_q_bucket = bool(calendar_bucket) and period_norm == "quarter"
    rows = _get(
        "revenue-product-segmentation",
        {
            "symbol": symbol.upper(),
            "structure": "flat",
            "period": period_norm,
        },
    )
    if not isinstance(rows, list):
        raise RuntimeError(
            f"FMP revenue-product-segmentation unexpected for {symbol}: {type(rows)}"
        )

    if period_norm == "annual":
        company_rev = {}
        inc_rows = _get(
            "income-statement",
            {
                "symbol": symbol.upper(),
                "period": "annual",
                "limit": max(40, int(limit) * 2),
            },
        )
        if isinstance(inc_rows, list):
            for row in inc_rows:
                d = row.get("date")
                rev = row.get("revenue")
                if not d or rev is None or rev == "":
                    continue
                company_rev[str(d)[:10]] = float(rev)
    else:
        company_rev = _quarterly_revenue_by_date(
            symbol, limit=max(80, int(limit) * 2), calendar_bucket=use_q_bucket
        )

    by_date: dict[str, float] = defaultdict(float)
    labels_used: dict[str, int] = defaultdict(int)
    matched = 0
    skipped_inflated: list[str] = []
    skipped_nonpositive: list[str] = []
    for row in rows:
        d = row.get("date")
        data = row.get("data")
        if not d or not isinstance(data, dict):
            continue
        label, val = _pick_segment_value(data, aliases)
        if label is None or val is None:
            continue
        key = calendar_quarter_end(str(d)[:10]) if use_q_bucket else str(d)[:10]
        try:
            val_f = float(val)
        except (TypeError, ValueError):
            continue
        if val_f <= 0:
            skipped_nonpositive.append(key)
            continue
        seg_sum = _segment_sum_excluding_rollups(data)
        total = company_rev.get(key)
        if total is None and period_norm == "annual":
            # Fiscal year-ends can drift a day vs segmentation date.
            y = key[:4]
            for ck, cv in company_rev.items():
                if ck.startswith(y):
                    total = cv
                    break
        # Single-segment ceiling always applies. Sum-of-leaves guard catches
        # doubled brand splits (YUM) but false-fires on parent+child trees
        # even after rollup stripping (QTRX Product vs Consumable+Instruments
        # with a parallel Service bucket). Callers may set
        # skip_segment_sum_guard via kwargs.
        inflated_self = bool(
            total
            and total > 0
            and val_f > total * float(max_segment_sum_vs_revenue)
        )
        inflated_sum = bool(
            total
            and total > 0
            and seg_sum > total * float(max_segment_sum_vs_revenue)
            and not skip_segment_sum_guard
        )
        if inflated_self or inflated_sum:
            skipped_inflated.append(key)
            continue
        by_date[key] += val_f
        labels_used[label] += 1
        matched += 1
    dates_sorted = sorted(by_date.keys())
    if len(dates_sorted) > int(limit):
        keep = set(dates_sorted[-int(limit) :])
        by_date = {d: by_date[d] for d in dates_sorted if d in keep}

    fx_meta: dict[str, Any] = {}
    if to_usd and by_date:
        from lib import fx_usd as fxl

        inc = _get(
            "income-statement",
            {
                "symbol": symbol.upper(),
                "period": period_norm,
                "limit": 4,
            },
        )
        ccy = _reported_currency(inc if isinstance(inc, list) else [])
        if ccy != "USD":
            rates = fx_rates if fx_rates is not None else fxl.latest_rates([ccy])
            by_date = {
                d: fxl.to_usd(v, ccy, rates=rates) for d, v in by_date.items()
            }
            fx = rates.get(ccy)
            fx_meta = {
                "currency_source": ccy,
                "fx_as_of": getattr(fx, "as_of", None),
                "fx_foreign_per_usd": getattr(fx, "foreign_per_usd", None),
                "fx_method": "latest_spot_constant_history",
            }

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
        "period": period_norm,
        "labels_used": dict(labels_used),
        "matched_prints": matched,
        "skipped_inflated_dates": sorted(set(skipped_inflated)),
        "skipped_nonpositive_dates": sorted(set(skipped_nonpositive)),
        "source": "fmp_revenue_product_segmentation",
        "unit": "usd_billions",
        **fx_meta,
    }
    return pts, meta


# ---------------------------------------------------------------------------
# Headcount (SEC 10-K via FMP historical-employee-count)
# ---------------------------------------------------------------------------


def fetch_employee_count(
    symbol: str,
    *,
    limit: int = 40,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Annual employee counts from FMP historical-employee-count (10-K)."""
    rows = _get(
        "historical-employee-count",
        {"symbol": str(symbol).upper(), "limit": int(limit)},
    )
    if not isinstance(rows, list):
        raise RuntimeError(
            f"FMP historical-employee-count unexpected for {symbol}: {type(rows)}"
        )
    by_date: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = str(row.get("periodOfReport") or row.get("date") or "")[:10]
        n = row.get("employeeCount")
        if not d or n is None or n == "":
            continue
        try:
            v = float(n)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        by_date[d] = v
    pts = [
        {"date": d, "value": v, "symbol": str(symbol).upper()}
        for d, v in sorted(by_date.items())
    ]
    meta = {
        "symbol": str(symbol).upper(),
        "n": len(pts),
        "source": "fmp_historical_employee_count",
        "unit": "employees",
    }
    return pts, meta


def fetch_revenue_per_employee(
    symbol: str,
    *,
    limit: int = 40,
    scale_revenue: float = 1.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Annual revenue / employeeCount (USD or reported currency per head)."""
    emp_pts, emp_meta = fetch_employee_count(symbol, limit=limit)
    rows = _get(
        "income-statement",
        {
            "symbol": str(symbol).upper(),
            "period": "annual",
            "limit": max(20, int(limit)),
        },
    )
    if not isinstance(rows, list):
        raise RuntimeError(
            f"FMP annual income-statement unexpected for {symbol}: {type(rows)}"
        )
    rev_by_year: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = str(row.get("date") or "")[:10]
        rev = row.get("revenue")
        if not d or rev is None or rev == "":
            continue
        rev_by_year[d[:4]] = float(rev) / float(scale_revenue)

    pts: list[dict[str, Any]] = []
    for p in emp_pts:
        year = str(p["date"])[:4]
        rev = rev_by_year.get(year)
        emp = float(p["value"])
        if rev is None or emp <= 0:
            continue
        pts.append(
            {
                "date": p["date"],
                "value": round(rev / emp, 2),
                "symbol": str(symbol).upper(),
            }
        )
    meta = {
        "symbol": str(symbol).upper(),
        "n": len(pts),
        "employee_n": emp_meta.get("n"),
        "source": "fmp_revenue_per_employee",
        "unit": "usd_per_employee",
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
