# Dividend Radar

Daily, plain-language health checks of dividend-paying companies worldwide, scored against the qualities that make **BSSR** (Baramulti Suksessarana) attractive: a generous dividend, paid from real cash, little debt, steady profits, and a fair price.

## How it works

- `pipeline/build.py` runs every day at 05:30 WIB via GitHub Actions (`.github/workflows/daily.yml`). It pulls data from Yahoo Finance (`yfinance`), scores each company out of 100, and writes JSON to `docs/data/`.
- `docs/` is a static site served by GitHub Pages. It has no build step and no framework.
- All plain-language explanations live in `docs/explain.js`.

## Adding companies

Add a Yahoo Finance symbol to `pipeline/universe.py` (e.g. `ADMR.JK`). It shows up after the next daily run.

## Run manually

- Locally (Python 3.9+): `pip install -r pipeline/requirements.txt && python pipeline/build.py` (or pass a few symbols: `python pipeline/build.py BSSR.JK PTBA.JK`)
- On GitHub: Actions → Daily update → Run workflow

Educational tool, not financial advice.
