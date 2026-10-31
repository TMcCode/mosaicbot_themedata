"""Tournament / cup monthly fixture counts from openfootball (GitHub).

Covers World Cup, UEFA Euro, and UEFA Champions League. Same light
``.txt`` scrape pattern as ``openfootball_pl`` — no API key.

Repos:
  https://github.com/openfootball/worldcup
  https://github.com/openfootball/euro
  https://github.com/openfootball/champions-league
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+openfootball_cups; +https://stockcontext.info)"
RAW_WC = "https://raw.githubusercontent.com/openfootball/worldcup/master"
RAW_EURO = "https://raw.githubusercontent.com/openfootball/euro/master"
RAW_CL = "https://raw.githubusercontent.com/openfootball/champions-league/master"

_MONTHS = {
    m: i
    for i, m in enumerate(
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1
    )
}
_DATE_LINE = re.compile(
    r"^(?:[A-Za-z]{3}\s+)?([A-Za-z]{3})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$"
)
_DATE_INLINE = re.compile(
    r"\b([A-Za-z]{3})\s+(\d{1,2})(?:\s+(\d{4}))?\b"
)
_MATCH_LINE = re.compile(r"(?:\bv\b|\d+\s*-\s*\d+)", re.I)

# Folder slug → tournament year (openfootball naming).
_WC_FOLDERS: dict[int, str] = {
    2018: "2018--russia",
    2022: "2022--qatar",
    2026: "2026--canada-usa-mexico",
}
_EURO_FOLDERS: dict[int, str] = {
    2016: "2016--france",
    2021: "2021--europe",  # played 2021 (labelled Euro 2020)
    2024: "2024--germany",
    2028: "2028--united_kingdom-ireland",
}


def _http_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/plain"})
    with urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _horizon_iso(*, ahead_months: int) -> str:
    today = datetime.now(timezone.utc).date()
    y, m = today.year, today.month
    for _ in range(max(0, ahead_months)):
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return f"{y:04d}-{m:02d}-01"


def parse_tournament_text(text: str, *, year_hint: int) -> Counter[str]:
    """Parse WC/Euro-style files (date lines + Matchday headers)."""
    counts: Counter[str] = Counter()
    cur: str | None = None
    year = year_hint
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "=")):
            continue
        m = _DATE_LINE.match(s)
        if m:
            mon_s, _day, y_s = m.group(1), int(m.group(2)), m.group(3)
            mi = _MONTHS.get(mon_s)
            if not mi:
                continue
            if y_s:
                year = int(y_s)
            cur = f"{year:04d}-{mi:02d}-01"
            continue
        if s.startswith("▪") or "Matchday" in s or s.startswith("Round"):
            ms = list(_DATE_INLINE.finditer(s))
            if ms:
                mon_s, _day, y_s = ms[-1].group(1), int(ms[-1].group(2)), ms[-1].group(3)
                mi = _MONTHS.get(mon_s)
                if mi:
                    if y_s:
                        year = int(y_s)
                    cur = f"{year:04d}-{mi:02d}-01"
            continue
        if cur and _MATCH_LINE.search(s) and not s.lower().startswith("group"):
            counts[cur] += 1
    return counts


def parse_season_text(text: str, *, season_start_year: int) -> Counter[str]:
    """Parse CL-style Aug–May season files (same shape as PL)."""
    counts: Counter[str] = Counter()
    cur: str | None = None
    year = season_start_year
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "=", "▪", "•")):
            continue
        if s.startswith("("):
            continue
        m = _DATE_LINE.match(s)
        if m:
            mon_s, _day, y_s = m.group(1), int(m.group(2)), m.group(3)
            mi = _MONTHS.get(mon_s)
            if not mi:
                continue
            if y_s:
                year = int(y_s)
            else:
                year = season_start_year if mi >= 8 else season_start_year + 1
            cur = f"{year:04d}-{mi:02d}-01"
            continue
        if cur and _MATCH_LINE.search(s):
            counts[cur] += 1
    return counts


def _points_from_counter(
    by_month: Counter[str], *, ahead_months: int
) -> list[dict[str, Any]]:
    horizon = _horizon_iso(ahead_months=ahead_months)
    return [
        {"date": d, "value": float(v)}
        for d, v in sorted(by_month.items())
        if d <= horizon
    ]


def fetch_world_cup_monthly(
    *,
    start_year: int = 2018,
    ahead_months: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_month: Counter[str] = Counter()
    fetched: list[str] = []
    missing: list[str] = []
    for year, folder in sorted(_WC_FOLDERS.items()):
        if year < start_year:
            continue
        url = f"{RAW_WC}/{folder}/cup.txt"
        try:
            text = _http_text(url)
        except Exception:
            missing.append(folder)
            continue
        part = parse_tournament_text(text, year_hint=year)
        by_month.update(part)
        fetched.append(f"{year}:{sum(part.values())}")

    if not by_month:
        raise RuntimeError(
            f"openfootball WC: no tournaments fetched (missing={missing})"
        )
    pts = _points_from_counter(by_month, ahead_months=ahead_months)
    meta = {
        "source": "openfootball_worldcup",
        "repo": "openfootball/worldcup",
        "tournaments_fetched": fetched,
        "tournaments_missing": missing,
        "horizon": _horizon_iso(ahead_months=ahead_months),
        "n_months": len(pts),
        "n_matches": int(sum(p["value"] for p in pts)),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return pts, meta


def fetch_euro_monthly(
    *,
    start_year: int = 2016,
    ahead_months: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_month: Counter[str] = Counter()
    fetched: list[str] = []
    missing: list[str] = []
    for year, folder in sorted(_EURO_FOLDERS.items()):
        if year < start_year:
            continue
        url = f"{RAW_EURO}/{folder}/euro.txt"
        try:
            text = _http_text(url)
        except Exception:
            missing.append(folder)
            continue
        part = parse_tournament_text(text, year_hint=year)
        by_month.update(part)
        fetched.append(f"{year}:{sum(part.values())}")

    if not by_month:
        raise RuntimeError(
            f"openfootball Euro: no tournaments fetched (missing={missing})"
        )
    pts = _points_from_counter(by_month, ahead_months=ahead_months)
    meta = {
        "source": "openfootball_euro",
        "repo": "openfootball/euro",
        "tournaments_fetched": fetched,
        "tournaments_missing": missing,
        "horizon": _horizon_iso(ahead_months=ahead_months),
        "n_months": len(pts),
        "n_matches": int(sum(p["value"] for p in pts)),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return pts, meta


def fetch_champions_league_monthly(
    *,
    start_season: int = 2017,
    end_season: int | None = None,
    ahead_months: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    today = datetime.now(timezone.utc).date()
    cur_season = today.year if today.month >= 8 else today.year - 1
    if end_season is None:
        end_season = cur_season + 1

    by_month: Counter[str] = Counter()
    fetched: list[str] = []
    missing: list[str] = []
    for sy in range(start_season, end_season + 1):
        label = f"{sy}-{str(sy + 1)[2:]}"
        url = f"{RAW_CL}/{label}/cl.txt"
        try:
            text = _http_text(url)
        except Exception:
            missing.append(label)
            continue
        part = parse_season_text(text, season_start_year=sy)
        by_month.update(part)
        fetched.append(f"{label}:{sum(part.values())}")

    if not by_month:
        raise RuntimeError(
            "openfootball CL: no season files fetched "
            f"(tried {start_season}–{end_season}; missing={missing})"
        )
    pts = _points_from_counter(by_month, ahead_months=ahead_months)
    meta = {
        "source": "openfootball_champions_league",
        "repo": "openfootball/champions-league",
        "seasons_fetched": fetched,
        "seasons_missing": missing,
        "horizon": _horizon_iso(ahead_months=ahead_months),
        "n_months": len(pts),
        "n_matches": int(sum(p["value"] for p in pts)),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return pts, meta
