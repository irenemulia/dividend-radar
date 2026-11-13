# Dividend Radar

Plain-language health checks of dividend-paying companies worldwide, scored against the qualities that make **BSSR** (Baramulti Suksessarana) attractive: a generous dividend, paid from real cash, little debt, steady profits, and a fair price.

Live site: https://irenemulia.github.io/dividend-radar/

## How it works

- `pipeline/build.py` runs four times a day via GitHub Actions (`.github/workflows/daily.yml`). It:
  1. builds a directory of listed companies from Yahoo Finance's screener: every company in Indonesia, and companies worth more than about US$200 million on the other major markets;
  2. analyses a batch of them with `yfinance` (never-analysed companies first, largest first, then the ones whose analysis is oldest) and scores each out of 100;
  3. writes one file per company plus a compact index for the ranking and search.
- The hand-picked list in `pipeline/universe.py` (about 450 well-known dividend companies) is refreshed every day at 05:30 Jakarta time. Everything else is refreshed about weekly.
- `docs/` is the static site: no build step, no framework. All plain-language explanations live in `docs/explain.js`.
- The site and its data are published to the `gh-pages` branch, which is rebuilt from scratch on every run. Data is not committed to `main`.

## Adding a company to the daily list

Add its Yahoo Finance symbol to `pipeline/universe.py` (e.g. `ADMR.JK`). It is picked up on the next run.

## Running it yourself

- On GitHub: Actions → Update site → Run workflow
- Locally (Python 3.9+): `pip install -r pipeline/requirements.txt`, then `python pipeline/build.py BSSR.JK PTBA.JK` to analyse a few symbols, or `python pipeline/build.py --no-fetch` to rebuild the index from saved data.

Educational tool, not financial advice.
