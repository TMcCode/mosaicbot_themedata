#!/usr/bin/env python3
"""Political Spend Reddit forums sidecar; publish to R2.

Theme: Political Spend '26: Ad Agencies & Polling.
Named agency brand stack + political-ads/polling category % + community volume
+ combined agency sentiment + thin digest
(r/PoliticalScience, r/Advertising, r/marketing).
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
from collections import defaultdict
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
PACK = "political_spend_forums"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+political_spend_forums; arctic-shift read-only)"
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "political_spend_forums_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_HISTORY = "weekly_history.jsonl"
# Publish from first dense overlap across watched AI/software subs.
_SERIES_FROM = "2025-01-01"

# Lexicon → series_key (posts can hit multiple).
LEXICON: list[tuple[str, re.Pattern[str], str]] = [
    (
        "Stagwell",
        re.compile(
            r"\bstagwell\b|the\s*media\s*machine|stagwell\s*search\+?|"
            r"\bstagwell\s*curate\b",
            re.I,
        ),
        "stagwell_mentions",
    ),
    (
        "Omnicom",
        re.compile(r"\bomnicom\b|\bomni\s*platform\b|\bacxiom\b", re.I),
        "omnicom_mentions",
    ),
    (
        "Publicis",
        re.compile(
            r"\bpublicis\b|\bmarcel\s*ai\b|\bepsilon\b|publicis\s*groupe",
            re.I,
        ),
        "publicis_mentions",
    ),
    (
        "WPP",
        re.compile(
            r"\bwpp\b|\bgroupm\b|\bogilvy\b|\bvml\b|\bburson\b|"
            r"wpp\s*open|wpp\s*media",
            re.I,
        ),
        "wpp_mentions",
    ),
    (
        "Ipsos",
        re.compile(r"\bipsos\b|ipsos\.digital", re.I),
        "ipsos_mentions",
    ),
    (
        "political_ads",
        re.compile(
            r"political\s*ads?|political\s*advertis|"
            r"campaign\s*ads?|election\s*ads?|"
            r"midterm|super\s*pac|ctv\s*political|"
            r"election\s*polling|\bpollster\b|public\s*opinion\s*poll|"
            r"political\s*spend|ad\s*spend.*election",
            re.I,
        ),
        "software",
    ),
]

SOFTWARE_TAGS = {name for name, _, _ in LEXICON}

_POS = re.compile(
    r"\b("
    r"love|loved|great|excellent|recommend|recommended|"
    r"worth it|powerful|useful|game[-\s]?changer|impressive|"
    r"best|solid|loyal|fire|goated|underrated|productive|"
    r"effective|winning|surge|record\s*spend"
    r")\b",
    re.I,
)
_NEG = re.compile(
    r"\b("
    r"awful|terrible|garbage|trash|hate|hated|scam|refund|"
    r"bug|buggy|broken|unreliable|hallucin|"
    r"overpriced|expensive|avoid|disappoint|never\s*again|worst|"
    r"nightmare|rip\-?off|layoff|layoffs|bloat|"
    r"deepfake|misinformation|dark\s*money|astroturf"
    r")\b",
    re.I,
)
_BRANDS = (
    "Stagwell",
    "Omnicom",
    "Publicis",
    "WPP",
    "Ipsos",
)
_NAMED = _BRANDS
_NAMED_COUNT_KEYS = (
    "stagwell",
    "omnicom",
    "publicis",
    "wpp",
    "ipsos",
)
_TOPIC_COUNT_KEYS = ("political_ads_category",)
# Agency brands: PoliticalScience + Advertising (marketing is too broad/noisy).
_BRAND_LEXICON_SUBS = frozenset({"PoliticalScience", "Advertising"})
_BRAND_WINDOW_SUBS = ("PoliticalScience", "Advertising")
_COMMUNITY_SUBS = ("PoliticalScience", "Advertising", "marketing")
_COMMUNITY_WINDOW_SUBS = ("PoliticalScience", "Advertising")
_COMMUNITY_SERIES_KEYS = frozenset(
    {
        "politicalscience_community_posts",
        "advertising_community_posts",
        "marketing_community_posts",
    }
)
_BRAND_SERIES_KEYS = frozenset(
    {
        "stagwell_mentions",
        "omnicom_mentions",
        "publicis_mentions",
        "wpp_mentions",
        "ipsos_mentions",
        "agency_named_brand_mentions",
        "political_ads_category_mentions",
        "agency_brand_sentiment",
    }
)


def _sentiment(text: str) -> float:
    """Return polarity in [-1, 1] from title+body lexicon hits."""
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
    """ISO-ish week bucket: Sunday end date (YYYY-MM-DD)."""
    # weekday: Mon=0 … Sun=6 → days until Sunday
    ahead = 6 - d.weekday()
    end = d + timedelta(days=ahead)
    return end.isoformat()


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
        data = _arctic_get(params)
        batch = data.get("data") or []
        if not batch:
            break
        out.extend(batch)
        before = min(
            float(p["created_utc"])
            for p in batch
            if p.get("created_utc") is not None
        )
        time.sleep(0.45)
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


def _post_ts(post: dict[str, Any]) -> datetime | None:
    t = post.get("created_utc")
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), tz=timezone.utc)


def _empty_bucket(we: str, sub: str) -> dict[str, Any]:
    bucket: dict[str, Any] = {
        "week_ending": we,
        "subreddit": sub,
        "total_posts": 0,
        "lexicon_hits": 0,
        "named_products": 0,
        "political_ads_category": 0,
        "_sent_lexicon": [],
        "_sent_named_products": [],
        "_sent_political_ads_category": [],
        "digest_candidates": [],
    }
    for k in _NAMED_COUNT_KEYS:
        bucket[k] = 0
        bucket[f"_sent_{k}"] = []
    return bucket


def aggregate_weeks(
    posts: list[dict[str, Any]],
    *,
    sub: str,
) -> dict[str, dict[str, Any]]:
    weeks: dict[str, dict[str, Any]] = {}
    tag_to_field = {
        "Stagwell": "stagwell",
        "Omnicom": "omnicom",
        "Publicis": "publicis",
        "WPP": "wpp",
        "Ipsos": "ipsos",
        "political_ads": "political_ads_category",
    }
    count_brands = sub in _BRAND_LEXICON_SUBS
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
        fields_hit: set[str] = set()
        for tag, field in tag_to_field.items():
            if tag not in tags:
                continue
            # Category talk OK on all watched subs; named brands only on brand subs.
            if field != "political_ads_category" and not count_brands:
                continue
            fields_hit.add(field)
        if not fields_hit:
            continue
        bucket["lexicon_hits"] += 1
        bucket["_sent_lexicon"].append(sent)
        for field in fields_hit:
            bucket[field] += 1
            bucket[f"_sent_{field}"].append(sent)
        if count_brands and (set(_NAMED) & set(tags)):
            bucket["named_products"] += 1
            bucket["_sent_named_products"].append(sent)
        title = (p.get("title") or "").strip()
        if not title:
            continue
        permalink = p.get("permalink") or ""
        url = (
            f"https://www.reddit.com{permalink}"
            if permalink.startswith("/")
            else (permalink or None)
        )
        brands = [t for t in tags if t in _BRANDS]
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
        sent_fields = [
            ("lexicon_sentiment", "_sent_lexicon"),
            ("agency_brand_sentiment", "_sent_named_products"),
            ("political_ads_category_sentiment", "_sent_political_ads_category"),
        ]
        for k in _NAMED_COUNT_KEYS:
            sent_fields.append((f"{k}_sentiment", f"_sent_{k}"))
        for field, acc in sent_fields:
            vals = bucket.pop(acc, [])
            bucket[field] = (
                round(sum(vals) / len(vals), 4) if vals else None
            )
        cands = bucket.pop("digest_candidates")
        cands.sort(
            key=lambda x: (
                1 if x.get("brands") else 0,
                abs(float(x.get("sentiment") or 0)),
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


_NAMED_SENT_WEIGHTS = (
    ("stagwell", "stagwell_sentiment"),
    ("omnicom", "omnicom_sentiment"),
    ("publicis", "publicis_sentiment"),
    ("wpp", "wpp_sentiment"),
    ("ipsos", "ipsos_sentiment"),
)


def _ensure_brand_sentiment(row: dict[str, Any]) -> None:
    """Fill agency_brand_sentiment from unique-post mean, or brand-weighted fallback."""
    if row.get("agency_brand_sentiment") is not None:
        return
    num = den = 0.0
    for count_k, sent_k in _NAMED_SENT_WEIGHTS:
        c = float(row.get(count_k) or 0)
        s = row.get(sent_k)
        if c > 0 and s is not None:
            num += c * float(s)
            den += c
    if den > 0:
        row["agency_brand_sentiment"] = round(num / den, 4)


def _points_from_history(
    rows: list[dict[str, Any]], *, sub: str, field: str
) -> list[dict[str, float]]:
    # sub="*" → sum numeric fields across subs per week (skip None sentiment means).
    by_week: dict[str, list[float]] = {}
    for r in rows:
        if sub != "*" and r.get("subreddit") != sub:
            continue
        we = r.get("week_ending")
        val = r.get(field)
        if not we or val is None or str(we) < _SERIES_FROM:
            continue
        by_week.setdefault(str(we), []).append(float(val))
    pts: list[dict[str, float]] = []
    for we, vals in by_week.items():
        if field.endswith("_sentiment") or field == "agency_brand_sentiment":
            pts.append({"date": we, "value": round(sum(vals) / len(vals), 4)})
        else:
            pts.append({"date": we, "value": float(sum(vals))})
    pts.sort(key=lambda p: p["date"])
    return pts


def _lexicon_hit_count(row: dict[str, Any]) -> float:
    hits = row.get("lexicon_hits")
    if hits is None:
        hits = row.get("software_mentions")  # legacy history field
    return float(hits or 0)


def _lexicon_pct_points(
    rows: list[dict[str, Any]], *, sub: str
) -> list[dict[str, float]]:
    """Share of subreddit posts that hit the chicken/QSR lexicon (0–100)."""
    by_week: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        if r.get("subreddit") != sub:
            continue
        we = r.get("week_ending")
        total = r.get("total_posts")
        if not we or total is None or str(we) < _SERIES_FROM:
            continue
        by_week.setdefault(str(we), []).append(
            (_lexicon_hit_count(r), float(total))
        )
    pts: list[dict[str, float]] = []
    for we, pairs in by_week.items():
        hits = sum(h for h, _ in pairs)
        total = sum(t for _, t in pairs)
        if total <= 0:
            continue
        pts.append({"date": we, "value": round(100.0 * hits / total, 2)})
    pts.sort(key=lambda p: p["date"])
    return pts


def _weekly_total_posts(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Sum total posts across all watched subs per week."""
    by_week: dict[str, float] = {}
    for r in rows:
        we = r.get("week_ending")
        total = r.get("total_posts")
        if not we or total is None or str(we) < _SERIES_FROM:
            continue
        by_week[str(we)] = by_week.get(str(we), 0.0) + float(total)
    return by_week


def _share_of_total_points(
    topic_points: list[dict[str, float]],
    totals: dict[str, float],
) -> list[dict[str, float]]:
    """Topic posts as % of all watched-sub posts that week."""
    out: list[dict[str, float]] = []
    for p in topic_points:
        den = totals.get(str(p["date"])) or 0.0
        if den <= 0:
            continue
        out.append(
            {
                "date": p["date"],
                "value": round(100.0 * float(p["value"]) / den, 2),
            }
        )
    return out


def _first_overlap_week(
    rows: list[dict[str, Any]],
    subs: tuple[str, ...] | frozenset[str],
) -> str | None:
    """Earliest week_ending present in every listed sub (honest chart window)."""
    want = tuple(subs)
    by_sub: dict[str, set[str]] = {s: set() for s in want}
    for r in rows:
        sub = r.get("subreddit")
        we = r.get("week_ending")
        if sub not in by_sub or not we:
            continue
        we_s = str(we)
        if we_s < _SERIES_FROM:
            continue
        by_sub[sub].add(we_s)
    if any(not weeks for weeks in by_sub.values()):
        return None
    common = set.intersection(*(by_sub[s] for s in want))
    return min(common) if common else None


def _first_dual_community_week(rows: list[dict[str, Any]]) -> str | None:
    """Earliest week with PoliticalScience+Advertising coverage."""
    return _first_overlap_week(rows, _COMMUNITY_WINDOW_SUBS)


def _first_brand_overlap_week(rows: list[dict[str, Any]]) -> str | None:
    """Earliest week with PoliticalScience+Advertising brand coverage."""
    return _first_overlap_week(rows, _BRAND_WINDOW_SUBS)


def _attach_share_pct(
    series_obj: dict[str, Any],
    share_points: list[dict[str, float]],
) -> None:
    from lib import transforms as tx

    series_obj.setdefault("derived", {})["share_pct"] = {
        "unit": "pct",
        "points": share_points,
        "latest": tx.latest(share_points),
    }


def _prune_pre_series_history(by: dict[str, dict[str, Any]]) -> int:
    """Drop quiet-sub archive weeks before _SERIES_FROM so charts stay honest."""
    drop = [k for k, r in by.items() if str(r.get("week_ending") or "") < _SERIES_FROM]
    for k in drop:
        del by[k]
    return len(drop)


def build_pack(
    *,
    catalog: dict,
    attachments: dict,
    out_dir: Path,
    backfill_posts: int,
    history_only: bool = False,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    history_path = out_dir / _HISTORY
    by = _load_history(history_path)
    errors: list[str] = []
    notes: list[str] = []

    if history_only:
        notes.append("history_only: skipped Arctic Shift fetch")
    else:
        for sub, n in (
            ("PoliticalScience", max(8000, backfill_posts)),
            ("Advertising", max(8000, backfill_posts)),
            ("marketing", max(8000, backfill_posts)),
        ):
            try:
                posts = fetch_subreddit(sub, max_posts=n)
                weeks = aggregate_weeks(posts, sub=sub)
                for we, row in weeks.items():
                    key = f"{sub}|{we}"
                    prev = by.get(key)
                    # Always refresh current week; else keep denser prior scrape
                    # (must include current named-brand keys or lexicon upgrades stall).
                    prev_complete = (
                        prev
                        and all(k in prev for k in _NAMED_COUNT_KEYS)
                        and all(k in prev for k in _TOPIC_COUNT_KEYS)
                    )
                    if (
                        prev_complete
                        and we != _week_ending_sunday(datetime.now(timezone.utc).date())
                        and int(prev.get("total_posts") or 0)
                        > int(row.get("total_posts") or 0)
                        and prev.get("agency_brand_sentiment") is not None
                    ):
                        continue
                    by[key] = row
                notes.append(
                    f"{sub}: fetched {len(posts)} posts, {len(weeks)} week buckets"
                )
            except Exception as e:
                errors.append(f"{sub}: {e}")

    pruned = _prune_pre_series_history(by)
    if pruned:
        notes.append(
            f"pruned {pruned} week rows before {_SERIES_FROM} "
            "(quiet-sub Arctic Shift tail, not continuous history)"
        )
    for row in by.values():
        _ensure_brand_sentiment(row)
    _write_history(history_path, by)
    rows = list(by.values())

    series_map = {
        "stagwell_mentions": ("*", "stagwell"),
        "omnicom_mentions": ("*", "omnicom"),
        "publicis_mentions": ("*", "publicis"),
        "wpp_mentions": ("*", "wpp"),
        "ipsos_mentions": ("*", "ipsos"),
        "agency_named_brand_mentions": ("*", "named_products"),
        "politicalscience_community_posts": ("PoliticalScience", "total_posts"),
        "advertising_community_posts": ("Advertising", "total_posts"),
        "marketing_community_posts": ("marketing", "total_posts"),
        "agency_brand_sentiment": ("*", "agency_brand_sentiment"),
    }
    ratio_keys: dict[str, str] = {}
    topic_pct_fields = {
        "political_ads_category_mentions": "political_ads_category",
    }

    series_out: dict[str, Any] = {}
    latest_date = ""
    post_totals = _weekly_total_posts(rows)
    community_from = _first_dual_community_week(rows)
    brand_from = _first_brand_overlap_week(rows)
    if community_from:
        notes.append(
            f"community stack aligned from {community_from} "
            f"(r/PoliticalScience + r/Advertising window; "
            f"marketing layer fills in when scraped)"
        )
    if brand_from:
        notes.append(
            f"brand/category/sentiment charts aligned from {brand_from} "
            f"(r/PoliticalScience + r/Advertising window)"
        )
    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        if key in ratio_keys:
            pts = _lexicon_pct_points(rows, sub=ratio_keys[key])
        elif key in topic_pct_fields:
            counts = _points_from_history(
                rows, sub="*", field=topic_pct_fields[key]
            )
            pts = _share_of_total_points(counts, post_totals)
        elif key in series_map:
            sub, field = series_map[key]
            pts = _points_from_history(rows, sub=sub, field=field)
        else:
            continue
        if key in _COMMUNITY_SERIES_KEYS and community_from:
            pts = [p for p in pts if str(p.get("date") or "") >= community_from]
        if key in _BRAND_SERIES_KEYS and brand_from:
            pts = [p for p in pts if str(p.get("date") or "") >= brand_from]
        if not pts:
            errors.append(f"{key}: no points")
            continue
        series_out[key] = ps.series_shell(meta, points=pts, fetched_at=fetched_at)
        latest = series_out[key]["raw"]["latest"]
        if latest and latest.get("date"):
            latest_date = max(latest_date, str(latest["date"]))

    # Thin digest: last 4 complete-ish weeks across both subs
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
                        "political_ads_category": r.get("political_ads_category"),
                        "agency_brand_sentiment": r.get("agency_brand_sentiment"),
                    }
                    for r in rows
                    if r.get("week_ending") == we
                },
            }
        )

    if not series_out and errors:
        raise RuntimeError("no series built: " + "; ".join(errors))

    return {
        "schema_version": SCHEMA_VERSION,
        "pack": PACK,
        "source": "Arctic Shift (reddit archive) — r/PoliticalScience + r/Advertising + r/marketing",
        "fetched_at": fetched_at,
        "as_of": latest_date,
        "catalog": catalog,
        "attachments": attachments,
        "series": series_out,
        "digest": digest_weeks,
        "errors": errors,
        "notes": notes,
        "provenance_notes": (
            "Best-effort scrape via Arctic Shift; not official Reddit API. "
            "Stores weekly mention counts + thin titles/links only (no full post bodies). "
            "Not used in theme backdrop scores."
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
        r2lib.put_json(f"{R2_PREFIX}/digest.json", {"digest": payload.get("digest") or []}),
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
            key: {
                "raw_latest": ser["raw"]["latest"],
                "derived_latest": {
                    name: meta.get("latest")
                    for name, meta in (ser.get("derived") or {}).items()
                },
            }
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
    backfill_posts: int = 30000,
    history_only: bool = False,
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
    p.add_argument(
        "--history-only",
        action="store_true",
        help="Rebuild/publish from weekly_history.jsonl without Arctic Shift fetch",
    )
    p.add_argument(
        "--backfill-posts",
        type=int,
        default=30000,
        help="Max posts to pull per subreddit on this run (builds/refreshes weekly history)",
    )
    args = p.parse_args(argv)
    try:
        payload = run(
            out_dir=args.out_dir,
            dry_run=args.dry_run,
            skip_r2=args.skip_r2,
            backfill_posts=args.backfill_posts,
            history_only=args.history_only,
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
