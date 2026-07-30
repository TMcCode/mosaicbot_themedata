# mosaicbot_themedata

Macro / industry **datasets that inform themes** (steel production, construction end markets, etc.).

Separate from:
- **mosaicbot_stockcontext** — ticker thesis / text context
- **mosaicbot_stockthemes** — public theme site
- **MosaicBot** — ETL + dashboards

Designed to run on a **Mac Mini** (residential IP) for sources that block cloud scrapers (e.g. AISI).

## Jobs

| Job | Cadence | Source |
|-----|---------|--------|
| `jobs/aisi_weekly.py` | Weekly (Mon) | [AISI industry data](https://www.steel.org/industry-data/) — latest week only; we append history |
| `jobs/census_construction.py` | Monthly | Census C-30 via FRED (`TLMFGCONS`, `TLHWYCONS`, …) |

```bash
python3 jobs/aisi_weekly.py
python3 jobs/census_construction.py
```

Outputs land under `data/` (gitignored). JSON schemas under `schemas/`.

## Status

Early spike. Theme ↔ dataset attachment and R2 publish come next.
