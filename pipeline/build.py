"""Daily data job: fetch company data, score it, write JSON for the website.

Output (all under docs/data/):
  latest.json              every company with metrics, history, score, flags
  insights.json            what changed since the previous run
  history/YYYY-MM-DD.json  small daily snapshot (score, price, yield) used for comparisons
"""
import datetime as dt
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

from universe import BENCHMARK, all_tickers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
HIST = os.path.join(DATA, "history")
TOP_N = 25
WORKERS = 4

FINANCIAL_SECTORS = {"Financial Services", "Real Estate"}


# ---------- helpers ----------

def num(x):
    """Return a plain float, or None for missing/NaN/inf values."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def row(df, *names):
    """Annual values for the first matching statement row, oldest year first."""
    if df is None or df.empty:
        return []
    for name in names:
        if name in df.index:
            s = df.loc[name]
            pairs = [(c.year, num(v)) for c, v in s.items()]
            return sorted(p for p in pairs if p[1] is not None)
    return []


def get_info(t, tries=3):
    for i in range(tries):
        try:
            info = t.info
            if info and (info.get("longName") or info.get("shortName")):
                return info
        except Exception:
            pass
        time.sleep(2 + i * 3)
    return None


# ---------- fetch ----------

def fetch(sym):
    t = yf.Ticker(sym)
    info = get_info(t)
    if not info:
        raise RuntimeError("no info")

    price = num(info.get("currentPrice")) or num(info.get("regularMarketPrice")) or num(info.get("previousClose"))
    if not price:
        raise RuntimeError("no price")

    divs = t.dividends
    cf = t.cashflow
    inc = t.income_stmt

    today = pd.Timestamp.now(tz="UTC")
    this_year = today.year

    # Dividends per share: trailing 12 months and per calendar year
    ttm_div, div_years = 0.0, []
    if divs is not None and not divs.empty:
        idx = divs.index.tz_convert("UTC") if divs.index.tz is not None else divs.index.tz_localize("UTC")
        divs = pd.Series(divs.values, index=idx)
        ttm_div = float(divs[divs.index > today - pd.Timedelta(days=365)].sum())
        by_year = divs.groupby(divs.index.year).sum()
        div_years = [[int(y), round(float(v), 6)] for y, v in by_year.items() if y >= this_year - 10]

    div_yield = ttm_div / price if ttm_div else 0.0
    if div_yield > 0.5:  # currency mismatch in Yahoo data; fall back to Yahoo's own figure
        div_yield = num(info.get("trailingAnnualDividendYield")) or 0.0
        if div_yield > 0.5:
            div_yield = 0.0

    # Streak of consecutive calendar years with a dividend, ending last year (max 10: the history kept)
    paid = {y for y, v in div_years if v > 0}
    streak, y = 0, this_year - 1
    while y in paid:
        streak += 1
        y -= 1

    last_full = dict(div_years)
    div_change = None
    a, b = last_full.get(this_year - 1), last_full.get(this_year - 2)
    if a is not None and b:
        div_change = a / b - 1

    revenue = row(inc, "Total Revenue", "Operating Revenue")
    net = row(inc, "Net Income Common Stockholders", "Net Income")
    fcf = row(cf, "Free Cash Flow")
    div_paid = row(cf, "Cash Dividends Paid", "Common Stock Dividend Paid")

    coverage = None
    if fcf and div_paid and div_paid[-1][1]:
        coverage = fcf[-1][1] / abs(div_paid[-1][1])

    de = num(info.get("debtToEquity"))
    de = de / 100 if de is not None else None
    cash, debt = num(info.get("totalCash")), num(info.get("totalDebt"))

    ex_div = info.get("exDividendDate")
    ex_div = dt.datetime.fromtimestamp(ex_div, dt.timezone.utc).strftime("%Y-%m-%d") if isinstance(ex_div, (int, float)) else None

    five_avg = num(info.get("fiveYearAvgDividendYield"))
    summary = (info.get("longBusinessSummary") or "").strip()

    return {
        "t": sym,
        "name": info.get("longName") or info.get("shortName"),
        "country": info.get("country"),
        "sector": info.get("sector") or "Other",
        "industry": info.get("industry"),
        "ccy": info.get("currency"),
        "fccy": info.get("financialCurrency") or info.get("currency"),
        "price": price,
        "mcap": num(info.get("marketCap")),
        "hi52": num(info.get("fiftyTwoWeekHigh")),
        "lo52": num(info.get("fiftyTwoWeekLow")),
        "summary": summary[:700] + ("…" if len(summary) > 700 else ""),
        "web": info.get("website"),
        "m": {
            "yield": round(div_yield, 5),
            "avg5": five_avg / 100 if five_avg is not None else None,  # Yahoo gives percent
            "payout": num(info.get("payoutRatio")),
            "coverage": coverage,
            "de": de,
            "cash": cash,
            "debt": debt,
            "roe": num(info.get("returnOnEquity")),
            "margin": num(info.get("profitMargins")),
            "pe": num(info.get("trailingPE")),
            "revGrowth": num(info.get("revenueGrowth")),
            "current": num(info.get("currentRatio")),
        },
        "hist": {"revenue": revenue, "net": net, "fcf": fcf, "divPaid": div_paid},
        "divYears": div_years,
        "streak": streak,
        "divChange": div_change,
        "exDiv": ex_div,
    }


# ---------- scoring ----------

def tier(value, steps):
    """steps: list of (threshold, points) checked in order with value >= threshold."""
    if value is None:
        return 0
    for threshold, pts in steps:
        if value >= threshold:
            return pts
    return 0


def score(c):
    m, h = c["m"], c["hist"]
    fin = c["sector"] in FINANCIAL_SECTORS
    parts = {}

    # 1. Dividend yield (25)
    parts["yield"] = tier(m["yield"], [(0.08, 25), (0.06, 21), (0.045, 16), (0.03, 10), (0.015, 4)])

    # 2. Is the dividend paid from real cash? (20)
    if m["coverage"] is not None and not fin:
        parts["cover"] = tier(m["coverage"], [(1.5, 20), (1.15, 15), (0.9, 8)])
    elif m["payout"] is not None and m["payout"] > 0:
        p = m["payout"]
        parts["cover"] = 20 if p <= 0.6 else 14 if p <= 0.8 else 6 if p <= 1.0 else 0
    else:
        parts["cover"] = 0

    # 3. Debt (15). Banks and property firms borrow by design, so they get a neutral score.
    if fin:
        parts["debt"] = 8
    elif m["cash"] is not None and m["debt"] is not None and m["cash"] >= m["debt"]:
        parts["debt"] = 15
    elif m["de"] is not None:
        d = m["de"]
        parts["debt"] = 12 if d < 0.3 else 8 if d < 0.7 else 4 if d < 1.2 else 0
    else:
        parts["debt"] = 0

    # 4. Profitability (15)
    parts["profit"] = tier(m["roe"], [(0.2, 15), (0.12, 10), (0.06, 5)])

    # 5. Steadiness (15): profitable years + dividend years
    nets = [v for _, v in h["net"]]
    prof = (sum(1 for v in nets if v > 0) / len(nets) * 8) if nets else 0
    last5 = {y: v for y, v in c["divYears"]}
    yrs = range(dt.date.today().year - 5, dt.date.today().year)
    divs = sum(1 for y in yrs if last5.get(y, 0) > 0) / 5 * 7
    steady = prof + divs
    if c["divChange"] is not None and c["divChange"] < -0.3:
        steady -= 3
    parts["steady"] = max(0, round(steady))

    # 6. Price (10): what you pay for each unit of profit
    pe = m["pe"]
    parts["value"] = 0 if pe is None or pe <= 0 else 10 if pe <= 8 else 7 if pe <= 12 else 4 if pe <= 18 else 1 if pe <= 25 else 0

    total = sum(parts.values())

    flags = []
    if m["payout"] is not None and m["payout"] > 1:
        flags.append("payout_over_100")
    if h["fcf"] and h["fcf"][-1][1] < 0:
        flags.append("fcf_negative")
    if nets and nets[-1] < 0:
        flags.append("loss")
    if c["divChange"] is not None and c["divChange"] < -0.3:
        flags.append("div_cut")
    if m["yield"] > 0.15:
        flags.append("yield_trap")
    if len(nets) >= 2 and nets[-2] > 0 and nets[-1] < nets[-2] * 0.7:
        flags.append("profit_drop")
    if not fin and m["de"] is not None and m["de"] > 1.5:
        flags.append("high_debt")
    if not fin and parts["debt"] == 15:
        flags.append("net_cash")
    if c["streak"] >= 10:
        flags.append("streak10")
    if m["coverage"] is not None and m["coverage"] >= 1.5 and not fin:
        flags.append("covered")

    # Hard caps: a company losing money or paying out far more than it earns can't rank as "healthy"
    if "loss" in flags or (m["payout"] or 0) > 1.5:
        total = min(total, 40)

    c["score"] = int(round(total))
    c["parts"] = parts
    c["flags"] = flags
    return c


# ---------- insights ----------

def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def snapshot_before(today, days):
    """Most recent snapshot at least `days` days old."""
    if not os.path.isdir(HIST):
        return None
    cutoff = (today - dt.timedelta(days=days)).isoformat()
    names = sorted(n for n in os.listdir(HIST) if n.endswith(".json") and n[:-5] <= cutoff)
    return load_json(os.path.join(HIST, names[-1])) if names else None


def build_insights(companies, today):
    ranked = sorted((c for c in companies if not c.get("stale")), key=lambda c: -c["score"])
    top = [c["t"] for c in ranked[:TOP_N]]
    by_t = {c["t"]: c for c in companies}
    out = []

    prev = snapshot_before(today, 1)
    if prev:
        prev_top = set(prev.get("top", []))
        for sym in top:
            if prev_top and sym not in prev_top:
                out.append({"kind": "new_top", "t": sym, "score": by_t[sym]["score"]})
        for sym in prev_top - set(top):
            if sym in by_t:
                out.append({"kind": "left_top", "t": sym, "score": by_t[sym]["score"]})
        for sym, old in prev.get("c", {}).items():
            c = by_t.get(sym)
            if c and not c.get("stale") and abs(c["score"] - old["s"]) >= 5:
                out.append({"kind": "score_change", "t": sym, "from": old["s"], "to": c["score"]})

    week = snapshot_before(today, 7)
    if week:
        for sym, old in week.get("c", {}).items():
            c = by_t.get(sym)
            if c and not c.get("stale") and c["score"] >= 60 and old.get("p"):
                chg = c["price"] / old["p"] - 1
                if chg <= -0.07:
                    out.append({"kind": "price_drop", "t": sym, "chg": round(chg, 4), "yield": c["m"]["yield"]})

    horizon = (today + dt.timedelta(days=21)).isoformat()
    for c in companies:
        if c.get("exDiv") and today.isoformat() <= c["exDiv"] <= horizon and c["score"] >= 55:
            out.append({"kind": "ex_div", "t": c["t"], "date": c["exDiv"], "yield": c["m"]["yield"]})

    snap = {
        "date": today.isoformat(),
        "top": top,
        "c": {c["t"]: {"s": c["score"], "p": c["price"], "y": c["m"]["yield"]} for c in companies if not c.get("stale")},
    }
    return out, snap


# ---------- main ----------

def main():
    os.makedirs(HIST, exist_ok=True)
    today = dt.date.today()
    symbols = all_tickers()
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]

    old = {c["t"]: c for c in (load_json(os.path.join(DATA, "latest.json")) or {}).get("companies", [])}
    results, failed = {}, []

    def work(sym):
        time.sleep(0.3)
        return score(fetch(sym))

    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(work, s): s for s in symbols}
        for i, fut in enumerate(as_completed(futures), 1):
            sym = futures[fut]
            try:
                c = fut.result()
                c["updated"] = today.isoformat()
                results[sym] = c
            except Exception as e:
                failed.append(sym)
                if sym in old:  # keep yesterday's data rather than dropping the company
                    stale = old[sym]
                    stale["stale"] = True
                    results[sym] = stale
                print("  fail %s: %s" % (sym, e), flush=True)
            if i % 25 == 0:
                print("%d/%d done" % (i, len(symbols)), flush=True)

    companies = sorted(results.values(), key=lambda c: -c["score"])
    fresh = sum(1 for c in companies if not c.get("stale"))
    print("fetched %d fresh, %d failed" % (fresh, len(failed)))
    if fresh < len(symbols) * 0.3:
        sys.exit("Too many failures (Yahoo may be blocking); keeping previous data.")

    insights, snap = build_insights(companies, today)

    with open(os.path.join(DATA, "latest.json"), "w", encoding="utf-8") as f:
        json.dump({"date": today.isoformat(), "benchmark": BENCHMARK, "failed": failed, "companies": companies},
                  f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(DATA, "insights.json"), "w", encoding="utf-8") as f:
        json.dump({"date": today.isoformat(), "items": insights}, f, separators=(",", ":"))
    with open(os.path.join(HIST, today.isoformat() + ".json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
