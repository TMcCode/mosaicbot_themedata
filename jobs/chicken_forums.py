#!/usr/bin/env python3
"""Chicken QSR forums sidecar — Arctic Shift weekly mention counts + thin digest.

Pack: chicken_forums. Theme context for Restaurants '26: Chicken Buyers.
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
PACK = "chicken_forums"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+chicken_forums; arctic-shift read-only)"
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "chicken_forums_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_HISTORY = "weekly_history.jsonl"
# Arctic Shift pulls N most-recent posts per sub. Quiet subs (ElPolloLoco) stretch
# that window years back as 1-post weeks — not continuous coverage. Publish from
# the first week dense brand subs overlap (KFC + Wingstop in history).
_SERIES_FROM = "2026-06-01"

# Lexicon → series_key (posts can hit multiple).
LEXICON: list[tuple[str, re.Pattern[str], str]] = [
    ("Wingstop", re.compile(r"\bwingstop\b", re.I), "wingstop_mentions"),
    ("KFC", re.compile(r"\bkfc\b|kentucky fried", re.I), "kfc_mentions"),
    ("El Pollo Loco", re.compile(r"el\s*pollo\s*loco|\bpolo\s*loco\b", re.I), "el_pollo_loco_mentions"),
    ("Popeyes", re.compile(r"\bpopeyes?\b", re.I), "popeyes_mentions"),
    (
        "Chick-fil-A",
        re.compile(r"chick[\s\-]*fil[\s\-]*a|\bcfa\b", re.I),
        "chick_fil_a_mentions",
    ),
    (
        "Dave's Hot Chicken",
        re.compile(r"dave'?s\s*hot\s*chicken|\bdhc\b", re.I),
        "daves_hot_chicken_mentions",
    ),
    (
        "Raising Cane's",
        re.compile(r"raising\s*cane'?s?|\bcane'?s\b", re.I),
        "raising_canes_mentions",
    ),
    (
        "value/deals",
        re.compile(
            r"\b(value\s*menu|\$?\d+\s*meal|deal(s)?|coupon|promo|promotion|affordable)\b",
            re.I,
        ),
        "value_deal_mentions",
    ),
    (
        "chicken sandwich",
        re.compile(r"chicken\s*sandwich|mccrispy|saucy\s*nuggets?", re.I),
        "chicken_sandwich_mentions",
    ),
    ("wings", re.compile(r"\bwings?\b|boneless\s*wings?", re.I), "software"),
    ("loyalty", re.compile(r"loyalty|rewards?\s*program|club\s*wingstop", re.I), "software"),
]

SOFTWARE_TAGS = {name for name, _, _ in LEXICON}

# Lightweight product-talk polarity (not a general NLP model).
# Applied at scrape time; we store weekly means only — no full text on R2.
_POS = re.compile(
    r"\b("
    r"love|loved|great|excellent|delicious|crispy|worth it|recommend|recommended|"
    r"fast|quick|accurate|fresh|fire|goated|underrated|addicted|favorite|favourite|"
    r"best|solid|bomb|hits? different|never disappoints"
    r")\b",
    re.I,
)
_NEG = re.compile(
    r"\b("
    r"awful|terrible|garbage|trash|hate|hated|disgusting|soggy|cold|greasy|"
    r"overpriced|expensive|slow|wrong\s*order|missing|rude|dirty|sick|food\s*poison|"
    r"avoid|disappoint|scam|never\s*again|worst|waited|understaffed|skimpy|"
    r"portion|ripoff|rip-off|bail"
    r")\b",
    re.I,
)
_BRANDS = (
    "Wingstop",
    "KFC",
    "El Pollo Loco",
    "Popeyes",
    "Chick-fil-A",
    "Dave's Hot Chicken",
    "Raising Cane's",
    "value/deals",
    "chicken sandwich",
)
_NAMED = (
    "Wingstop",
    "KFC",
    "El Pollo Loco",
    "Popeyes",
    "Chick-fil-A",
    "Dave's Hot Chicken",
    "Raising Cane's",
)
_NAMED_COUNT_KEYS = (
    "wingstop",
    "kfc",
    "el_pollo_loco",
    "popeyes",
    "chick_fil_a",
    "daves_hot_chicken",
    "raising_canes",
)
_TOPIC_COUNT_KEYS = ("value_deals", "chicken_sandwich")


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
    return {
        "week_ending": we,
        "subreddit": sub,
        "total_posts": 0,
        "lexicon_hits": 0,
        "wingstop": 0,
        "kfc": 0,
        "el_pollo_loco": 0,
        "popeyes": 0,
        "chick_fil_a": 0,
        "daves_hot_chicken": 0,
        "raising_canes": 0,
        "named_products": 0,
        "value_deals": 0,
        "chicken_sandwich": 0,
        # sentiment accumulators (mean taken at end)
        "_sent_lexicon": [],
        "_sent_wingstop": [],
        "_sent_kfc": [],
        "_sent_el_pollo_loco": [],
        "_sent_popeyes": [],
        "_sent_chick_fil_a": [],
        "_sent_daves_hot_chicken": [],
        "_sent_raising_canes": [],
        "_sent_named_products": [],
        "_sent_value_deals": [],
        "_sent_chicken_sandwich": [],
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
        if "Wingstop" in tags:
            bucket["wingstop"] += 1
            bucket["_sent_wingstop"].append(sent)
        if "KFC" in tags:
            bucket["kfc"] += 1
            bucket["_sent_kfc"].append(sent)
        if "El Pollo Loco" in tags:
            bucket["el_pollo_loco"] += 1
            bucket["_sent_el_pollo_loco"].append(sent)
        if "Popeyes" in tags:
            bucket["popeyes"] += 1
            bucket["_sent_popeyes"].append(sent)
        if "Chick-fil-A" in tags:
            bucket["chick_fil_a"] += 1
            bucket["_sent_chick_fil_a"].append(sent)
        if "Dave's Hot Chicken" in tags:
            bucket["daves_hot_chicken"] += 1
            bucket["_sent_daves_hot_chicken"].append(sent)
        if "Raising Cane's" in tags:
            bucket["raising_canes"] += 1
            bucket["_sent_raising_canes"].append(sent)
        if set(_NAMED) & set(tags):
            bucket["named_products"] += 1
            bucket["_sent_named_products"].append(sent)
        if "value/deals" in tags:
            bucket["value_deals"] += 1
            bucket["_sent_value_deals"].append(sent)
        if "chicken sandwich" in tags:
            bucket["chicken_sandwich"] += 1
            bucket["_sent_chicken_sandwich"].append(sent)
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
            ("wingstop_sentiment", "_sent_wingstop"),
            ("kfc_sentiment", "_sent_kfc"),
            ("el_pollo_loco_sentiment", "_sent_el_pollo_loco"),
            ("popeyes_sentiment", "_sent_popeyes"),
            ("chick_fil_a_sentiment", "_sent_chick_fil_a"),
            ("daves_hot_chicken_sentiment", "_sent_daves_hot_chicken"),
            ("raising_canes_sentiment", "_sent_raising_canes"),
            ("brand_sentiment", "_sent_named_products"),
            ("value_deal_sentiment", "_sent_value_deals"),
            ("chicken_sandwich_sentiment", "_sent_chicken_sandwich"),
        ):
            vals = bucket.pop(acc, [])
            bucket[field] = (
                round(sum(vals) / len(vals), 4) if vals else None
            )
        cands = bucket.pop("digest_candidates")
        # Prefer brand-tagged + extreme sentiment for digest substance
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
    ("wingstop", "wingstop_sentiment"),
    ("kfc", "kfc_sentiment"),
    ("el_pollo_loco", "el_pollo_loco_sentiment"),
    ("popeyes", "popeyes_sentiment"),
    ("chick_fil_a", "chick_fil_a_sentiment"),
    ("daves_hot_chicken", "daves_hot_chicken_sentiment"),
    ("raising_canes", "raising_canes_sentiment"),
)


def _ensure_brand_sentiment(row: dict[str, Any]) -> None:
    """Fill brand_sentiment from unique-post mean, or brand-weighted fallback."""
    if row.get("brand_sentiment") is not None:
        return
    num = den = 0.0
    for count_k, sent_k in _NAMED_SENT_WEIGHTS:
        c = float(row.get(count_k) or 0)
        s = row.get(sent_k)
        if c > 0 and s is not None:
            num += c * float(s)
            den += c
    if den > 0:
        row["brand_sentiment"] = round(num / den, 4)


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
        if field.endswith("_sentiment") or field == "brand_sentiment":
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
            ("Wingstop", backfill_posts),
            ("KFC", backfill_posts),
            ("ElPolloLoco", max(400, backfill_posts // 2)),
            # high-volume — need deeper pulls to match June brand-sub window
            ("fastfood", max(3000, backfill_posts)),
            ("ChickFilA", max(2000, backfill_posts)),
            ("RaisingCanes", max(1200, backfill_posts)),
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
                        and prev.get("brand_sentiment") is not None
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
        # cross-sub brand counts (field summed across all scraped subs)
        "wingstop_mentions": ("*", "wingstop"),
        "kfc_mentions": ("*", "kfc"),
        "el_pollo_loco_mentions": ("*", "el_pollo_loco"),
        "popeyes_mentions": ("*", "popeyes"),
        "chick_fil_a_mentions": ("*", "chick_fil_a"),
        "daves_hot_chicken_mentions": ("*", "daves_hot_chicken"),
        "raising_canes_mentions": ("*", "raising_canes"),
        "named_product_mentions": ("*", "named_products"),
        # Brand-home community volume = total posts (comparable across brand subs).
        "wingstop_community_posts": ("Wingstop", "total_posts"),
        "kfc_community_posts": ("KFC", "total_posts"),
        "chickfila_community_posts": ("ChickFilA", "total_posts"),
        "raising_canes_community_posts": ("RaisingCanes", "total_posts"),
        "brand_sentiment": ("*", "brand_sentiment"),
    }
    # Mixed topic sub: share of posts hitting chicken/QSR lexicon (separate chart).
    ratio_keys = {"fastfood_lexicon_pct": "fastfood"}
    # Topic charts: publish % of all watched-sub posts (not raw counts).
    topic_pct_fields = {
        "value_deal_mentions": "value_deals",
        "chicken_sandwich_mentions": "chicken_sandwich",
    }

    series_out: dict[str, Any] = {}
    latest_date = ""
    post_totals = _weekly_total_posts(rows)
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
                        "lexicon_hits": r.get("lexicon_hits", r.get("software_mentions")),
                        "total_posts": r.get("total_posts"),
                        "wingstop": r.get("wingstop"),
                        "kfc": r.get("kfc"),
                        "el_pollo_loco": r.get("el_pollo_loco"),
                        "popeyes": r.get("popeyes"),
                        "value_deals": r.get("value_deals"),
                        "brand_sentiment": r.get("brand_sentiment"),
                        "value_deal_sentiment": r.get("value_deal_sentiment"),
                        "lexicon_sentiment": r.get(
                            "lexicon_sentiment", r.get("software_sentiment")
                        ),
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
        "source": "Arctic Shift (reddit archive) — r/Wingstop + r/KFC + r/ElPolloLoco + r/ChickFilA + r/RaisingCanes + r/fastfood",
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
    backfill_posts: int = 600,
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
        default=600,
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
