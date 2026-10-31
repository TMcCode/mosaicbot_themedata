"""Shared FMP CapEx / revenue / transcript-keyword resolution for pack jobs.

Catalog series use sources:
  fmp_capex | fmp_capex_ticker
  fmp_revenue | fmp_revenue_ticker
  fmp_transcript_keywords | fmp_transcript_keywords_ticker

Jobs call ``FmpCaches.from_catalog(catalog)`` once, then
``caches.resolve(meta, start=...)`` inside the series loop.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from lib import fmp as fmplib

_CAPEX = frozenset({"fmp_capex", "fmp_capex_ticker"})
_REV = frozenset({"fmp_revenue", "fmp_revenue_ticker"})
_SEG = frozenset({"fmp_revenue_segment"})
_TX = frozenset({"fmp_transcript_keywords", "fmp_transcript_keywords_ticker"})
_EMP = frozenset({"fmp_employee_count"})
_RPE = frozenset({"fmp_revenue_per_employee"})
_INC = frozenset({"fmp_income_field", "fmp_income_field_ticker"})
_CF = frozenset({"fmp_cashflow_field", "fmp_cashflow_field_ticker"})
_BS = frozenset({"fmp_balance_field", "fmp_balance_field_ticker"})
FMP_SOURCES = _CAPEX | _REV | _SEG | _TX | _EMP | _RPE | _INC | _CF | _BS


def is_fmp_source(src: str | None) -> bool:
    return str(src or "") in FMP_SOURCES


def _add_syms(bucket: list[str], meta_row: dict[str, Any]) -> None:
    syms = list(meta_row.get("fmp_symbols") or [])
    one = meta_row.get("fmp_symbol")
    if one:
        syms.append(str(one))
    for s in syms:
        su = str(s).upper()
        if su and su not in bucket:
            bucket.append(su)


@dataclass
class FmpCaches:
    capex_symbols: list[str] = field(default_factory=list)
    rev_symbols: list[str] = field(default_factory=list)
    tx_symbols: list[str] = field(default_factory=list)
    capex_by_symbol: dict[str, list[dict[str, Any]]] | None = None
    rev_by_symbol: dict[str, list[dict[str, Any]]] | None = None
    tx_cache: dict[tuple[str, int, int], dict[str, Any]] | None = None
    # (symbol, tuple(aliases)) → points
    seg_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = field(
        default_factory=dict
    )
    # (field_name, to_usd) → {symbol: points}
    income_field_by: dict[tuple[str, bool], dict[str, list[dict[str, Any]]]] = field(
        default_factory=dict
    )
    income_field_symbols: list[str] = field(default_factory=list)
    # (field_name, to_usd, period) → {symbol: points}
    cashflow_field_by: dict[
        tuple[str, bool, str], dict[str, list[dict[str, Any]]]
    ] = field(default_factory=dict)
    cashflow_field_symbols: list[str] = field(default_factory=list)
    balance_field_by: dict[
        tuple[str, bool, str, bool], dict[str, list[dict[str, Any]]]
    ] = field(default_factory=dict)
    balance_field_symbols: list[str] = field(default_factory=list)
    # If any CapEx/revenue series sets fmp_to_usd, convert the whole cache
    # batch at latest FRED FX (constant over history). Safe for USD filers.
    capex_to_usd: bool = False
    rev_to_usd: bool = False
    income_to_usd: bool = False
    cashflow_to_usd: bool = False
    balance_to_usd: bool = False
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_catalog(cls, catalog: dict[str, Any]) -> "FmpCaches":
        out = cls()
        for meta in catalog.get("series") or []:
            src = meta.get("source")
            if src in _CAPEX:
                # Annual CapEx is fetched per-series; don't pollute quarterly cache.
                if str(meta.get("fmp_period") or "quarter").strip().lower() == "annual":
                    if meta.get("fmp_to_usd"):
                        out.capex_to_usd = True
                    continue
                if src == "fmp_capex" and not meta.get("fmp_symbols"):
                    for s in fmplib.DEFAULT_HYPERSCALERS:
                        if s not in out.capex_symbols:
                            out.capex_symbols.append(s)
                _add_syms(out.capex_symbols, meta)
                if meta.get("fmp_to_usd"):
                    out.capex_to_usd = True
            elif src in _REV:
                _add_syms(out.rev_symbols, meta)
                if meta.get("fmp_to_usd"):
                    out.rev_to_usd = True
            elif src in _SEG:
                _add_syms(out.rev_symbols, meta)  # symbol only; fetch lazy in resolve
                if meta.get("fmp_to_usd"):
                    out.rev_to_usd = True  # segments use same reported ccy
            elif src in _TX:
                _add_syms(out.tx_symbols, meta)
            elif src in _EMP or src in _RPE:
                pass  # resolved lazily per series
            elif src in _INC:
                _add_syms(out.income_field_symbols, meta)
                if meta.get("fmp_to_usd"):
                    out.income_to_usd = True
            elif src in _CF:
                _add_syms(out.cashflow_field_symbols, meta)
                if meta.get("fmp_to_usd"):
                    out.cashflow_to_usd = True
            elif src in _BS:
                _add_syms(out.balance_field_symbols, meta)
                if meta.get("fmp_to_usd"):
                    out.balance_to_usd = True
        return out

    def resolve(
        self, meta: dict[str, Any], *, start: str
    ) -> tuple[list[dict[str, Any]], str]:
        """Return (points, note) for one FMP-backed series."""
        src = str(meta.get("source") or "")
        key = str(meta.get("key") or src)

        if src in _CAPEX:
            if self.capex_by_symbol is None:
                if not self.capex_symbols:
                    raise RuntimeError("no FMP CapEx symbols in catalog")
                self.capex_by_symbol = fmplib.fetch_capex_by_symbol(
                    self.capex_symbols,
                    limit=int(meta.get("fmp_limit") or 40),
                    to_usd=self.capex_to_usd or bool(meta.get("fmp_to_usd")),
                )
                self.notes.append(
                    f"fmp: fetched CapEx for {','.join(self.capex_symbols)}"
                    + (
                        " (to_usd=latest_FX)"
                        if (self.capex_to_usd or meta.get("fmp_to_usd"))
                        else ""
                    )
                )
            bucket = str(
                meta.get("calendar_bucket") or meta.get("fmp_bucket") or "quarter"
            ).strip().lower()
            half = bucket in ("half", "semiannual", "semi-annual", "semi", "h")
            fmp_period = str(meta.get("fmp_period") or "quarter").strip().lower()
            if src == "fmp_capex_ticker":
                sym = str(meta.get("fmp_symbol") or "").upper()
                if not sym:
                    raise RuntimeError("fmp_symbol required")
                capex_start = fmplib.revenue_history_start(start, meta)
                # Annual CapEx bypasses the shared quarterly cache (FMP often
                # zeros quarterly CapEx for some JP names while annual is real).
                if fmp_period == "annual":
                    raw = fmplib.fetch_quarterly_capex(
                        sym,
                        limit=int(meta.get("fmp_limit") or 40),
                        calendar_bucket=False,
                        scale=1e9,
                        to_usd=self.capex_to_usd or bool(meta.get("fmp_to_usd")),
                        period="annual",
                    )
                    pts = [
                        {"date": p["date"], "value": p["value"]}
                        for p in raw
                        if p["date"] >= capex_start
                    ]
                    return (
                        pts,
                        f"{key}: FMP CapEx {sym} annual n={len(pts)}",
                    )
                pts = [
                    {"date": p["date"], "value": p["value"]}
                    for p in self.capex_by_symbol.get(sym) or []
                    if p["date"] >= capex_start
                ]
                if half:
                    pts = fmplib.rebucket_points(pts, bucket="half")
                return (
                    pts,
                    f"{key}: FMP CapEx {sym} n={len(pts)}"
                    + (f" bucket={bucket}" if half else ""),
                )
            agg_syms = [
                str(s).upper()
                for s in (meta.get("fmp_symbols") or list(fmplib.DEFAULT_HYPERSCALERS))
            ]
            subset = {
                s: self.capex_by_symbol[s]
                for s in agg_syms
                if s in self.capex_by_symbol
            }
            if half:
                subset = {
                    s: fmplib.rebucket_points(rows, bucket="half")
                    for s, rows in subset.items()
                }
            pts, mm = fmplib.aggregate_capex_points(
                subset, require_all=bool(meta.get("require_all", True))
            )
            pts = [p for p in pts if p["date"] >= start]
            return (
                pts,
                f"{key}: FMP CapEx sum {','.join(mm.get('symbols') or [])} "
                f"quarters={mm.get('quarters')}"
                + (f" bucket={bucket}" if half else ""),
            )

        if src in _REV:
            rev_start = fmplib.revenue_history_start(start, meta)
            if self.rev_by_symbol is None:
                if not self.rev_symbols:
                    raise RuntimeError("no FMP revenue symbols in catalog")
                to_usd = self.rev_to_usd or bool(meta.get("fmp_to_usd"))
                self.rev_by_symbol = fmplib.fetch_revenue_by_symbol(
                    self.rev_symbols,
                    limit=int(meta.get("fmp_limit") or 40),
                    to_usd=to_usd,
                )
                self.notes.append(
                    f"fmp: fetched revenue for {','.join(self.rev_symbols)} "
                    f"(history from {rev_start}"
                    f"{'; to_usd=latest_FX' if to_usd else ''})"
                )
            bucket = str(
                meta.get("calendar_bucket") or meta.get("fmp_bucket") or "quarter"
            ).strip().lower()
            if src == "fmp_revenue_ticker":
                sym = str(meta.get("fmp_symbol") or "").upper()
                if not sym:
                    raise RuntimeError("fmp_symbol required")
                exclude = {
                    str(d)[:10]
                    for d in (meta.get("exclude_dates") or [])
                    if d
                }
                pts = [
                    {"date": p["date"], "value": p["value"]}
                    for p in self.rev_by_symbol.get(sym) or []
                    if p["date"] >= rev_start and p["date"] not in exclude
                ]
                overrides = meta.get("override_points") or []
                if overrides:
                    by = {p["date"]: p for p in pts}
                    for ov in overrides:
                        d = str(ov.get("date") or "")[:10]
                        if not d or d < rev_start:
                            continue
                        try:
                            by[d] = {"date": d, "value": float(ov["value"])}
                        except (TypeError, ValueError, KeyError):
                            continue
                    pts = [by[d] for d in sorted(by)]
                if bucket in ("half", "semiannual", "semi-annual", "semi", "h"):
                    pts = fmplib.rebucket_points(pts, bucket="half")
                note = f"{key}: FMP revenue {sym} n={len(pts)}"
                if exclude:
                    note += f" excluded={sorted(exclude)}"
                if overrides:
                    note += f" overrides={len(overrides)}"
                if bucket not in ("", "quarter", "quarterly"):
                    note += f" bucket={bucket}"
                return (pts, note)
            agg_syms = [str(s).upper() for s in (meta.get("fmp_symbols") or [])]
            # Optional per-symbol date drops / overrides (FMP bad prints).
            member_excl: dict[str, set[str]] = {}
            raw_excl = meta.get("exclude_member_dates") or {}
            if isinstance(raw_excl, dict):
                for sym_k, dates in raw_excl.items():
                    member_excl[str(sym_k).upper()] = {
                        str(d)[:10] for d in (dates or []) if d
                    }
            member_over: dict[str, dict[str, float]] = {}
            raw_over = meta.get("override_member_points") or {}
            if isinstance(raw_over, dict):
                for sym_k, rows in raw_over.items():
                    mp: dict[str, float] = {}
                    for ov in rows or []:
                        d = str((ov or {}).get("date") or "")[:10]
                        if not d:
                            continue
                        try:
                            mp[d] = float(ov["value"])
                        except (TypeError, ValueError, KeyError):
                            continue
                    if mp:
                        member_over[str(sym_k).upper()] = mp
            subset = {}
            for s in agg_syms:
                if s not in self.rev_by_symbol:
                    continue
                drop = member_excl.get(s) or set()
                rows = [
                    p
                    for p in self.rev_by_symbol[s]
                    if p["date"] not in drop
                ]
                if s in member_over:
                    by = {p["date"]: p for p in rows}
                    for d, v in member_over[s].items():
                        by[d] = {"date": d, "value": v, "symbol": s}
                    rows = [by[d] for d in sorted(by)]
                subset[s] = rows
            pts, mm = fmplib.aggregate_revenue_points(
                subset, require_all=bool(meta.get("require_all", False))
            )
            pts = [p for p in pts if p["date"] >= rev_start]
            if bucket in ("half", "semiannual", "semi-annual", "semi", "h"):
                # Rebucket each member first so halves align, then re-sum.
                half_by = {
                    s: fmplib.rebucket_points(rows, bucket="half")
                    for s, rows in subset.items()
                }
                pts, mm = fmplib.aggregate_revenue_points(
                    half_by, require_all=bool(meta.get("require_all", False))
                )
                pts = [p for p in pts if p["date"] >= rev_start]
            return (
                pts,
                f"{key}: FMP revenue sum {','.join(mm.get('symbols') or [])} "
                f"quarters={mm.get('quarters')}"
                + (f" bucket={bucket}" if bucket not in ("", "quarter", "quarterly") else ""),
            )

        if src in _SEG:
            sym = str(meta.get("fmp_symbol") or "").upper()
            if not sym:
                raise RuntimeError("fmp_symbol required for fmp_revenue_segment")
            aliases = [
                str(a).strip()
                for a in (meta.get("segment_aliases") or meta.get("segments") or [])
                if str(a).strip()
            ]
            if not aliases:
                raise RuntimeError("segment_aliases required")
            rev_start = fmplib.revenue_history_start(start, meta)
            to_usd = self.rev_to_usd or bool(meta.get("fmp_to_usd"))
            period = str(meta.get("fmp_period") or "quarter").strip().lower()
            seg_dim = str(
                meta.get("segment_dimension")
                or meta.get("fmp_segment_dimension")
                or "product"
            ).strip().lower()
            cache_key = (
                sym,
                tuple(aliases),
                bool(meta.get("skip_segment_sum_guard")),
                to_usd,
                period,
                seg_dim,
            )
            if cache_key not in self.seg_by_key:
                pts_all, mm = fmplib.fetch_quarterly_segment_revenue(
                    sym,
                    aliases,
                    limit=int(meta.get("fmp_limit") or 40),
                    skip_segment_sum_guard=bool(
                        meta.get("skip_segment_sum_guard")
                    ),
                    to_usd=to_usd,
                    period=period,
                    segment_dimension=seg_dim,
                )
                self.seg_by_key[cache_key] = pts_all
                self.notes.append(
                    f"fmp: segment revenue {sym} aliases={aliases} "
                    f"labels={mm.get('labels_used')} matched={mm.get('matched_prints')} "
                    f"(history from {rev_start})"
                )
            pts = [
                {"date": p["date"], "value": p["value"]}
                for p in self.seg_by_key[cache_key]
                if p["date"] >= rev_start
            ]
            return pts, f"{key}: FMP segment revenue {sym} n={len(pts)}"

        if src in _TX:
            kws = [
                str(k).strip()
                for k in (meta.get("keywords") or [])
                if str(k).strip()
            ]
            if not kws:
                raise RuntimeError("keywords required")
            limit_q = int(meta.get("fmp_limit") or 16)
            if self.tx_cache is None:
                if not self.tx_symbols:
                    raise RuntimeError("no FMP transcript symbols in catalog")
                self.tx_cache = {}
                for sym in self.tx_symbols:
                    for row in fmplib.list_transcript_dates(sym)[:limit_q]:
                        year = int(row.get("fiscalYear") or row.get("year") or 0)
                        quarter = int(row.get("quarter") or 0)
                        if not year or not quarter:
                            continue
                        tr = fmplib.fetch_transcript(sym, year=year, quarter=quarter)
                        time.sleep(0.1)
                        if not tr:
                            continue
                        call_d = str(row.get("date") or "")[:10]
                        if call_d:
                            tr = {**tr, "_call_date": call_d}
                        self.tx_cache[(sym, year, quarter)] = tr
                self.notes.append(
                    f"fmp: cached {len(self.tx_cache)} transcripts for "
                    f"{','.join(self.tx_symbols)}"
                )

            def _tx_points_for(sym: str) -> list[dict[str, Any]]:
                by_date: dict[str, float] = defaultdict(float)
                for (s, y, q), tr in self.tx_cache.items():
                    if s != sym:
                        continue
                    hits = fmplib.count_keyword_hits(
                        str(tr.get("content") or ""), kws
                    )
                    # Call-date → prior calendar quarter end (handles June FYE
                    # like SLQT; fiscal_quarter_end assumes calendar FY).
                    call_d = str(
                        tr.get("_call_date") or tr.get("date") or ""
                    )[:10]
                    if call_d:
                        key_d = fmplib.transcript_period_end(call_d)
                    else:
                        key_d = fmplib.fiscal_quarter_end(int(y), int(q))
                    by_date[key_d] += float(sum(hits.values()))
                return [
                    {"date": d, "value": round(v, 4), "symbol": sym}
                    for d, v in sorted(by_date.items())
                ]

            if src == "fmp_transcript_keywords_ticker":
                sym = str(meta.get("fmp_symbol") or "").upper()
                if not sym:
                    raise RuntimeError("fmp_symbol required")
                pts = [
                    {"date": p["date"], "value": p["value"]}
                    for p in _tx_points_for(sym)
                    if p["date"] >= start
                ]
                return pts, f"{key}: FMP transcript keywords {sym} n={len(pts)}"
            by_sym = {
                s: _tx_points_for(s)
                for s in [str(x).upper() for x in (meta.get("fmp_symbols") or [])]
            }
            pts, mm = fmplib.aggregate_transcript_keyword_points(
                by_sym, require_all=bool(meta.get("require_all", False))
            )
            pts = [p for p in pts if p["date"] >= start]
            return (
                pts,
                f"{key}: FMP transcript keywords sum "
                f"{','.join(mm.get('symbols') or [])} quarters={mm.get('quarters')}",
            )

        if src in _EMP:
            sym = str(meta.get("fmp_symbol") or "").upper()
            if not sym:
                raise RuntimeError("fmp_symbol required for fmp_employee_count")
            pts_all, mm = fmplib.fetch_employee_count(
                sym, limit=int(meta.get("fmp_limit") or 40)
            )
            pts = [
                {"date": p["date"], "value": p["value"]}
                for p in pts_all
                if p["date"] >= start
            ]
            return (
                pts,
                f"{key}: FMP employee count {sym} n={len(pts)} "
                f"(filed={mm.get('n')})",
            )

        if src in _RPE:
            sym = str(meta.get("fmp_symbol") or "").upper()
            if not sym:
                raise RuntimeError("fmp_symbol required for fmp_revenue_per_employee")
            pts_all, mm = fmplib.fetch_revenue_per_employee(
                sym, limit=int(meta.get("fmp_limit") or 40)
            )
            pts = [
                {"date": p["date"], "value": p["value"]}
                for p in pts_all
                if p["date"] >= start
            ]
            return (
                pts,
                f"{key}: FMP revenue/employee {sym} n={len(pts)} "
                f"(emp_prints={mm.get('employee_n')})",
            )

        if src in _INC:
            field_name = str(
                meta.get("fmp_income_field")
                or meta.get("income_field")
                or "researchAndDevelopmentExpenses"
            ).strip()
            if not field_name:
                raise RuntimeError("fmp_income_field required")
            to_usd = self.income_to_usd or bool(meta.get("fmp_to_usd"))
            cache_key = (field_name, bool(to_usd))
            if cache_key not in self.income_field_by:
                syms = list(self.income_field_symbols)
                one = str(meta.get("fmp_symbol") or "").upper()
                if one and one not in syms:
                    syms.append(one)
                for s in meta.get("fmp_symbols") or []:
                    su = str(s).upper()
                    if su and su not in syms:
                        syms.append(su)
                if not syms:
                    raise RuntimeError("no FMP income-field symbols in catalog")
                self.income_field_by[cache_key] = fmplib.fetch_income_field_by_symbol(
                    syms,
                    field_name,
                    limit=int(meta.get("fmp_limit") or 40),
                    scale=float(meta.get("fmp_scale") or 1e9),
                    to_usd=to_usd,
                )
                self.notes.append(
                    f"fmp: fetched income field {field_name} for {','.join(syms)}"
                    + (" (to_usd=latest_FX)" if to_usd else "")
                )
            bucket = self.income_field_by[cache_key]
            if src == "fmp_income_field_ticker":
                sym = str(meta.get("fmp_symbol") or "").upper()
                if not sym:
                    raise RuntimeError("fmp_symbol required")
                pts = [
                    {"date": p["date"], "value": p["value"]}
                    for p in (bucket.get(sym) or [])
                    if p["date"] >= start
                ]
                return pts, f"{key}: FMP {field_name} {sym} n={len(pts)}"
            agg_syms = [
                str(s).upper() for s in (meta.get("fmp_symbols") or []) if str(s).strip()
            ]
            subset = {s: bucket[s] for s in agg_syms if s in bucket}
            pts, mm = fmplib.aggregate_revenue_points(
                subset, require_all=bool(meta.get("require_all", False))
            )
            pts = [p for p in pts if p["date"] >= start]
            return (
                pts,
                f"{key}: FMP {field_name} sum {','.join(mm.get('symbols') or [])} "
                f"quarters={mm.get('quarters')}",
            )

        if src in _CF:
            field_name = str(
                meta.get("fmp_cashflow_field")
                or meta.get("cashflow_field")
                or "commonDividendsPaid"
            ).strip()
            if not field_name:
                raise RuntimeError("fmp_cashflow_field required")
            to_usd = self.cashflow_to_usd or bool(meta.get("fmp_to_usd"))
            period = str(meta.get("fmp_period") or "quarter").strip().lower()
            abs_value = meta.get("fmp_abs_value")
            if abs_value is None:
                abs_value = True
            abs_value = bool(abs_value)
            cache_key = (field_name, bool(to_usd), period, abs_value)
            if cache_key not in self.cashflow_field_by:
                syms = list(self.cashflow_field_symbols)
                one = str(meta.get("fmp_symbol") or "").upper()
                if one and one not in syms:
                    syms.append(one)
                for s in meta.get("fmp_symbols") or []:
                    su = str(s).upper()
                    if su and su not in syms:
                        syms.append(su)
                if not syms:
                    raise RuntimeError("no FMP cashflow-field symbols in catalog")
                self.cashflow_field_by[cache_key] = (
                    fmplib.fetch_cashflow_field_by_symbol(
                        syms,
                        field_name,
                        limit=int(meta.get("fmp_limit") or 40),
                        scale=float(meta.get("fmp_scale") or 1e9),
                        to_usd=to_usd,
                        period=period,
                        calendar_bucket=period == "quarter",
                        abs_value=abs_value,
                    )
                )
                self.notes.append(
                    f"fmp: fetched cashflow field {field_name} ({period}) for "
                    f"{','.join(syms)}"
                    + (" (to_usd=latest_FX)" if to_usd else "")
                )
            bucket = self.cashflow_field_by[cache_key]
            if src == "fmp_cashflow_field_ticker":
                sym = str(meta.get("fmp_symbol") or "").upper()
                if not sym:
                    raise RuntimeError("fmp_symbol required")
                pts = [
                    {"date": p["date"], "value": p["value"]}
                    for p in (bucket.get(sym) or [])
                    if p["date"] >= start
                ]
                return pts, f"{key}: FMP CF {field_name} {sym} n={len(pts)}"
            agg_syms = [
                str(s).upper() for s in (meta.get("fmp_symbols") or []) if str(s).strip()
            ]
            subset = {s: bucket[s] for s in agg_syms if s in bucket}
            pts, mm = fmplib.aggregate_revenue_points(
                subset, require_all=bool(meta.get("require_all", False))
            )
            pts = [p for p in pts if p["date"] >= start]
            return (
                pts,
                f"{key}: FMP CF {field_name} sum {','.join(mm.get('symbols') or [])} "
                f"quarters={mm.get('quarters')}",
            )

        if src in _BS:
            field_name = str(
                meta.get("fmp_balance_field")
                or meta.get("balance_field")
                or "totalDebt"
            ).strip()
            if not field_name:
                raise RuntimeError("fmp_balance_field required")
            to_usd = self.balance_to_usd or bool(meta.get("fmp_to_usd"))
            period = str(meta.get("fmp_period") or "quarter").strip().lower()
            abs_value = bool(meta.get("fmp_abs_value") or False)
            cache_key = (field_name, bool(to_usd), period, abs_value)
            if cache_key not in self.balance_field_by:
                syms = list(self.balance_field_symbols)
                one = str(meta.get("fmp_symbol") or "").upper()
                if one and one not in syms:
                    syms.append(one)
                for s in meta.get("fmp_symbols") or []:
                    su = str(s).upper()
                    if su and su not in syms:
                        syms.append(su)
                if not syms:
                    raise RuntimeError("no FMP balance-field symbols in catalog")
                self.balance_field_by[cache_key] = (
                    fmplib.fetch_balance_field_by_symbol(
                        syms,
                        field_name,
                        limit=int(meta.get("fmp_limit") or 40),
                        scale=float(meta.get("fmp_scale") or 1e9),
                        to_usd=to_usd,
                        period=period,
                        calendar_bucket=period == "quarter",
                        abs_value=abs_value,
                    )
                )
                self.notes.append(
                    f"fmp: fetched balance field {field_name} ({period}) for "
                    f"{','.join(syms)}"
                    + (" (to_usd=latest_FX)" if to_usd else "")
                )
            bucket = self.balance_field_by[cache_key]
            if src == "fmp_balance_field_ticker":
                sym = str(meta.get("fmp_symbol") or "").upper()
                if not sym:
                    raise RuntimeError("fmp_symbol required")
                bs_start = fmplib.revenue_history_start(start, meta)
                pts = [
                    {"date": p["date"], "value": p["value"]}
                    for p in (bucket.get(sym) or [])
                    if p["date"] >= bs_start
                ]
                return pts, f"{key}: FMP BS {field_name} {sym} n={len(pts)}"
            agg_syms = [
                str(s).upper() for s in (meta.get("fmp_symbols") or []) if str(s).strip()
            ]
            subset = {s: bucket[s] for s in agg_syms if s in bucket}
            pts, mm = fmplib.aggregate_revenue_points(
                subset, require_all=bool(meta.get("require_all", False))
            )
            pts = [p for p in pts if p["date"] >= start]
            return (
                pts,
                f"{key}: FMP BS {field_name} sum {','.join(mm.get('symbols') or [])} "
                f"quarters={mm.get('quarters')}",
            )

        raise RuntimeError(f"not an FMP source: {src}")
