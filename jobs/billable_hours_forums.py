#!/usr/bin/env python3
"""Billable hours / IT services pyramid forums sidecar — Arctic Shift weekly mention counts + thin digest.

Pack: billable_hours_forums. Theme context for BS Jobs '26: Billable Hours Pyramid.
Not in backdrop scores. Soft-fails if Arctic Shift is down.
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
PACK = "billable_hours_forums"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+billable_hours_forums; arctic-shift read-only)"
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "billable_hours_forums_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_HISTORY = "weekly_history.jsonl"
# Publish from first week all three subs overlap so stacked community charts
# don't cliff when layers appear. Re-set after first scrape once history exists.
# Busy cscareerquestions only reaches ~Aug 2026 at 2.5k posts; quieter
# consulting stretches further. Publish from first week all three overlap.
_SERIES_FROM = "2026-08-30"

_WATCHED_SUBS = ("consulting", "cscareerquestions", "ITCareerQuestions")

# Lexicon tag names (posts can hit multiple).
LEXICON: list[tuple[str, re.Pattern[str], str]] = [
    ("Accenture", re.compile(r"\baccenture\b", re.I), "accenture_mentions"),
    ("Cognizant", re.compile(r"\bcognizant\b", re.I), "cognizant_mentions"),
    ("Infosys", re.compile(r"\binfosys\b", re.I), "infosys_mentions"),
    ("EPAM", re.compile(r"\bepam\b", re.I), "epam_mentions"),
    (
        "Booz Allen",
        re.compile(r"booz[\s-]?allen|\bbooz\b|\bBAH\b", re.I),
        "booz_mentions",
    ),
    (
        "Billable hours",
        re.compile(
            r"\bbillable\b|\butilization\b|\bon the bench\b|\bbench time\b|"
            r"\bT\s*&\s*M\b|time and materials|\bstaffing pyramid\b|"
            r"\bpartner track\b|\bup or out\b|"
            r"campus hiring freeze|hiring freeze|\bchargeable hours\b|"
            r"utilization rate|\bpyramid model\b|\bdelivery center\b|"
            r"\boffshore\b|\bnearshore\b|\bbody shop\b|\bSOW\b|"
            r"statement of work|\bengagement manager\b|\bMD promotion\b|"
            r"\banalyst grind\b|\bbillable target\b",
            re.I,
        ),
        "billable_hours_talk_mentions",
    ),
    (
        "AI junior displacement",
        re.compile(
            r"AI replacing|replaced by AI|\bcopilot\b|\bvibe coding\b|\bvibecoding\b|"
            r"junior.*layoff|layoff.*junior|entry[\s-]?level freeze|"
            r"entry[\s-]?level hiring freeze|automated QA|"
            r"AI taking jobs|no longer hiring juniors|"
            r"junior dev.*dead|GPT.*(?:job|replace)|codegen|"
            r"\bdevin\b|AI.*entry[\s-]?level",
            re.I,
        ),
        "ai_junior_displacement_mentions",
    ),
]

# Lightweight product-talk polarity (not a general NLP model).
_POS = re.compile(
    r"\b("
    r"love|loved|great|excellent|impressive|game[\s-]?changer|worth it|switched to|"
    r"recommend|recommended|must[\s-]?have|accurate|helpful|faster|saves? time|"
    r"works well|working well|solid|underrated|best in class|prefer|preferred|"
    r"easy to use|intuitive|reliable|finally|killer|fire|goated|indispensable|"
    r"worth the (?:money|price|cost)|good deal|quality"
    r")\b",
    re.I,
)
_NEG = re.compile(
    r"\b("
    r"awful|terrible|garbage|trash|hate|hated|useless|overpriced|expensive|slow|"
    r"buggy|broken|burnout|toxic|miserable|soul[\s-]?crushing|"
    r"don't buy|do not buy|avoid|disappoint|scam|nightmare|worst|"
    r"worse than|switched (away|off|from)|cancelled|canceled|churn|"
    r"ripoff|rip-off|bloat|clunky|unusable|"
    r"layoff|laid off|pip\b|performance improvement|"
    r"utilization (?:is )?(?:bad|low|terrible)|on the bench forever"
    r")\b",
    re.I,
)
_BRANDS = (
    "Accenture",
    "Cognizant",
    "Infosys",
    "EPAM",
    "Booz Allen",
    "Billable hours",
    "AI junior displacement",
)
_NAMED = ("Accenture", "Cognizant", "Infosys", "EPAM", "Booz Allen")
_NAMED_COUNT_KEYS = (
    "accenture",
    "cognizant",
    "infosys",
    "epam",
    "booz",
)
_CATEGORY_COUNT_KEYS = ("billable_hours_category", "ai_junior_category")


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
    return {
        "week_ending": we,
        "subreddit": sub,
        "total_posts": 0,
        "lexicon_hits": 0,
        "accenture": 0,
        "cognizant": 0,
        "infosys": 0,
        "epam": 0,
        "booz": 0,
        "named_products": 0,
        "billable_hours_category": 0,
        "ai_junior_category": 0,
        "_sent_lexicon": [],
        "_sent_accenture": [],
        "_sent_cognizant": [],
        "_sent_infosys": [],
        "_sent_epam": [],
        "_sent_booz": [],
        "_sent_named_products": [],
        "_sent_billable_hours_category": [],
        "_sent_ai_junior_category": [],
        "digest_candidates": [],
    }


def aggregate_weeks(
    posts: list[dict[str, Any]],
    *,
    sub: str,
) -> dict[str, dict[str, Any]]:
    """week_ending -> counts + brand sentiment means + digest candidates."""
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
        if "Accenture" in tags:
            bucket["accenture"] += 1
            bucket["_sent_accenture"].append(sent)
        if "Cognizant" in tags:
            bucket["cognizant"] += 1
            bucket["_sent_cognizant"].append(sent)
        if "Infosys" in tags:
            bucket["infosys"] += 1
            bucket["_sent_infosys"].append(sent)
        if "EPAM" in tags:
            bucket["epam"] += 1
            bucket["_sent_epam"].append(sent)
        if "Booz Allen" in tags:
            bucket["booz"] += 1
            bucket["_sent_booz"].append(sent)
        if set(_NAMED) & set(tags):
            bucket["named_products"] += 1
            bucket["_sent_named_products"].append(sent)
        if "Billable hours" in tags:
            bucket["billable_hours_category"] += 1
            bucket["_sent_billable_hours_category"].append(sent)
        if "AI junior displacement" in tags:
            bucket["ai_junior_category"] += 1
            bucket["_sent_ai_junior_category"].append(sent)
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
        for field, acc in (
            ("lexicon_sentiment", "_sent_lexicon"),
            ("accenture_sentiment", "_sent_accenture"),
            ("cognizant_sentiment", "_sent_cognizant"),
            ("infosys_sentiment", "_sent_infosys"),
            ("epam_sentiment", "_sent_epam"),
            ("booz_sentiment", "_sent_booz"),
            ("billable_tools_sentiment", "_sent_named_products"),
            ("ai_junior_displacement_sentiment", "_sent_ai_junior_category"),
        ):
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
    ("accenture", "accenture_sentiment"),
    ("cognizant", "cognizant_sentiment"),
    ("infosys", "infosys_sentiment"),
    ("epam", "epam_sentiment"),
    ("booz", "booz_sentiment"),
)


def _ensure_billable_tools_sentiment(row: dict[str, Any]) -> None:
    """Fill billable_tools_sentiment from unique-post mean, or brand-weighted fallback."""
    if row.get("billable_tools_sentiment") is not None:
        return
    num = den = 0.0
    for count_k, sent_k in _NAMED_SENT_WEIGHTS:
        c = float(row.get(count_k) or 0)
        s = row.get(sent_k)
        if c > 0 and s is not None:
            num += c * float(s)
            den += c
    if den > 0:
        row["billable_tools_sentiment"] = round(num / den, 4)


def _points_from_history(
    rows: list[dict[str, Any]], *, sub: str, field: str
) -> list[dict[str, float]]:
    # sub="*" → sum numeric fields across subs per week (average sentiment means).
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
        if field.endswith("_sentiment") or field == "billable_tools_sentiment":
            pts.append({"date": we, "value": round(sum(vals) / len(vals), 4)})
        else:
            pts.append({"date": we, "value": float(sum(vals))})
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
                        and prev.get("billable_tools_sentiment") is not None
                    ):
                        continue
                    by[key] = row
                notes.append(
                    f"{sub}: fetched {len(posts)} posts, {len(weeks)} week buckets"
                )
            except Exception as e:
                errors.append(f"{sub}: {e}")

    for row in by.values():
        _ensure_billable_tools_sentiment(row)
    _write_history(history_path, by)
    rows = list(by.values())

    series_map = {
        "accenture_mentions": ("*", "accenture"),
        "cognizant_mentions": ("*", "cognizant"),
        "infosys_mentions": ("*", "infosys"),
        "epam_mentions": ("*", "epam"),
        "booz_mentions": ("*", "booz"),
        "billable_named_mentions": ("*", "named_products"),
        "consulting_community_posts": ("consulting", "total_posts"),
        "cscareerquestions_community_posts": ("cscareerquestions", "total_posts"),
        "itcareerquestions_community_posts": ("ITCareerQuestions", "total_posts"),
        "billable_tools_sentiment": ("*", "billable_tools_sentiment"),
        "ai_junior_displacement_sentiment": ("*", "ai_junior_displacement_sentiment"),
        "accenture_sentiment": ("*", "accenture_sentiment"),
        "cognizant_sentiment": ("*", "cognizant_sentiment"),
        "infosys_sentiment": ("*", "infosys_sentiment"),
        "epam_sentiment": ("*", "epam_sentiment"),
        "booz_sentiment": ("*", "booz_sentiment"),
    }
    topic_pct_fields = {
        "billable_hours_talk_mentions": "billable_hours_category",
        "ai_junior_displacement_mentions": "ai_junior_category",
    }

    series_out: dict[str, Any] = {}
    latest_date = ""
    post_totals = _weekly_total_posts(rows)
    for meta in catalog.get("series") or []:
        key = meta.get("key")
        if not key:
            continue
        if key in topic_pct_fields:
            counts = _points_from_history(
                rows, sub="*", field=topic_pct_fields[key]
            )
            pts = _share_of_total_points(counts, post_totals)
        elif key in series_map:
            sub, field = series_map[key]
            pts = _points_from_history(rows, sub=sub, field=field)
        else:
            continue
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
                        "accenture": r.get("accenture"),
                        "cognizant": r.get("cognizant"),
                        "infosys": r.get("infosys"),
                        "epam": r.get("epam"),
                        "booz": r.get("booz"),
                        "named_products": r.get("named_products"),
                        "billable_hours_category": r.get("billable_hours_category"),
                        "ai_junior_category": r.get("ai_junior_category"),
                        "billable_tools_sentiment": r.get("billable_tools_sentiment"),
                        "ai_junior_displacement_sentiment": r.get(
                            "ai_junior_displacement_sentiment"
                        ),
                        "lexicon_sentiment": r.get("lexicon_sentiment"),
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
        "source": (
            "Arctic Shift (reddit archive) — r/consulting + r/cscareerquestions + "
            "r/ITCareerQuestions"
        ),
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
    backfill_posts: int = 4000,
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
        default=4000,
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
