#!/usr/bin/env python3
"""MOVED — Narrative Radar news now lives in MosaicBotMain.

Use:
  cd ../MosaicBotMain_Local_Dev
  python FetchEODData/news_radar_job.py --themes 3 --tickers 0
  python FetchEODData/news_radar_job.py --bake-radar-only

See FetchEODData/news_radar/ and FetchEODData/config/news_feeds.json.
This stub remains so old cron/docs fail loudly instead of writing here.
"""

from __future__ import annotations

import sys

print(
    "ERROR: news job moved to MosaicBotMain_Local_Dev/FetchEODData/news_radar_job.py "
    "(not run.py). Do not run jobs/news_feeds.py in mosaicbot_themedata.",
    file=sys.stderr,
)
raise SystemExit(2)
