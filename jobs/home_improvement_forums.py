#!/usr/bin/env python3
"""Home improvement forums sidecar — Arctic Shift weekly mention counts + thin digest.

Pack: home_improvement_forums. Theme context for Buying a House '26: Home Improvement Retailers.
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
PACK = "home_improvement_forums"
R2_PREFIX = f"themedata/{PACK}"
UA = "mosaicbot_themedata/0.1 (+home_improvement_forums; arctic-shift read-only)"
ARCTIC = "https://arctic-shift.photon-reddit.com/api/posts/search"
_DEFAULT_OUT = _ROOT / "data" / PACK
_CATALOG = _ROOT / "config" / "home_improvement_forums_catalog.json"
_ATTACHMENTS = _ROOT / "config" / "series_attachments.json"
_HISTORY = "weekly_history.jsonl"
# Busy HI/DIY subs only reach ~May/Jun 2026 at 12k posts; quieter r/homeowners
# stretches further back. Publish from first week all three overlap (else stacks cliff).
_SERIES_FROM = "2026-06-07"

# Lexicon → series_key (posts can hit multiple).
LEXICON: list[tuple[str, re.Pattern[str], str]] = [
    (
        "Home Depot",
        re.compile(r"home\s*depot|\bhomedepot\b", re.I),
        "home_depot_mentions",
    ),
    (
        "Lowe's",
        re.compile(r"lowe'?s|\blowes\b", re.I),
        "lowes_mentions",
    ),
    (
        "Floor & Decor",
        re.compile(r"floor\s*(?:&|and)\s*decor|\bfnd\b", re.I),
        "floor_decor_mentions",
    ),
    (
        "Tractor Supply",
        re.compile(r"tractor\s*supply|\btsco\b|\bpetsense\b", re.I),
        "tractor_supply_mentions",
    ),
    (
        "Leslie's",
        re.compile(r"leslie'?s(?:\s+pool)?", re.I),
        "leslies_mentions",
    ),
    (
        "remodel",
        re.compile(
            r"\bremodel(?:ing|s)?\b|\brenovation(?:s)?\b|\breno\b|"
            r"diy\s*project|bathroom\s*remodel|kitchen\s*remodel|"
            r"home\s*improvement\s*project",
            re.I,
        ),
        "remodel_mentions",
    ),
    (
        "maintenance",
        re.compile(
            r"\bmaintenance\b|\brepair(?:s|ing)?\b|\bfix(?:ing|ed)?\b|"
            r"how\s*(?:do|to)\s*(?:i|you)\s*(?:fix|replace|install)|"
            r"leaky|clogged|replace\s*(?:the\s+)?(?:faucet|filter|gasket|seal)|"
            r"hvac\s*(?:filter|maintenance)|water\s*heater\s*(?:flush|anode)|"
            r"seasonal\s*maintenance|preventive\s*maintenance",
            re.I,
        ),
        "maintenance_mentions",
    ),
    (
        "cost_talk",
        re.compile(
            r"\b(?:too\s*)?expensive\b|\boverpriced\b|\bcost(?:s|ly)?\b|"
            r"\$\s?\d[\d,]*(?:\.\d+)?|\bquote(?:s|d)?\b|\bbid(?:s)?\b|"
            r"afford(?:able)?|budget|cheap(?:er)?|price(?:y|s)?|"
            r"mortgage|property\s*tax|home\s*(?:price|value)|housing\s*cost|"
            r"worth\s*(?:the\s*)?(?:money|price|it)",
            re.I,
        ),
        "cost_talk_mentions",
    ),
    ("DIY", re.compile(r"\bdiy\b|do\s*it\s*yourself", re.I), "software"),
    ("contractor", re.compile(r"\bcontractor(?:s)?\b|handyman", re.I), "software"),
    ("landscaping", re.compile(r"landscap|lawn\s*care|\bgarden(?:ing)?\b", re.I), "software"),
    ("flooring", re.compile(r"\bflooring\b|\btile\b|hardwood|laminate|vinyl\s*plank", re.I), "software"),
]

SOFTWARE_TAGS = {name for name, _, _ in LEXICON}

# Lightweight product-talk polarity (not a general NLP model).
_POS = re.compile(
    r"\b("
    r"love|loved|great|excellent|worth it|recommend|recommended|helpful|"
    r"quality|solid|favorite|favourite|best|underrated|easy|saved|bargain|"
    r"good deal|fast|quick|accurate|fire|goated|never disappoints"
    r")\b",
    re.I,
)
_NEG = re.compile(
    r"\b("
    r"awful|terrible|garbage|trash|hate|hated|overpriced|expensive|slow|"
    r"rude|dirty|avoid|disappoint|scam|never\s*again|worst|waited|"
    r"understaffed|ripoff|rip-off|bail|shoddy|defective|return|refund|"
    r"out of stock|bait.?and.?switch|nightmare"
    r")\b",
    re.I,
)
_BRANDS = (
    "Home Depot",
    "Lowe's",
    "Floor & Decor",
    "Tractor Supply",
    "Leslie's",
    "remodel",
    "maintenance",
    "cost_talk",
)
_NAMED = (
    "Home Depot",
    "Lowe's",
    "Floor & Decor",
    "Tractor Supply",
    "Leslie's",
)
_NAMED_COUNT_KEYS = (
    "home_depot",
    "lowes",
    "floor_decor",
    "tractor_supply",
    "leslies",
)
_TOPIC_COUNT_KEYS = ("remodel", "maintenance", "cost_talk")


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
        "home_depot": 0,
        "lowes": 0,
        "floor_decor": 0,
        "tractor_supply": 0,
        "leslies": 0,
        "named_products": 0,
        "remodel": 0,
        "maintenance": 0,
        "cost_talk": 0,
        "_sent_lexicon": [],
        "_sent_home_depot": [],
        "_sent_lowes": [],
        "_sent_floor_decor": [],
        "_sent_tractor_supply": [],
        "_sent_leslies": [],
        "_sent_named_products": [],
        "_sent_remodel": [],
        "_sent_maintenance": [],
        "_sent_cost_talk": [],
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
        if "Home Depot" in tags:
            bucket["home_depot"] += 1
            bucket["_sent_home_depot"].append(sent)
        if "Lowe's" in tags:
            bucket["lowes"] += 1
            bucket["_sent_lowes"].append(sent)
        if "Floor & Decor" in tags:
            bucket["floor_decor"] += 1
            bucket["_sent_floor_decor"].append(sent)
        if "Tractor Supply" in tags:
            bucket["tractor_supply"] += 1
            bucket["_sent_tractor_supply"].append(sent)
        if "Leslie's" in tags:
            bucket["leslies"] += 1
            bucket["_sent_leslies"].append(sent)
        if set(_NAMED) & set(tags):
            bucket["named_products"] += 1
            bucket["_sent_named_products"].append(sent)
        if "remodel" in tags:
            bucket["remodel"] += 1
            bucket["_sent_remodel"].append(sent)
        if "maintenance" in tags:
            bucket["maintenance"] += 1
            bucket["_sent_maintenance"].append(sent)
        if "cost_talk" in tags:
            bucket["cost_talk"] += 1
            bucket["_sent_cost_talk"].append(sent)
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
            ("home_depot_sentiment", "_sent_home_depot"),
            ("lowes_sentiment", "_sent_lowes"),
            ("floor_decor_sentiment", "_sent_floor_decor"),
            ("tractor_supply_sentiment", "_sent_tractor_supply"),
            ("leslies_sentiment", "_sent_leslies"),
            ("brand_sentiment", "_sent_named_products"),
            ("remodel_sentiment", "_sent_remodel"),
            ("maintenance_sentiment", "_sent_maintenance"),
            ("cost_talk_sentiment", "_sent_cost_talk"),
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
    ("home_depot", "home_depot_sentiment"),
    ("lowes", "lowes_sentiment"),
    ("floor_decor", "floor_decor_sentiment"),
    ("tractor_supply", "tractor_supply_sentiment"),
    ("leslies", "leslies_sentiment"),
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
    """Share of subreddit posts that hit the HI lexicon (0–100)."""
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
            # Busy generalist subs — ~500–600 posts/wk; 12k ≈ ~5 months each.
            ("HomeImprovement", max(12000, backfill_posts)),
            ("homeowners", max(12000, backfill_posts)),
            ("DIY", max(12000, backfill_posts)),
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
        "home_depot_mentions": ("*", "home_depot"),
        "lowes_mentions": ("*", "lowes"),
        "floor_decor_mentions": ("*", "floor_decor"),
        "tractor_supply_mentions": ("*", "tractor_supply"),
        "leslies_mentions": ("*", "leslies"),
        "named_product_mentions": ("*", "named_products"),
        "homeimprovement_community_posts": ("HomeImprovement", "total_posts"),
        "homeowners_community_posts": ("homeowners", "total_posts"),
        "diy_community_posts": ("DIY", "total_posts"),
        "brand_sentiment": ("*", "brand_sentiment"),
        "remodel_sentiment": ("*", "remodel_sentiment"),
        "maintenance_sentiment": ("*", "maintenance_sentiment"),
        "cost_talk_sentiment": ("*", "cost_talk_sentiment"),
    }
    ratio_keys: dict[str, str] = {}
    topic_pct_fields = {
        "remodel_mentions": "remodel",
        "maintenance_mentions": "maintenance",
        "cost_talk_mentions": "cost_talk",
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
                        "home_depot": r.get("home_depot"),
                        "lowes": r.get("lowes"),
                        "floor_decor": r.get("floor_decor"),
                        "tractor_supply": r.get("tractor_supply"),
                        "leslies": r.get("leslies"),
                        "remodel": r.get("remodel"),
                        "maintenance": r.get("maintenance"),
                        "cost_talk": r.get("cost_talk"),
                        "brand_sentiment": r.get("brand_sentiment"),
                        "remodel_sentiment": r.get("remodel_sentiment"),
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
        "source": "Arctic Shift (reddit archive) — r/HomeImprovement + r/homeowners + r/DIY",
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
    backfill_posts: int = 12000,
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
        default=12000,
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
