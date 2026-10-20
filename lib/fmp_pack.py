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
FMP_SOURCES = _CAPEX | _REV | _SEG | _TX


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
    seg_by_key: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = field(
        default_factory=dict
    )
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_catalog(cls, catalog: dict[str, Any]) -> "FmpCaches":
        out = cls()
        for meta in catalog.get("series") or []:
            src = meta.get("source")
            if src in _CAPEX:
                if src == "fmp_capex" and not meta.get("fmp_symbols"):
                    for s in fmplib.DEFAULT_HYPERSCALERS:
                        if s not in out.capex_symbols:
                            out.capex_symbols.append(s)
                _add_syms(out.capex_symbols, meta)
            elif src in _REV:
                _add_syms(out.rev_symbols, meta)
            elif src in _SEG:
                _add_syms(out.rev_symbols, meta)  # symbol only; fetch lazy in resolve
            elif src in _TX:
                _add_syms(out.tx_symbols, meta)
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
                )
                self.notes.append(
                    f"fmp: fetched CapEx for {','.join(self.capex_symbols)}"
                )
            if src == "fmp_capex_ticker":
                sym = str(meta.get("fmp_symbol") or "").upper()
                if not sym:
                    raise RuntimeError("fmp_symbol required")
                pts = [
                    {"date": p["date"], "value": p["value"]}
                    for p in self.capex_by_symbol.get(sym) or []
                    if p["date"] >= start
                ]
                return pts, f"{key}: FMP CapEx {sym} n={len(pts)}"
            agg_syms = [
                str(s).upper()
                for s in (meta.get("fmp_symbols") or list(fmplib.DEFAULT_HYPERSCALERS))
            ]
            subset = {
                s: self.capex_by_symbol[s]
                for s in agg_syms
                if s in self.capex_by_symbol
            }
            pts, mm = fmplib.aggregate_capex_points(
                subset, require_all=bool(meta.get("require_all", True))
            )
            pts = [p for p in pts if p["date"] >= start]
            return (
                pts,
                f"{key}: FMP CapEx sum {','.join(mm.get('symbols') or [])} "
                f"quarters={mm.get('quarters')}",
            )

        if src in _REV:
            rev_start = fmplib.revenue_history_start(start, meta)
            if self.rev_by_symbol is None:
                if not self.rev_symbols:
                    raise RuntimeError("no FMP revenue symbols in catalog")
                self.rev_by_symbol = fmplib.fetch_revenue_by_symbol(
                    self.rev_symbols,
                    limit=int(meta.get("fmp_limit") or 40),
                )
                self.notes.append(
                    f"fmp: fetched revenue for {','.join(self.rev_symbols)} "
                    f"(history from {rev_start})"
                )
            if src == "fmp_revenue_ticker":
                sym = str(meta.get("fmp_symbol") or "").upper()
                if not sym:
                    raise RuntimeError("fmp_symbol required")
                pts = [
                    {"date": p["date"], "value": p["value"]}
                    for p in self.rev_by_symbol.get(sym) or []
                    if p["date"] >= rev_start
                ]
                return pts, f"{key}: FMP revenue {sym} n={len(pts)}"
            agg_syms = [str(s).upper() for s in (meta.get("fmp_symbols") or [])]
            subset = {
                s: self.rev_by_symbol[s]
                for s in agg_syms
                if s in self.rev_by_symbol
            }
            pts, mm = fmplib.aggregate_revenue_points(
                subset, require_all=bool(meta.get("require_all", False))
            )
            pts = [p for p in pts if p["date"] >= rev_start]
            return (
                pts,
                f"{key}: FMP revenue sum {','.join(mm.get('symbols') or [])} "
                f"quarters={mm.get('quarters')}",
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
            cache_key = (sym, tuple(aliases))
            if cache_key not in self.seg_by_key:
                pts_all, mm = fmplib.fetch_quarterly_segment_revenue(
                    sym,
                    aliases,
                    limit=int(meta.get("fmp_limit") or 40),
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
                        self.tx_cache[(sym, year, quarter)] = tr
                self.notes.append(
                    f"fmp: cached {len(self.tx_cache)} transcripts for "
                    f"{','.join(self.tx_symbols)}"
                )

            def _tx_points_for(sym: str) -> list[dict[str, Any]]:
                by_date: dict[str, float] = defaultdict(float)
                for (s, _y, _q), tr in self.tx_cache.items():
                    if s != sym:
                        continue
                    d_raw = str(tr.get("date") or "")[:10]
                    if not d_raw:
                        continue
                    hits = fmplib.count_keyword_hits(
                        str(tr.get("content") or ""), kws
                    )
                    key_d = fmplib.calendar_quarter_end(d_raw)
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

        raise RuntimeError(f"not an FMP source: {src}")
