"""Alt scrapes for Commerce & Merchant Platforms pack.

Wikipedia pageviews, Shopify App Store category counts, iTunes rating counts,
npm package downloads. Weekly append history where sources are point-in-time.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UA = "mosaicbot_themedata/0.1 (+commerce_merchant_alt; research read-only)"
WIKI_UA = "mosaicbot_themedata/0.1 (https://stockcontext.info; commerce pageviews)"

WIKI_ARTICLES: list[tuple[str, str]] = [
    ("shopify", "Shopify"),
    ("wix", "Wix.com"),
    ("toast", "Toast,_Inc."),
    ("block", "Block,_Inc."),
    ("magento", "Magento"),
]

# (series field, App Store category URL path fragment after /categories/)
SHOPIFY_APP_CATEGORIES: list[tuple[str, str]] = [
    (
        "product_content",
        "store-design-content-product-content/all",
    ),
    (
        "search_filters",
        "store-design-search-and-navigation-search-and-filters/all",
    ),
    (
        "analytics",
        "store-management-operations-analytics/all",
    ),
]

# (field, iTunes trackId)
ITUNES_APPS: list[tuple[str, int]] = [
    ("shop_consumer", 1223471316),  # Shop: Track, Pay, Discover
    ("shopify_merchant", 371294472),  # Shopify: Sell. Run a business.
    ("square_pos", 335948318),  # Square Point of Sale (verify via lookup)
    ("godaddy", 1470680860),  # may need resolve
    ("wix", 1099745985),
]

NPM_PACKAGES: list[tuple[str, str]] = [
    ("shopify_api", "@shopify/shopify-api"),
    ("shopify_cli", "@shopify/cli"),
    ("square_sdk", "square"),
    ("wix_sdk", "@wix/sdk"),
]

_APP_COUNT_RX = re.compile(r"([\d,]+)\s+apps?\b", re.I)


def _week_ending_sunday(d: date | None = None) -> str:
    d = d or datetime.now(timezone.utc).date()
    return (d + timedelta(days=(6 - d.weekday()))).isoformat()


def _month_start(ts: str) -> str:
    s = str(ts)[:8]
    return f"{s[:4]}-{s[4:6]}-01"


def _http_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 60) -> Any:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 60) -> str:
    h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _append_history(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    week = str(row.get("week") or "")
    existing: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                existing.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    kept = [r for r in existing if str(r.get("week") or "") != week]
    kept.append(row)
    kept.sort(key=lambda r: str(r.get("week") or ""))
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in kept),
        encoding="utf-8",
    )


def _points_from_history(path: Path, field: str) -> list[dict[str, float]]:
    if not path.exists():
        return []
    pts: list[dict[str, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        week = row.get("week")
        val = row.get(field)
        if week is None or val is None:
            continue
        try:
            pts.append({"date": str(week)[:10], "value": float(val)})
        except (TypeError, ValueError):
            continue
    pts.sort(key=lambda p: p["date"])
    return pts


# --- Wikipedia ----------------------------------------------------------------


def wikipedia_brand_and_basket(
    *, start_year: int = 2018
) -> tuple[dict[str, list], list, list[str]]:
    """Monthly pageviews per brand + equal-weight basket sum."""
    notes: list[str] = []
    brands: dict[str, list] = {}
    start = f"{start_year:04d}0101"
    end = datetime.now(timezone.utc).strftime("%Y%m%d")
    basket_by_month: dict[str, float] = {}

    for field, article in WIKI_ARTICLES:
        url = (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia/all-access/all-agents/{urllib.parse.quote(article, safe='')}"
            f"/monthly/{start}/{end}"
        )
        try:
            data = _http_json(url, headers={"User-Agent": WIKI_UA})
            items = data.get("items") or []
            pts: list[dict[str, float]] = []
            for it in items:
                m = _month_start(str(it.get("timestamp") or ""))
                v = float(it.get("views") or 0)
                pts.append({"date": m, "value": v})
                basket_by_month[m] = basket_by_month.get(m, 0.0) + v
            brands[field] = pts
            notes.append(f"wiki {field} ({article}): n={len(pts)}")
        except Exception as e:
            brands[field] = []
            notes.append(f"wiki {field}: ERR {e}")
        time.sleep(0.15)

    basket = [
        {"date": d, "value": basket_by_month[d]}
        for d in sorted(basket_by_month.keys())
    ]
    return brands, basket, notes


# --- Shopify App Store --------------------------------------------------------


def scrape_shopify_app_categories(hist_path: Path) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    week = _week_ending_sunday()
    row: dict[str, Any] = {"week": week, "fetched_at": datetime.now(timezone.utc).isoformat()}
    total = 0.0
    for field, path in SHOPIFY_APP_CATEGORIES:
        url = f"https://apps.shopify.com/categories/{path}"
        try:
            html = _http_text(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    )
                },
            )
            ms = _APP_COUNT_RX.findall(html)
            # Prefer the page H1 count (first reasonable <20k hit).
            n = None
            for m in ms:
                v = int(m.replace(",", ""))
                if 1 <= v <= 50000:
                    n = v
                    break
            if n is None:
                raise RuntimeError(f"no app count in HTML (matches={ms[:5]})")
            row[field] = float(n)
            total += float(n)
            notes.append(f"shopify_apps {field}: {n}")
        except Exception as e:
            notes.append(f"shopify_apps {field}: ERR {e}")
        time.sleep(0.4)
    if total > 0:
        row["category_sum"] = total
        _append_history(hist_path, row)
    return row, notes


def shopify_app_points(hist_path: Path, field: str) -> list[dict[str, float]]:
    return _points_from_history(hist_path, field)


# --- iTunes -------------------------------------------------------------------


def _resolve_itunes_ids() -> list[tuple[str, int]]:
    """Prefer known IDs; fall back to search for fragile ones."""
    out: list[tuple[str, int]] = []
    # Shop consumer + Shopify merchant — stable.
    out.append(("shop_consumer", 1223471316))
    out.append(("shopify_merchant", 371294472))

    searches = [
        ("square_pos", "Square Point of Sale", "Square Point of Sale"),
        ("godaddy", "GoDaddy Websites Marketing", "GoDaddy"),
        ("wix", "Wix Website Builder", "Wix"),
        ("toast_now", "Toast Now", "Toast Now"),
    ]
    for field, term, name_hint in searches:
        try:
            url = (
                "https://itunes.apple.com/search?"
                + urllib.parse.urlencode(
                    {
                        "term": term,
                        "entity": "software",
                        "country": "us",
                        "limit": 8,
                    }
                )
            )
            data = _http_json(url)
            best = None
            for it in data.get("results") or []:
                name = str(it.get("trackName") or "")
                tid = it.get("trackId")
                if not tid:
                    continue
                if name_hint.lower() in name.lower():
                    best = int(tid)
                    if name.lower().startswith(name_hint.lower()[:8]):
                        break
            if best:
                out.append((field, best))
        except Exception:
            pass
        time.sleep(0.2)
    return out


def scrape_itunes_ratings(hist_path: Path) -> tuple[dict[str, Any], list[str]]:
    notes: list[str] = []
    week = _week_ending_sunday()
    row: dict[str, Any] = {"week": week, "fetched_at": datetime.now(timezone.utc).isoformat()}
    ids = _resolve_itunes_ids()
    basket = 0.0
    for field, tid in ids:
        try:
            url = f"https://itunes.apple.com/lookup?id={tid}&country=us"
            data = _http_json(url)
            results = data.get("results") or []
            if not results:
                raise RuntimeError("empty lookup")
            it = results[0]
            count = float(it.get("userRatingCount") or 0)
            row[field] = count
            if field in ("shop_consumer", "shopify_merchant", "square_pos", "wix", "godaddy"):
                basket += count
            notes.append(
                f"itunes {field}: ratings={int(count)} "
                f"avg={it.get('averageUserRating')} ({it.get('trackName')})"
            )
        except Exception as e:
            notes.append(f"itunes {field}: ERR {e}")
        time.sleep(0.15)
    if basket > 0:
        row["ratings_basket"] = basket
        _append_history(hist_path, row)
    return row, notes


def itunes_points(hist_path: Path, field: str) -> list[dict[str, float]]:
    return _points_from_history(hist_path, field)


# --- npm ----------------------------------------------------------------------


def scrape_npm_downloads(hist_path: Path) -> tuple[dict[str, Any], list[str]]:
    """Append last-month download point for key commerce SDKs (weekly snapshot)."""
    notes: list[str] = []
    week = _week_ending_sunday()
    row: dict[str, Any] = {"week": week, "fetched_at": datetime.now(timezone.utc).isoformat()}
    total = 0.0
    for field, pkg in NPM_PACKAGES:
        try:
            url = f"https://api.npmjs.org/downloads/point/last-month/{urllib.parse.quote(pkg, safe='@/')}"
            data = _http_json(url)
            n = float(data.get("downloads") or 0)
            row[field] = n
            total += n
            notes.append(f"npm {field} ({pkg}): {int(n)}")
        except Exception as e:
            notes.append(f"npm {field}: ERR {e}")
        time.sleep(0.15)
    if total > 0:
        row["npm_basket"] = total
        _append_history(hist_path, row)
    return row, notes


def npm_points(hist_path: Path, field: str) -> list[dict[str, float]]:
    return _points_from_history(hist_path, field)
