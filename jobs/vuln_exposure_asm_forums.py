#!/usr/bin/env python3
"""Vuln / exposure ASM Reddit forums sidecar.

Pack: vuln_exposure_asm_forums.
Theme: Cybersecurity '26: Vulnerability, Exposure & Attack Surface Management.
CTEM/EASM/KEV category talk + Tenable/Qualys/Rapid7/CrowdStrike/Palo Alto
names + r/cybersecurity + r/netsec + r/sysadmin volume. Chart-only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.env import load_env  # noqa: E402
from lib import r2 as r2lib  # noqa: E402
from lib import pack_series as ps  # noqa: E402

SCHEMA_VERSION = 0
PACK = "vuln_exposure_asm_forums"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+vuln_exposure_asm_forums; arctic-shift read-only)"
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "vuln_exposure_asm_forums_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_HISTORY = "weekly_history.jsonl"
_SERIES_FROM = "2024-01-01"

_WATCHED_SUBS = ("cybersecurity", "netsec", "sysadmin")
# High-throughput subs exhaust a fixed post budget in ~2–4 months.
_VOLUME_SUBS = ("cybersecurity", "sysadmin")

LEXICON: list[tuple[str, re.Pattern[str], str]] = [
    (
        "Tenable",
        re.compile(r"\btenable\b|\btenable\s*one\b|\bvmdr\b", re.I),
        "ve_tenable_mentions",
    ),
    (
        "Qualys",
        re.compile(r"\bqualys\b|\btrurisk\b", re.I),
        "ve_qualys_mentions",
    ),
    (
        "Rapid7",
        re.compile(
            r"\brapid7\b|\binsightvm\b|\bexposure\s*command\b",
            re.I,
        ),
        "ve_rapid7_mentions",
    ),
    (
        "CrowdStrike",
        re.compile(
            r"\bcrowdstrike\b|\bfalcon\s*exposure\b|\bfalcon\s*spotlight\b",
            re.I,
        ),
        "ve_crowdstrike_mentions",
    ),
    (
        "Palo Alto",
        re.compile(
            r"\bxpanse\b|\bcortex\s*xsiam\b|\bcortex\s*exposure\b|"
            r"\bpalo\s*alto(?:\s*networks)?\b|\bPAN[- ]?OS\b",
            re.I,
        ),
        "ve_paloalto_mentions",
    ),
    (
        "CTEM talk",
        re.compile(
            r"\bCTEM\b|\bcontinuous\s*threat\s*exposure\b|"
            r"\bEASM\b|\bCAASM\b|\battack\s*surface\b|"
            r"\bKEV\b|\bknown\s*exploited\b|"
            r"\bvulnerability\s*management\b|\bexposure\s*management\b|"
            r"\battack\s*path\b|\bexploitability\b|\bEPSS\b|"
            r"\badversarial\s*exposure\b|\bbreach\s*and\s*attack\s*simulation\b|\bBAS\b",
            re.I,
        ),
        "ve_ctem_talk_mentions",
    ),
]

_POS = re.compile(
    r"\b("
    r"love|loved|great|excellent|impressive|game[\s-]?changer|worth it|"
    r"recommend|recommended|must[\s-]?have|solid|underrated|fire|goated|"
    r"happy|excited|works\s*well|seamless|easy|bullish"
    r")\b",
    re.I,
)
_NEG = re.compile(
    r"\b("
    r"awful|terrible|garbage|trash|hate|hated|useless|overpriced|expensive|"
    r"broken|avoid|disappoint|scam|nightmare|worst|ripoff|rip-off|"
    r"bearish|bankrupt|sunk"
    r")\b",
    re.I,
)
_NAMED = ("Tenable", "Qualys", "Rapid7", "CrowdStrike", "Palo Alto")
_NAMED_COUNT_KEYS = (
    "tenable",
    "qualys",
    "rapid7",
    "crowdstrike",
    "paloalto",
)
_CATEGORY_COUNT_KEYS = ("ctem_talk",)
_NAME_TO_FIELD = {
    "Tenable": "tenable",
    "Qualys": "qualys",
    "Rapid7": "rapid7",
    "CrowdStrike": "crowdstrike",
    "Palo Alto": "paloalto",
    "CTEM talk": "ctem_talk",
}


def _sentiment(text: str) -> float:
    if not text:
        return 0.0
    pos = len(_POS.findall(text))
    neg = len(_NEG.findall(text))
    if pos == 0 and neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def _sentiment_label(score: float) -> str:
    if score >= 0.2:
        return "positive"
    if score <= -0.2:
        return "negative"
    return "mixed"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_attachments() -> dict:
    attachments = (
        _load_json(_ATTACHMENTS) if _ATTACHMENTS.exists() else {"attachments": []}
    )
    try:
        remote = r2lib.get_json("themedata/series_attachments.json")
        if isinstance(remote, dict) and isinstance(remote.get("attachments"), list):
            attachments = remote
    except Exception:
        pass
    return attachments


def _week_ending_sunday(d: date) -> str:
    ahead = 6 - d.weekday()
    return (d + timedelta(days=ahead)).isoformat()


def _hits(text: str) -> list[str]:
    return [name for name, rx, _ in LEXICON if rx.search(text)]


def _blob(post: dict[str, Any]) -> str:
    return " ".join(
        filter(None, [post.get("title") or "", post.get("selftext") or ""])
    )


def _arctic_get(params: dict[str, Any], *, retries: int = 4) -> dict[str, Any]:
    qs = urllib.parse.urlencode(params)
    url = f"{ARCTIC}?{qs}"
    last: Exception | None = None
    for i in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(1.2 * (i + 1))
    assert last is not None
    raise last


def fetch_subreddit(sub: str, *, max_posts: int = 600) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    before: float | None = None
    while len(out) < max_posts:
        params: dict[str, Any] = {
            "subreddit": sub,
            "limit": 100,
            "sort": "desc",
        }
        if before is not None:
            params["before"] = str(int(before))
        try:
            data = _arctic_get(params)
        except urllib.error.HTTPError as e:
            if out and e.code in (422, 429, 502, 503):
                break
            raise
        batch = data.get("data") or []
        if not batch:
            break
        out.extend(batch)
        before = min(
            float(p["created_utc"])
            for p in batch
            if p.get("created_utc") is not None
        )
        time.sleep(0.55)
        if len(batch) < 100:
            break
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for p in out:
        pid = str(p.get("id") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        uniq.append(p)
    return uniq


def _last_closed_week_ending(today: date | None = None) -> date:
    today = today or datetime.now(timezone.utc).date()
    we_today = date.fromisoformat(_week_ending_sunday(today))
    if we_today >= today:
        return we_today - timedelta(days=7)
    return we_today


def _week_window_ts(we: date) -> tuple[float, float]:
    week_start = we - timedelta(days=6)  # Monday
    after_ts = datetime(
        week_start.year, week_start.month, week_start.day, tzinfo=timezone.utc
    ).timestamp()
    before_ts = (
        datetime(we.year, we.month, we.day, 23, 59, 59, tzinfo=timezone.utc).timestamp()
        + 1.0
    )
    return after_ts, before_ts


def fetch_posts_in_window(
    sub: str,
    *,
    after_ts: float,
    before_ts: float,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    before = before_ts
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "subreddit": sub,
            "limit": 100,
            "sort": "desc",
            "after": str(int(after_ts)),
            "before": str(int(before)),
        }
        try:
            data = _arctic_get(params)
        except urllib.error.HTTPError as e:
            if out and e.code in (422, 429, 502, 503):
                break
            raise
        batch = data.get("data") or []
        if not batch:
            break
        out.extend(batch)
        created = [
            float(p["created_utc"])
            for p in batch
            if p.get("created_utc") is not None
        ]
        if not created:
            break
        before = min(created)
        time.sleep(0.35)
        if len(batch) < 100 or before <= after_ts:
            break
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for p in out:
        pid = str(p.get("id") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        uniq.append(p)
    return uniq


def count_posts_in_window(
    sub: str,
    *,
    after_ts: float,
    before_ts: float,
    max_pages: int = 50,
) -> int:
    total = 0
    before = before_ts
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "subreddit": sub,
            "limit": 100,
            "sort": "desc",
            "after": str(int(after_ts)),
            "before": str(int(before)),
        }
        try:
            data = _arctic_get(params)
        except urllib.error.HTTPError as e:
            if total and e.code in (422, 429, 502, 503):
                break
            raise
        batch = data.get("data") or []
        if not batch:
            break
        total += len(batch)
        created = [
            float(p["created_utc"])
            for p in batch
            if p.get("created_utc") is not None
        ]
        if not created:
            break
        before = min(created)
        time.sleep(0.35)
        if len(batch) < 100 or before <= after_ts:
            break
    return total


def backfill_weekly_volume(
    by: dict[str, dict[str, Any]],
    *,
    subs: tuple[str, ...],
    n_weeks: int = 52,
) -> list[str]:
    """Per-week Arctic counts — extends busy-sub volume past the post-budget cliff."""
    notes: list[str] = []
    last_closed = _last_closed_week_ending()
    for sub in subs:
        filled = 0
        for i in range(max(1, int(n_weeks))):
            we = last_closed - timedelta(days=7 * i)
            after_ts, before_ts = _week_window_ts(we)
            try:
                n = count_posts_in_window(sub, after_ts=after_ts, before_ts=before_ts)
            except Exception as e:
                notes.append(f"{sub} volume week {we.isoformat()}: {e}")
                break
            key = f"{sub}|{we.isoformat()}"
            row = by.get(key) or _empty_bucket(we.isoformat(), sub)
            prev_n = int(row.get("total_posts") or 0)
            if n >= prev_n:
                row["total_posts"] = n
            by[key] = row
            filled += 1
        notes.append(
            f"{sub}: weekly volume backfill weeks={filled} through {last_closed.isoformat()}"
        )
    return notes


def backfill_weekly_bodies(
    by: dict[str, dict[str, Any]],
    *,
    subs: tuple[str, ...],
    n_weeks: int = 26,
) -> list[str]:
    """Re-score lexicon from full per-week bodies (fixes CTEM/brand cliffs)."""
    notes: list[str] = []
    last_closed = _last_closed_week_ending()
    for sub in subs:
        filled = 0
        skipped = 0
        failures = 0
        for i in range(max(1, int(n_weeks))):
            we = last_closed - timedelta(days=7 * i)
            key = f"{sub}|{we.isoformat()}"
            prev = by.get(key)
            if prev and prev.get("_week_bodies"):
                skipped += 1
                filled += 1
                continue
            after_ts, before_ts = _week_window_ts(we)
            posts: list[dict[str, Any]] | None = None
            last_err: Exception | None = None
            for attempt in range(4):
                try:
                    posts = fetch_posts_in_window(
                        sub, after_ts=after_ts, before_ts=before_ts
                    )
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(1.5 * (attempt + 1))
            if posts is None:
                failures += 1
                notes.append(f"{sub} body week {we.isoformat()}: {last_err}")
                if failures >= 5:
                    notes.append(
                        f"{sub}: aborting body backfill after {failures} failures"
                    )
                    break
                continue
            weeks = aggregate_weeks(posts, sub=sub)
            row = weeks.get(we.isoformat()) or _empty_bucket(we.isoformat(), sub)
            if prev and int(prev.get("total_posts") or 0) > int(
                row.get("total_posts") or 0
            ):
                row["total_posts"] = int(prev["total_posts"])
            row["_week_bodies"] = True
            by[key] = row
            filled += 1
            print(
                f"  body {sub} {we.isoformat()}: posts={len(posts)} "
                f"ctem={row.get('ctem_talk')}",
                flush=True,
            )
        notes.append(
            f"{sub}: weekly body backfill weeks={filled} "
            f"(skipped_cached={skipped}, failures={failures}) "
            f"through {last_closed.isoformat()}"
        )
    return notes


def _volume_common_start(rows: list[dict[str, Any]]) -> str | None:
    """Earliest week where every watched sub has a volume print (no fake cliff)."""
    first: dict[str, str] = {}
    for r in rows:
        sub = str(r.get("subreddit") or "")
        if sub not in _WATCHED_SUBS:
            continue
        we = str(r.get("week_ending") or "")
        n = int(r.get("total_posts") or 0)
        if not we or we < _SERIES_FROM or n <= 0:
            continue
        if sub not in first or we < first[sub]:
            first[sub] = we
    if len(first) < len(_WATCHED_SUBS):
        return None
    return max(first.values())


def _mention_common_start(rows: list[dict[str, Any]]) -> str | None:
    """Shared start of per-week body lexicons — drop budget-scrape mention cliffs."""
    first: dict[str, str] = {}
    for r in rows:
        sub = str(r.get("subreddit") or "")
        if sub not in _WATCHED_SUBS:
            continue
        if not r.get("_week_bodies"):
            continue
        we = str(r.get("week_ending") or "")
        if not we or we < _SERIES_FROM:
            continue
        if sub not in first or we < first[sub]:
            first[sub] = we
    if len(first) < len(_WATCHED_SUBS):
        return None
    return max(first.values())


def _post_ts(post: dict[str, Any]) -> datetime | None:
    t = post.get("created_utc")
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), tz=timezone.utc)


def _empty_bucket(we: str, sub: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "week_ending": we,
        "subreddit": sub,
        "total_posts": 0,
        "lexicon_hits": 0,
        "named_products": 0,
        "_sent_lexicon": [],
        "_sent_named_products": [],
        "_sent_category": [],
        "digest_candidates": [],
    }
    for k in _NAMED_COUNT_KEYS + _CATEGORY_COUNT_KEYS:
        row[k] = 0
    return row


def aggregate_weeks(
    posts: list[dict[str, Any]],
    *,
    sub: str,
) -> dict[str, dict[str, Any]]:
    weeks: dict[str, dict[str, Any]] = {}
    for p in posts:
        dt = _post_ts(p)
        if not dt:
            continue
        we = _week_ending_sunday(dt.date())
        bucket = weeks.setdefault(we, _empty_bucket(we, sub))
        bucket["total_posts"] += 1
        text = _blob(p)
        tags = _hits(text)
        if not tags:
            continue
        sent = _sentiment(text)
        bucket["lexicon_hits"] += 1
        bucket["_sent_lexicon"].append(sent)
        for tag in tags:
            field = _NAME_TO_FIELD.get(tag)
            if field:
                bucket[field] = int(bucket.get(field) or 0) + 1
        if set(_NAMED) & set(tags):
            bucket["named_products"] += 1
            bucket["_sent_named_products"].append(sent)
        if "CTEM talk" in tags:
            bucket["_sent_category"].append(sent)
        title = (p.get("title") or "").strip()
        if not title:
            continue
        permalink = p.get("permalink") or ""
        url = (
            f"https://www.reddit.com{permalink}"
            if permalink.startswith("/")
            else (permalink or None)
        )
        brands = [t for t in tags if t in _NAMED]
        bucket["digest_candidates"].append(
            {
                "date": dt.date().isoformat(),
                "title": title[:160],
                "score": int(p.get("score") or 0),
                "comments": int(p.get("num_comments") or 0),
                "hits": tags,
                "brands": brands,
                "sentiment": round(sent, 3),
                "sentiment_label": _sentiment_label(sent),
                "url": url,
                "flair": p.get("link_flair_text"),
            }
        )
    for bucket in weeks.values():
        for field, acc in (
            ("lexicon_sentiment", "_sent_lexicon"),
            ("ve_named_sentiment", "_sent_named_products"),
            ("ve_ctem_talk_sentiment", "_sent_category"),
        ):
            vals = bucket.pop(acc, [])
            bucket[field] = (
                round(sum(vals) / len(vals), 4) if vals else None
            )
        cands = bucket.pop("digest_candidates")
        cands.sort(
            key=lambda x: (
                1 if x.get("brands") else 0,
                1 if "CTEM talk" in (x.get("hits") or []) else 0,
                x.get("score") or 0,
                x.get("comments") or 0,
            ),
            reverse=True,
        )
        bucket["digest"] = cands[:8]
    return weeks


def _load_history(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    by: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        key = f"{row.get('subreddit')}|{row.get('week_ending')}"
        by[key] = row
    return by


def _write_history(path: Path, by: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        by.values(),
        key=lambda r: (r.get("subreddit") or "", r.get("week_ending") or ""),
    )
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
        encoding="utf-8",
    )


def _points_from_history(
    rows: list[dict[str, Any]],
    *,
    sub: str,
    field: str,
    start_date: str | None = None,
) -> list[dict[str, float]]:
    by_week: dict[str, list[float]] = {}
    floor = start_date if start_date and start_date > _SERIES_FROM else _SERIES_FROM
    for r in rows:
        if sub != "*" and r.get("subreddit") != sub:
            continue
        we = r.get("week_ending")
        val = r.get(field)
        if not we or val is None or str(we) < floor:
            continue
        by_week.setdefault(str(we), []).append(float(val))
    # Sparse brand/category mention counts: emit only weeks with a hit.
    # Keep full weekly spine for community volume (total_posts) + sentiment.
    sparse_counts = field in _NAMED_COUNT_KEYS + _CATEGORY_COUNT_KEYS
    today = datetime.now(timezone.utc).date().isoformat()
    pts: list[dict[str, float]] = []
    for we, vals in by_week.items():
        if str(we) > today:
            continue
        if "sentiment" in field:
            pts.append({"date": we, "value": round(sum(vals) / len(vals), 4)})
        else:
            total = float(sum(vals))
            if sparse_counts and total <= 0:
                continue
            pts.append({"date": we, "value": total})
    pts.sort(key=lambda p: p["date"])
    return pts


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    out_dir: Path,
    backfill_posts: int,
    history_only: bool = False,
    volume_weeks: int = 0,
    mention_weeks: int = 0,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    history_path = out_dir / _HISTORY
    by = _load_history(history_path)
    errors: list[str] = []
    notes: list[str] = []

    if history_only:
        notes.append("history_only: skipped Arctic Shift fetch")
    else:
        for sub in _WATCHED_SUBS:
            n = max(400, int(backfill_posts))
            try:
                posts = fetch_subreddit(sub, max_posts=n)
                weeks = aggregate_weeks(posts, sub=sub)
                for we, row in weeks.items():
                    key = f"{sub}|{we}"
                    prev = by.get(key)
                    prev_complete = (
                        prev
                        and all(k in prev for k in _NAMED_COUNT_KEYS)
                        and all(k in prev for k in _CATEGORY_COUNT_KEYS)
                    )
                    if (
                        prev_complete
                        and we != _week_ending_sunday(datetime.now(timezone.utc).date())
                        and int(prev.get("total_posts") or 0)
                        > int(row.get("total_posts") or 0)
                    ):
                        continue
                    by[key] = row
                notes.append(
                    f"{sub}: fetched {len(posts)} posts, {len(weeks)} week buckets"
                )
            except Exception as e:
                errors.append(f"{sub}: {e}")

    if mention_weeks > 0:
        try:
            notes.extend(
                backfill_weekly_bodies(
                    by, subs=_WATCHED_SUBS, n_weeks=max(4, int(mention_weeks))
                )
            )
        except Exception as e:
            errors.append(f"weekly_body_backfill: {e}")
    if volume_weeks > 0:
        try:
            notes.extend(
                backfill_weekly_volume(
                    by, subs=_VOLUME_SUBS, n_weeks=max(4, int(volume_weeks))
                )
            )
        except Exception as e:
            errors.append(f"weekly_volume_backfill: {e}")

    _write_history(history_path, by)
    rows = list(by.values())

    volume_keys = {
        "ve_cybersecurity_sub_posts",
        "ve_netsec_sub_posts",
        "ve_sysadmin_sub_posts",
    }
    mention_keys = {
        "ve_tenable_mentions",
        "ve_qualys_mentions",
        "ve_rapid7_mentions",
        "ve_crowdstrike_mentions",
        "ve_paloalto_mentions",
        "ve_ctem_talk_mentions",
        "ve_ctem_talk_sentiment",
    }
    # Always align stacks to dense history when present (not only on backfill runs).
    volume_start = _volume_common_start(rows)
    if volume_start:
        notes.append(
            f"community volume truncated to shared dense window start={volume_start}"
        )
    mention_start = _mention_common_start(rows)
    if mention_start:
        notes.append(
            f"CTEM/brand/sentiment truncated to week-body start={mention_start}"
        )
    elif mention_weeks > 0:
        notes.append(
            "mentions: no shared _week_bodies window yet — charts may still cliff"
        )

    series_map = {
        "ve_tenable_mentions": ("*", "tenable"),
        "ve_qualys_mentions": ("*", "qualys"),
        "ve_rapid7_mentions": ("*", "rapid7"),
        "ve_crowdstrike_mentions": ("*", "crowdstrike"),
        "ve_paloalto_mentions": ("*", "paloalto"),
        "ve_ctem_talk_mentions": ("*", "ctem_talk"),
        "ve_cybersecurity_sub_posts": ("cybersecurity", "total_posts"),
        "ve_netsec_sub_posts": ("netsec", "total_posts"),
        "ve_sysadmin_sub_posts": ("sysadmin", "total_posts"),
        "ve_ctem_talk_sentiment": ("*", "ve_ctem_talk_sentiment"),
    }
    series_out: dict[str, Any] = {}
    latest_date = ""
    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key or key not in series_map:
            continue
        sub, field = series_map[key]
        start = None
        if key in volume_keys:
            start = volume_start
        elif key in mention_keys:
            start = mention_start
        pts = _points_from_history(
            rows, sub=sub, field=field, start_date=start
        )
        if not pts:
            errors.append(f"{key}: no points")
            continue
        series_out[key] = ps.series_shell(meta, points=pts, fetched_at=fetched_at)
        latest = series_out[key]["raw"]["latest"]
        if latest and latest.get("date"):
            latest_date = max(latest_date, str(latest["date"]))

    digest_weeks: list[dict[str, Any]] = []
    week_ends = sorted(
        {r.get("week_ending") for r in rows if r.get("week_ending")}, reverse=True
    )[:4]
    for we in week_ends:
        entries = []
        for r in rows:
            if r.get("week_ending") != we:
                continue
            for item in r.get("digest") or []:
                entries.append({**item, "subreddit": r.get("subreddit")})
        entries.sort(
            key=lambda x: (x.get("score") or 0, x.get("comments") or 0), reverse=True
        )
        digest_weeks.append(
            {
                "week_ending": we,
                "top_posts": entries[:10],
                "counts": {
                    r.get("subreddit"): {
                        "lexicon_hits": r.get("lexicon_hits"),
                        "total_posts": r.get("total_posts"),
                        "named_products": r.get("named_products"),
                        "ctem_talk": r.get("ctem_talk"),
                    }
                    for r in rows
                    if r.get("week_ending") == we
                },
            }
        )

    if not series_out and errors:
        notes.append(
            "soft-fail: Arctic Shift unavailable — publishing empty shell; "
            "retry later (history appends when scrape recovers)"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": (
            "Arctic Shift — r/cybersecurity + r/netsec + r/sysadmin; "
            "CTEM/EASM/KEV category + Tenable/Qualys/Rapid7/CRWD/PANW names"
        ),
        "fetched_at": fetched_at,
        "as_of": latest_date or fetched_at[:10],
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "digest": digest_weeks,
        "errors": errors,
        "notes": notes,
        "provenance_notes": (
            "Best-effort scrape via Arctic Shift; not official Reddit API. "
            "Weekly mention counts + thin titles/links only. Not in theme scores."
        ),
    }


def _write_local(out_dir: Path, payload: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "catalog.json").write_text(
        json.dumps(payload["catalog"], indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "attachments.json").write_text(
        json.dumps(payload["attachments"], indent=2) + "\n", encoding="utf-8"
    )
    raw_dir = out_dir / "raw"
    der_dir = out_dir / "derived"
    raw_dir.mkdir(exist_ok=True)
    der_dir.mkdir(exist_ok=True)
    for key, ser in (payload.get("series") or {}).items():
        (raw_dir / f"{key}.json").write_text(
            json.dumps(
                {
                    "key": key,
                    "label": ser.get("label"),
                    "display_name": ser.get("display_name"),
                    "frequency": ser.get("frequency"),
                    "unit": ser.get("unit"),
                    "points": ser["raw"]["points"],
                    "latest": ser["raw"]["latest"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (der_dir / f"{key}.json").write_text(
            json.dumps({"key": key, "derived": ser.get("derived")}, indent=2) + "\n",
            encoding="utf-8",
        )
    (out_dir / "digest.json").write_text(
        json.dumps({"digest": payload.get("digest") or []}, indent=2) + "\n",
        encoding="utf-8",
    )


def _publish_r2(payload: dict) -> list[str]:
    uris = [
        r2lib.put_json(f"{R2_PREFIX}/latest.json", payload),
        r2lib.put_json(f"{R2_PREFIX}/catalog.json", payload["catalog"]),
        r2lib.put_json(f"{R2_PREFIX}/attachments.json", payload["attachments"]),
        r2lib.put_json(
            f"{R2_PREFIX}/digest.json", {"digest": payload.get("digest") or []}
        ),
    ]
    for key, ser in (payload.get("series") or {}).items():
        uris.append(
            r2lib.put_json(
                f"{R2_PREFIX}/raw/{key}.json",
                {
                    "key": key,
                    "label": ser.get("label"),
                    "display_name": ser.get("display_name"),
                    "frequency": ser.get("frequency"),
                    "unit": ser.get("unit"),
                    "points": ser["raw"]["points"],
                    "latest": ser["raw"]["latest"],
                },
            )
        )
        uris.append(
            r2lib.put_json(
                f"{R2_PREFIX}/derived/{key}.json",
                {"key": key, "derived": ser.get("derived")},
            )
        )
    slim = {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "fetched_at": payload.get("fetched_at"),
        "as_of": payload.get("as_of"),
        "metrics": {
            key: {"raw_latest": ser["raw"]["latest"]}
            for key, ser in (payload.get("series") or {}).items()
        },
    }
    uris.append(r2lib.put_json(f"{R2_PREFIX}/metrics_latest.json", slim))
    return uris


def run(
    *,
    out_dir: Path,
    dry_run: bool = False,
    skip_r2: bool = False,
    backfill_posts: int = 2500,
    history_only: bool = False,
    volume_weeks: int = 0,
    mention_weeks: int = 0,
) -> dict:
    load_env()
    catalog = _load_json(_CATALOG)
    attachments = _load_attachments()
    payload = build_pack(
        catalog=catalog,
        attachments=attachments,
        out_dir=out_dir,
        backfill_posts=backfill_posts,
        history_only=history_only,
        volume_weeks=volume_weeks,
        mention_weeks=mention_weeks,
    )
    if dry_run:
        return payload
    _write_local(out_dir, payload)
    r2_uris: list[str] = []
    if not skip_r2:
        r2_uris.extend(_publish_r2(payload))
        try:
            r2_uris.append(
                r2lib.put_json(
                    "themedata/series_attachments.json", payload["attachments"]
                )
            )
        except Exception:
            pass
    payload["_r2_uris"] = r2_uris
    return payload


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-r2", action="store_true")
    p.add_argument("--history-only", action="store_true")
    p.add_argument("--backfill-posts", type=int, default=2500)
    p.add_argument(
        "--volume-weeks",
        type=int,
        default=0,
        help="Per-week Arctic counts for busy subs (cybersecurity/sysadmin)",
    )
    p.add_argument(
        "--mention-weeks",
        type=int,
        default=0,
        help="Per-week full-body lexicon rescore (fixes CTEM/brand cliffs)",
    )
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            backfill_posts=args.backfill_posts,
            history_only=args.history_only,
            volume_weeks=args.volume_weeks,
            mention_weeks=args.mention_weeks,
        )
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1

    summary = {
        "pack": PACK,
        "as_of": payload.get("as_of"),
        "series": {
            k: (payload["series"][k]["raw"]["latest"])
            for k in (payload.get("series") or {})
        },
        "digest_weeks": len(payload.get("digest") or []),
        "errors": payload.get("errors") or [],
        "notes": payload.get("notes") or [],
        "r2": payload.get("_r2_uris") or [],
    }
    print(json.dumps(summary, indent=2))
    if payload.get("errors"):
        print(f"\nWARN: {len(payload['errors'])} issues", file=sys.stderr)
        for err in payload["errors"]:
            print(f"  - {err}", file=sys.stderr)
    if not args.dry_run:
        print(f"\nwrote {args.out_dir}/latest.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
