# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Run the Streamlit UI: `streamlit run app.py`
- Run the CLI quote/indicator query: `python main.py 2330 [--otc] [--period 1y]`
- Run daily post-market data ingestion (institutional flow + margin balance → SQLite): `python daily_update.py`
- Run the pre-market brief (US overnight + TAIFEX + VIX → Telegram): `python morning_brief.py`
- Run the signal-wall scan (technical signals on the watchlist → Telegram): `python signals_wall.py`
- Run the intraday alert monitor (15s poll + Telegram push): `python alert_monitor.py`
- Tests: `pytest` is not in `requirements.txt` — install it first (`pip install pytest`), then:
  - `pytest tests/ -q` — unit tests with `tests/conftest.py` fixtures (chip_data/database/fetch_data/risk)
  - `pytest test_daily_update.py test_morning_brief.py test_signals_wall.py -q` — three more test files that live at repo root instead of `tests/`
  - Single test: `pytest tests/test_database.py::TestGetWarrantFlow::test_upsert_and_get_warrant_flow -q`
  - `test_daily_update.py` and `test_morning_brief.py` hardcode the original author's Windows path (`sys.path.insert(0, r"C:\Users\Ryzen USER\...")`) as a leftover — harmless when run with `pytest` from the repo root (pytest already puts the rootdir on `sys.path`), but don't rely on that line working literally.
- Local Telegram config: copy `alert_config.example.py` → `alert_config.py` and fill in the bot token/chat id/watchlist. On Fly.io, `alert_config.py` doesn't exist, so scripts fall back to `alert_config_cloud.py` + the `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env vars (set via `fly secrets set`).
- Deploy is manual: `flyctl deploy`. `.github/workflows/fly-schedule.yml` only starts/stops the Fly.io machine on a weekday schedule to save cost — it is not a CI/CD deploy pipeline.

## Architecture

- **SQLite is the single data layer.** `database.py` owns all schema (`institutional_flow`, `margin_balance`, `warrant_flow`, `warrant_large_trade`, `twse_mis_cache`, `history_cache`, `code_market`) at `data_cache/stock.db`, plus generic `get_*`/`upsert_*` helpers. Every fetcher (`chip_data.py`, `margin_data.py`, `fetch_data.py`) follows the same pattern: check the cache for the requested date range first, only hit the network for the missing dates, then upsert the result back before returning.
- **Batch vs. per-code fetching (chip_data.py).** The TWSE/TPEx MIS APIs return one full-market table per trading day, not per stock. `chip_data.py` has two idioms built on the same underlying tables:
  - per-code (e.g. `get_warrant_flow`, `get_institutional_flow`): loops per date, re-fetches the whole day's table, then filters for one stock — fine for single-stock lookups but wasteful if called in a loop over many stocks.
  - batch (e.g. `get_institutional_flow_multi`, `get_warrant_large_trade_counts_multi`): fetches each day's table once and slices it for every requested code in the same pass.
  Any new feature that scans/screens many stocks should extend the batch path, not call a per-code function in a loop.
- **Signal rules (`signals.py`)** combine three data sources into one long-signal: 三大法人 (three major institutional investors) net-buy trend, 個股認購權證 (single-stock call warrant) large single-trade detection (`_fetch_warrant_table` via `chip_data.py`), and an optional XQ 分點 (broker branch) ranking corroboration (`xq_branch.py`). All thresholds (`WARRANT_SINGLE_TRADE_THRESHOLD`, `WARRANT_STRONG_TRADE_MIN_COUNT`, `WARRANT_TIER_THRESHOLDS`, etc.) are constants at the top of `signals.py` — the module docstring documents the exact rule combination and its known limitation (TWSE only publishes per-warrant daily totals, not individual trades, so a large same-day value can't be distinguished from a large seller).
- **`xq_branch.py`** reads CSVs the user manually exports from the XQ 全球贏家 desktop app into `xq_branch_data/` (filenames start with the stock code). There is no API for this data — it only exists for stocks the user has bothered to export, and `.gitignore` excludes these files/`.dsl` watchlists as personal trading data.
- **`app.py`** is a single-file Streamlit app (~2300 lines) with four tabs — 技術分析 (chart), 做多訊號 (long signal), 分點掃描 (branch scan), 風險 (risk) — that reuse the same fetch/signal modules as the CLI and cron scripts. The 加權指數 (index) shares the chart tab but is excluded from the institutional/warrant-dependent tabs since TWSE doesn't publish that data for indices.
- **Two runtime surfaces share this codebase**: the interactive Streamlit app, and an always-on Fly.io background service (`Dockerfile` + `start.sh`) that runs `alert_monitor.py` in the foreground alongside `supercronic` executing the schedule in `crontab` (`daily_update.py`, `morning_brief.py`, `signals_wall.py`). Times in `crontab` are UTC; comments give the Asia/Taipei equivalent.
