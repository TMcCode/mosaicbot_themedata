# Agent guide — mosaicbot_themedata

Conventions for agents and humans working in this repo (and the sibling web UI).
Read this before adding packs, series, attachments, or chart UI.

## Product split (do not collapse)

| Layer | Owns | Does **not** |
|-------|------|----------------|
| **Pack** | One domain dataset: sources, series, derived metrics, labels | Theme/ticker wiring; live UI fetches |
| **Attachments** | Editorial map: series → theme and/or ticker | Fetch logic; chart layout recipes |
| **Web** (`mosaicbot_themedata_web`) | Presentation only from published JSON | BLS / Census / AISI calls |
| **Admin** (`MosaicBotMain` admin dashboard) | Edit attachments, theme score recipes (weights / polarity / enable), default boards | Own a second copy of pack ETL |

Sibling pattern matches stockthemes / stockcontext: **data repo publishes → site repo reads**.

## Domain packs vs release cadence

- **Packs are semantic** (lab economics, steel, construction). Group by what the theme story is.
- **Do not** split packs by “these numbers drop on the same day.”
- **Cadence belongs on schedule / series metadata** (`config/update_schedule.json`, per-series `cadence` / `access` / `as_of_semantics` in pack catalogs, mirrored in `config/datasets.json`).
- Same-day series may share a fetch adapter under the hood; they still live in the domain pack catalog.

Dispatcher: `jobs/dispatch_due.py` — due window **or** stale `as_of`. Host-aware via `THEMEDATA_HOST`.

## Datasets registry vs series vs attachments

| Artifact | Owns |
|----------|------|
| **`config/datasets.json`** | Global registry of *sources* (free/paid/scrape). Paid = `status: map_only` until redistribution license — full commercial map seed is `alt_data_map.csv` (re-import via `scripts/import_alt_data_map.py`). Fields: `access`, `cadence`, `as_of_semantics`, `pack`, `series_keys`, plus paid-map `provider` / `aliases` / `panel`. |
| **Pack `*_catalog.json`** | Pullable **series** (`series_key`) with naming + `dataset_id` + `access`/`cadence`. |
| **`series_attachments.json`** | Editorial wiring: series → theme / ticker / membership. |
| **`config/news_feeds.json`** | News is a **different object** (article streams), not a series column. |

Per-theme `dataset_mapping_*.json` files are thin **indexes** into `datasets.json` (theme/ticker id lists), not a second copy of metadata.

### Watchlist → registry matching (free)

LLM monitoring watchlists (`EconomicDataWatch` / `FreeAltDataWatch` / `PaidAltDataWatch`), ticker **KeyInputsAndSourcing**, and **Ticker_KeyFactors** Free/Paid Alt Data cells still output plain provider+metric / input objects (Key Factors: 0–2 items per cell). After normalize, MosaicBot **post-processes** against `datasets.json` (sibling themedata or vendored `MosaicBotMain/config/themedata_datasets.json`) and fills:

- `dataset_id`, `access`, `cadence`, `as_of_semantics`, `registry_match` (`high|medium|none`)

No extra LLM call. Match prefers exact public ids (incl. key-input `commodity_code`), then **provider + detailed `data_type`** (paid map differentiator), with soft boosts from `canonical_data_type` / `sector`. No editorial rank. Unmatched free sources get heuristic `access` only (FRED→api, AISI→scrape, Placer→file).

### Series access / cadence (explicit)

On each catalog series (and matching dataset row):

- `access`: `api` | `scrape` | `file` | `manual`
- `cadence`: how often we *try* to refresh (`daily` / `weekly` / `monthly` / …) — may differ from economic `frequency`
- `as_of_semantics`: what point `date` means — `period_end` (BLS month) vs `vendor_as_of` (TE/AISI/Manifold print date) vs `scrape_as_of`

Schedule host/weekday still lives in `update_schedule.json` (VPS weekends for scrapes until Mac Mini).

### Provenance on published series

`lib/pack_series.series_shell` attaches:

```json
"provenance": {
  "fetched_at": "<ISO when we pulled>",
  "access": "api|scrape|...",
  "source": "...",
  "as_of_semantics": "period_end|vendor_as_of|...",
  "notes": "Point dates are vendor/period as_of; fetched_at is pull time."
}
```

Scrapes that append history (AISI, TE Ge) also keep `fetched_at` on each history snapshot row.

## News (not a series_key)

**Moved** to **`MosaicBotMain_Local_Dev/FetchEODData/news_radar_job.py`** (package `FetchEODData/news_radar/`). Not a themedata pack and **not** wired into `run.py`.

- Config: `FetchEODData/config/news_feeds.json`
- Bake: `radar_news.json` → `themedata/news/` (R2) / `data/news/` (local)
- Themedata `jobs/news_feeds.py` is a stub (exits 2)

Stockthemes Narrative Radar “In the News” reads the baked JSON only.

## History / backfill philosophy

- Prefer **whatever history the source returns today**; do not fail a pack because deep history is missing.
- If a code/series only exists from a certain date (e.g. HS `382219` kg from ~2022), start there and **keep appending** as new periods publish.
- True point-in-time sources (e.g. AISI weekly page = latest week only): scrape/store latest on each run and append to local/R2 history — same pattern going forward.
- Re-fetch APIs that expose a full window (BLS, Census ranges) on each run; treat empty pre-existence chunks as skip, not hard error.
- Theme scores use the latest available component metrics; missing series are omitted from the weighted mean until they appear.

## Repo layout (target)

```
mosaicbot_themedata/
  packs/                    # preferred long-term: one folder per pack
    <pack>/
      catalog.json
      job.py                # or jobs/<pack>.py until migrated
      schema.json
  config/
    series_attachments.json
    update_schedule.json
    <pack>_catalog.json     # current: lab_economics_catalog.json
  jobs/
    dispatch_due.py
    *.py                    # pack runners today
  lib/                      # shared transforms, R2, API clients
  schemas/
  data/                     # local outputs (gitignored)
  docs/
  AGENTS.md                 # this file
```

Today several packs still live as flat `jobs/*.py` + `config/*_catalog.json`. New work should keep **one pack = one catalog + one runner + one R2 prefix**. Prefer migrating toward `packs/<name>/` when touching a pack heavily.

## Naming fields (series catalog)

Every series should carry:

| Field | Use |
|-------|-----|
| `label` | Source-faithful / legacy technical title (keep for debugging) |
| `official_name` | Code or official taxonomy (e.g. `NAICS 621511 Medical Laboratories`, `HS 382219 …`) |
| `short_name` | Compact colloquial name |
| `display_name` | **UI default** — plain English chart title |

Web and boards prefer: `display_name` → `short_name` → `label`.

Colloquial examples already in use:

- `HS 382219` → diagnostic / lab reagents (imports, volume, $/kg)
- `NAICS 621511` → diagnostic labs / medical laboratories

## Themes registry (stockthemes join key)

Canonical list: **`config/themes.json`**.

| Field | Role |
|-------|------|
| `theme` | Exact stockthemes **display name** (group + year + theme title), e.g. `Space Supply Chain '26: Surveillance Satellites` |
| `theme_slug` | Exact stockthemes **URL slug**, e.g. `space-supply-chain-26-surveillance-satellites` |
| `group` / `group_slug` | stockthemes group (informational) |

Attachments, boards, and score recipes must carry **`theme_slug`** (not only `theme`) so renames and automation don’t rely on string equality alone. Prefer registry over `slugify(theme)` when present.

## Attachments format

Canonical editorial file (admin + seed):

- Repo seed: `config/series_attachments.json`
- R2: `r2://mosaic-themes/themedata/series_attachments.json` (admin **Theme Data Attachments** tool)
- Mirrored to `themedata/<pack>/attachments.json` on save

Fields:

- `scope`: `theme` | `ticker`
- `theme`: stockthemes display name (must match `config/themes.json`)
- `theme_slug`: stockthemes URL slug (must match `config/themes.json`)
- `ticker`: symbol or `null`
- `series_key`: pack catalog key

Theme-scoped = broad industry context. Ticker-scoped = company-local proxy (e.g. TX QCEW for CSTL).

**Ticker pages inherit theme context:** if a ticker is linked on any attachment that names a theme, the ticker page shows (1) `scope=ticker` series first, then (2) all `scope=theme` series for those themes. Do not duplicate every theme series as ticker rows unless you want a ticker-only chart that is *not* on the theme.

**Chart order:** pack catalog `usefulness` (1–5, higher first). This score is **per series in the pack**, not per theme or ticker. On ticker pages, company-local charts still pin above theme-context charts, then usefulness within each group.

## Theme backdrop scores

Config: `config/theme_scores.json` (per-theme weights + polarity).

- One **visible score per theme** (0–100). Tickers show one chip per theme they belong to (no forced mega-blend).
- Recompute **whenever contributing pack series update** (full pack job *or* partial refresh like weekly TE/AISI) via `lib.theme_score.recompute_and_publish` — cheap, no source refetch. Not a separate cron.
- Signal: clip YoY % at ±10 → `[-1, +1]`; `score = 50 + 50 * Σ(weight × polarity × signal) / Σ(weight)`.
- Publish under `themedata/scores/…` including a `history` array (same recipe over time; mixed frequencies carry forward).
- Theme/ticker UI: score chip + one backdrop history chart.
- **Ticker pages:** checkbox toggles for each linked theme that has a backdrop score; show chips + history only for checked themes (default: all on).

### Admin: score recipe editor (planned)

Admin tool (alongside attachments) to edit `theme_scores.json` / the published recipe without hand-editing JSON:

- Pick a theme → see components (series, metric, **weight**, **polarity**, enabled)
- Adjust weights / polarity / enable-disable
- Save to R2 (`themedata/scores/recipe.json` or sync back to config seed) and **trigger a score recompute** from latest pack metrics (no full BLS/Census refetch required)
- Do **not** invent new series here — only weight series already in the pack catalog

## Admin roadmap (themedata tools)

1. **Theme Data hub** — done (admin **Theme Data** tile with tabs): Datasets · Attachments · Score Weights · Boards
2. Canonical R2 objects: `themedata/datasets.json`, `themedata/series_attachments.json`, `themedata/scores/recipe.json`, `themedata/boards.json`

All hub tabs write editorial config to R2 under `themedata/`; pack jobs remain the only live source fetchers.

## Charts / boards (product rules)

**Not Tableau.** Constrained recipes only.

Building blocks:

1. Series (from pack catalog)
2. Metric (`level` / derived keys from catalog, e.g. `yoy_pct`, `m3_avg_yoy_pct`)
3. Chart type allowlist (`line` | `bar`; catalog default + optional override)
4. **Chart recipe** = `{ series_key, metric, chart_type }`
5. **Board** = ordered recipes for a theme or ticker

Layers:

- **Default board** — editorial (admin / config): what MedTech or CSTL shows by default
- **User board** — fork of default; add/hide/reorder from the **same catalog** only

Allow: metric toggle, reorder, hide, light 2-series compare when units match.  
Refuse: arbitrary SQL, free-form calculated fields, cross-pack mashups without an explicit comparable flag, dashboard-builder UI.

### Chart UI behavior (web)

Match stockthemes Lightweight Charts lock:

- `handleScale.mouseWheel / pinch / axisPressedMouseMove`: false
- `handleScroll`: false
- Fitted full range; no zoom/pan
- Presentation reads R2 / build cache only

## Storage

Private R2 bucket `mosaic-themes` (via `storage_compat` / this repo’s `lib/r2.py`):

```
r2://mosaic-themes/themedata/<pack>/
  latest.json
  catalog.json
  attachments.json
  raw/<series_key>.json
  derived/<series_key>.json
  metrics_latest.json
r2://mosaic-themes/themedata/series_attachments.json
r2://mosaic-themes/themedata/datasets.json
r2://mosaic-themes/themedata/scheduler/last_runs.json
```

Never use GCS for these objects. Env: `R2_*`, `MOSAIC_THEMES_BUCKET`, pack API keys in this repo’s `.env` (standalone).

## Adding a series / pack (checklist)

1. Register or update the **dataset** in `config/datasets.json` (`license`, `status`, `access`, `cadence`). Paid stays `map_only` until licensed.
2. Add or extend pack **catalog** with keys, source ids, `derived`, naming fields, `dataset_id` / `access` / `cadence` / `as_of_semantics`.
3. Implement / extend pack **job**; write `data/<pack>/` and publish under `themedata/<pack>/` (series include `provenance.fetched_at`).
4. Wire **attachments** if it should show on a theme/ticker.
5. Register pack in **update_schedule.json** (hosts + cadence).
6. Update **schema** if payload shape changes.
7. Web sync reads published JSON — no new live API calls in the site.

## Related repos

| Repo | Role |
|------|------|
| `mosaicbot_themedata` | Packs, jobs, attachments seed, R2 publish |
| `mosaicbot_themedata_web` | Static Next.js UI |
| `MosaicBotMain_Local_Dev` admin | Attachments / boards editor (planned) |
| `mosaicbot_stockthemes` / `mosaicbot_stockcontext` | Pattern reference for static export + LW Charts |
