"""Premier League monthly fixture counts from openfootball/england (GitHub).

No API key. One small ``.txt`` per season (~0.5–1 KB requests). Includes
scheduled future matchdays when the season file has them.

Source: https://github.com/openfootball/england
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

UA = "mosaicbot_themedata/0.1 (+openfootball_pl; +https://stockcontext.info)"
RAW = "https://raw.githubusercontent.com/openfootball/england/master"
_MONTHS = {
    m: i
    for i, m in enumerate(
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1
    )
}
_DATE_LINE = re.compile(
    r"^(?:[A-Za-z]{3}\s+)?([A-Za-z]{3})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$"
)
# "Team A  v Team B" or "Team A  2-1 (0-0)  Team B"
_MATCH_LINE = re.compile(
    r"(?:\bv\b|\d+\s*-\s*\d+)",
    re.I,
)


def _http_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA, "Accept": "text/plain"})
    with urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[2:]}"


def parse_season_text(text: str, *, season_start_year: int) -> Counter[str]:
    """Return Counter of ``YYYY-MM-01`` → match count."""
    counts: Counter[str] = Counter()
    cur: str | None = None
    year = season_start_year
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "=", "▪", "•")):
            continue
        if s.startswith("("):  # scorer footnotes
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


def fetch_monthly_match_counts(
    *,
    start_season: int = 2017,
    end_season: int | None = None,
    ahead_months: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Monthly PL fixture counts from ``start_season`` through next season file.

    Points extend into the future when openfootball has scheduled matchdays.
    ``ahead_months`` caps how far past today we keep (default 12).
    """
    today = datetime.now(timezone.utc).date()
    # Current PL season start year: Aug–Jul → if month>=8 use this year else prior.
    cur_season = today.year if today.month >= 8 else today.year - 1
    if end_season is None:
        end_season = cur_season + 1  # try next season file for forward schedule

    by_month: Counter[str] = Counter()
    fetched: list[str] = []
    missing: list[str] = []
    for sy in range(start_season, end_season + 1):
        label = _season_label(sy)
        url = f"{RAW}/{label}/1-premierleague.txt"
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
            "openfootball PL: no season files fetched "
            f"(tried {start_season}–{end_season}; missing={missing})"
        )

    # Horizon: keep history + limited future
    if ahead_months > 0:
        y, m = today.year, today.month
        for _ in range(ahead_months):
            m += 1
            if m > 12:
                y, m = y + 1, 1
        horizon = f"{y:04d}-{m:02d}-01"
    else:
        horizon = today.replace(day=1).isoformat()

    pts = [
        {"date": d, "value": float(v)}
        for d, v in sorted(by_month.items())
        if d <= horizon
    ]
    # Drop empty pre-start months not in data (already only months with matches)
    meta = {
        "source": "openfootball_england",
        "repo": "openfootball/england",
        "seasons_fetched": fetched,
        "seasons_missing": missing,
        "horizon": horizon,
        "n_months": len(pts),
        "n_matches": int(sum(p["value"] for p in pts)),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    return pts, meta
