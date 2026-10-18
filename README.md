# mosaicbot_themedata

Macro / industry **datasets that inform themes** (steel production, construction end markets, lab economics, etc.).

**Agents / contributors:** read [`AGENTS.md`](./AGENTS.md) for packs, attachments, naming, charts, and R2 conventions.

Separate from:
- **mosaicbot_stockcontext** — ticker thesis / text context
- **mosaicbot_stockthemes** — public theme site
- **MosaicBot** — ETL + dashboards

Designed to run **standalone** (own `.env`) on a **VPS now**, **Mac Mini** later for sources that block cloud scrapers (e.g. AISI).

## Setup

```bash
cd mosaicbot_themedata
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# copy/edit .env (gitignored): BLS_API_KEY, CENSUS_API_KEY, R2_*
```

## Jobs

| Job | Cadence | Source |
|-----|---------|--------|
| `jobs/aisi_weekly.py` | Weekly (Mon) | [AISI industry data](https://www.steel.org/industry-data/) — latest week only; we append history |
| `jobs/census_construction.py` | Monthly | Census C-30 via FRED (`TLMFGCONS`, `TLHWYCONS`, …) |
| `jobs/lab_economics_bls.py` | Monthly (via dispatcher) | Lab economics Phase A — BLS PPI/CES/ECI/QCEW → R2 `themedata/lab_economics/` |
| `jobs/dispatch_due.py` | Daily cron | Runs packs that are **due** on this host (`config/update_schedule.json`) |

```bash
python3 jobs/aisi_weekly.py
python3 jobs/census_construction.py
python3 jobs/lab_economics_bls.py            # local + R2
python3 jobs/lab_economics_bls.py --skip-r2  # local only
python3 jobs/lab_economics_bls.py --dry-run
python3 jobs/dispatch_due.py --dry-run
python3 jobs/dispatch_due.py --force lab_economics
```

### Scheduler / VPS (temporary)

Contabo hosts API packs until Mac Mini takes over. AISI is `hosts: ["macmini"]` only.

```bash
# from laptop
chmod +x scripts/install_vps.sh scripts/run_dispatch.sh
./scripts/install_vps.sh
```

Cron: `/etc/cron.d/mosaic-themedata` → daily **10:30 America/New_York**, `THEMEDATA_HOST=vps`, then themedata digest email.  
Status: `r2://mosaic-themes/themedata/scheduler/last_runs.json`

Outputs land under `data/` (gitignored). JSON schemas under `schemas/`.  
Series ↔ theme/ticker map: `config/series_attachments.json`.

### R2 layout (private `mosaic-themes`)

```
themedata/lab_economics/
  latest.json
  metrics_latest.json
  catalog.json
  attachments.json
  raw/<series_key>.json
  derived/<series_key>.json
```

## Status

Phase A lab economics + Phase B Census HS `382219` trade live. Theme ↔ dataset attachment editable via `config/series_attachments.json`.
