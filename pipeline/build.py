"""Data job: keep a directory of listed companies, analyse them in batches, write JSON for the website.

Output (all under docs/data/, published to the gh-pages branch rather than committed to main):
  index.json               one compact row per company, for the ranking and search
  c/<SYMBOL>.json          full analysis of one company (metrics, history, score, flags)
  insights.json            what changed since the previous day
  history/YYYY-MM-DD.json  small daily snapshot (score, price, yield) used for comparisons
  directory.json           every listed company we know of, from Yahoo's screener
  state.json               when each company was last fetched
"""
import argparse
import datetime as dt
import json
import math
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from universe import BENCHMARK, all_tickers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data")
DETAIL = os.path.join(DATA, "c")
HIST = os.path.join(DATA, "history")
STATE = os.path.join(DATA, "state.json")
DIRECTORY = os.path.join(DATA, "directory.json")

TOP_N = 25
WORKERS = 4
REFRESH_DAYS = 7          # how often a company outside the hand-picked list is re-analysed
BIG_USD = 500e6           # the home page's top list only shows companies at least this large
MIN_MCAP_USD = 200e6      # smaller companies are left out of the directory (except Indonesia)
SITE_TZ = dt.timezone(dt.timedelta(hours=7))  # Jakarta: the site's "today"

FINANCIAL_SECTORS = {"Financial Services", "Real Estate"}

# Yahoo screener regions: (code, most companies to take, smallest market value in USD)
REGIONS = [
    ("id", 1500, 0),
    ("us", 5000, MIN_MCAP_USD), ("jp", 2500, MIN_MCAP_USD), ("cn", 3000, MIN_MCAP_USD),
    ("hk", 1500, MIN_MCAP_USD), ("in", 2500, MIN_MCAP_USD), ("gb", 1000, MIN_MCAP_USD),
    ("ca", 1000, MIN_MCAP_USD), ("au", 1000, MIN_MCAP_USD), ("kr", 1200, MIN_MCAP_USD),
    ("tw", 1200, MIN_MCAP_USD), ("sg", 400, MIN_MCAP_USD), ("my", 600, MIN_MCAP_USD),
    ("th", 600, MIN_MCAP_USD), ("ph", 250, MIN_MCAP_USD), ("vn", 400, MIN_MCAP_USD),
    ("fr", 500, MIN_MCAP_USD), ("ch", 300, MIN_MCAP_USD), ("nl", 200, MIN_MCAP_USD),
    ("es", 200, MIN_MCAP_USD), ("it", 400, MIN_MCAP_USD), ("se", 500, MIN_MCAP_USD),
    ("no", 300, MIN_MCAP_USD), ("dk", 200, MIN_MCAP_USD), ("fi", 200, MIN_MCAP_USD),
    ("be", 150, MIN_MCAP_USD), ("pt", 60, MIN_MCAP_USD), ("ie", 60, MIN_MCAP_USD),
    ("gr", 150, MIN_MCAP_USD), ("pl", 300, MIN_MCAP_USD), ("tr", 400, MIN_MCAP_USD),
    ("il", 400, MIN_MCAP_USD), ("sa", 300, MIN_MCAP_USD), ("ae", 150, MIN_MCAP_USD),
    ("qa", 60, MIN_MCAP_USD), ("kw", 150, MIN_MCAP_USD), ("eg", 150, MIN_MCAP_USD),
    ("za", 300, MIN_MCAP_USD), ("nz", 150, MIN_MCAP_USD),
    ("pk", 100, MIN_MCAP_USD),
    # These exchanges also list thousands of foreign companies, so they go last and the
    # duplicate check below keeps each company's busiest listing.
    ("br", 3000, MIN_MCAP_USD), ("de", 4000, MIN_MCAP_USD), ("at", 2000, MIN_MCAP_USD),
    ("mx", 1500, MIN_MCAP_USD), ("cl", 500, MIN_MCAP_USD), ("ar", 1000, MIN_MCAP_USD),
]
# Only these exchanges within a region (US: no over-the-counter market; Germany: Xetra only)
EXCHANGES = {"us": ["NMS", "NYQ", "NGM", "NCM", "ASE"], "de": ["GER"]}

# Venues that only carry copies of shares listed elsewhere (Cboe Canada, Cboe UK, London IOB)
SKIP_SUFFIXES = (".NE", ".XC", ".IL")

# Currencies Yahoo quotes in hundredths
MINOR = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}


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


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def safe(sym):
    """Symbols double as file names, so only plain ones are accepted."""
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9.\-_&]*$", sym or ""))


def to_usd(amount, ccy, fx, minor=True):
    """Convert to US dollars. fx maps currency -> units per dollar. Share prices come in minor
    units for some currencies (pence), market values don't."""
    if amount is None or not ccy:
        return None
    base, div = MINOR.get(ccy, (ccy, 1))
    rate = fx.get(base)
    if not rate:
        return None
    return amount / (div if minor else 1) / rate


def load_fx(previous):
    try:
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=30) as r:
            rates = json.load(r)["rates"]
        if rates.get("IDR"):
            return rates
    except Exception as e:
        print("exchange rates unavailable (%s); using the previous ones" % e, flush=True)
    return previous or {}


# ---------- directory of listed companies ----------

NAME_NOISE = {"inc", "corp", "corporation", "ltd", "limited", "plc", "sa", "ag", "se", "nv", "tbk", "pt",
              "co", "company", "the", "spa", "asa", "ab", "oyj", "as", "adr", "class", "a", "b"}


def name_key(name):
    words = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
    return " ".join(w for w in words if w not in NAME_NOISE) or name.lower()


def screen_region(region, limit, min_usd, fx):
    import yfinance as yf
    from yfinance import EquityQuery

    ops = [EquityQuery("eq", ["region", region]), EquityQuery("gt", ["intradaymarketcap", 0])]
    exchanges = EXCHANGES.get(region)
    if exchanges and len(exchanges) == 1:
        ops.append(EquityQuery("eq", ["exchange", exchanges[0]]))
    elif exchanges:
        ops.append(EquityQuery("or", [EquityQuery("eq", ["exchange", x]) for x in exchanges]))
    query = EquityQuery("and", ops)
    if not fx:
        min_usd = 0

    out, offset = [], 0
    while offset < limit:
        res, err = None, None
        for attempt in range(3):
            try:
                res = yf.screen(query, offset=offset, size=250, sortField="intradaymarketcap", sortAsc=False)
                break
            except Exception as e:
                err = e
                time.sleep(3 + attempt * 5)
        if res is None:
            print("  screener %s stopped at %d: %s" % (region, offset, err), flush=True)
            break
        quotes = res.get("quotes") or []
        if not quotes:
            break
        kept = 0
        for q in quotes:
            sym, name = q.get("symbol"), q.get("longName") or q.get("shortName")
            if not safe(sym) or not name or sym.endswith(SKIP_SUFFIXES):
                continue
            ccy = q.get("currency")
            mcap = to_usd(num(q.get("marketCap")), ccy, fx, minor=False)
            if min_usd and (mcap is None or mcap < min_usd):
                continue
            traded = (num(q.get("averageDailyVolume3Month")) or 0) * (to_usd(num(q.get("regularMarketPrice")), ccy, fx) or 0)
            out.append({"t": sym, "name": name, "exch": q.get("fullExchangeName") or q.get("exchange"),
                        "mcap": mcap, "vol": traded, "region": region})
            kept += 1
        if kept == 0 and min_usd:  # sorted largest first, so everything further down is smaller
            break
        offset += 250
        time.sleep(0.4)
    return out


def build_directory(fx, curated, previous):
    found = {}
    for region, limit, min_usd in REGIONS:
        try:
            rows = screen_region(region, limit, min_usd, fx)
        except Exception as e:
            print("  screener %s failed: %s" % (region, e), flush=True)
            rows = []
        if not rows:  # keep what we knew about this market rather than dropping it
            rows = [r for r in previous.values() if r.get("region") == region]
        print("directory %s: %d companies" % (region, len(rows)), flush=True)
        for r in rows:
            found.setdefault(r["t"], r)

    # The same company is often listed on several exchanges; keep its busiest listing.
    best = {}
    for r in found.values():
        k = name_key(r["name"])
        cur = best.get(k)
        if cur is None or (r["vol"], r["mcap"] or 0) > (cur["vol"], cur["mcap"] or 0):
            best[k] = r
    keep = {r["t"] for r in best.values()}
    return {t: r for t, r in found.items() if t in keep or t in curated}


# ---------- fetch ----------

def fetch(sym, fx):
    import pandas as pd
    import yfinance as yf

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
    # Yahoo's dividend history is sometimes not adjusted for splits (e.g. 8766.T), so if our figure is
    # far above either of Yahoo's own yields (one of which can share
    # the split error), trust the lower one. Those yields are themselves near zero for companies
    # that report in another currency than they trade in (e.g. BSSR.JK), so ignore tiny ones.
    refs = [num(info.get("trailingAnnualDividendYield")), (num(info.get("dividendYield")) or 0) / 100]
    refs = [r for r in refs if r and r >= 0.005]
    if refs and div_yield > min(refs) * 1.8 and div_yield - min(refs) > 0.03:
        div_yield = min(refs)
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

    # Free cash flow means little for banks and property firms, so no coverage figure for them
    coverage = None
    if fcf and div_paid and div_paid[-1][1] and (info.get("sector") or "") not in FINANCIAL_SECTORS:
        coverage = fcf[-1][1] / abs(div_paid[-1][1])

    de = num(info.get("debtToEquity"))
    de = de / 100 if de is not None else None
    cash, debt = num(info.get("totalCash")), num(info.get("totalDebt"))

    ex_div = info.get("exDividendDate")
    ex_div = dt.datetime.fromtimestamp(ex_div, dt.timezone.utc).strftime("%Y-%m-%d") if isinstance(ex_div, (int, float)) else None

    five_avg = num(info.get("fiveYearAvgDividendYield"))
    summary = (info.get("longBusinessSummary") or "").strip()
    mcap = num(info.get("marketCap"))

    return {
        "t": sym,
        "name": info.get("longName") or info.get("shortName"),
        "country": info.get("country"),
        "exch": info.get("fullExchangeName") or info.get("exchange"),
        "sector": info.get("sector") or "Other",
        "industry": info.get("industry"),
        "ccy": info.get("currency"),
        "fccy": info.get("financialCurrency") or info.get("currency"),
        "price": price,
        "mcap": mcap,
        "mcapUsd": to_usd(mcap, info.get("currency"), fx, minor=False),
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


def nets_of(h):
    return [v for _, v in h["net"]]


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

    profit_drop = len(nets_of(h)) >= 2 and nets_of(h)[-2] > 0 and nets_of(h)[-1] < nets_of(h)[-2] * 0.7
    profit_shrinking = len(nets_of(h)) >= 3 and nets_of(h)[-1] < max(nets_of(h)) * 0.5

    # 5. Steadiness (15): profitable years + dividend years
    nets = [v for _, v in h["net"]]
    prof = (sum(1 for v in nets if v > 0) / len(nets) * 8) if nets else 0
    last5 = {y: v for y, v in c["divYears"]}
    yrs = range(dt.date.today().year - 5, dt.date.today().year)
    divs = sum(1 for y in yrs if last5.get(y, 0) > 0) / 5 * 7
    steady = prof + divs
    if c["divChange"] is not None and c["divChange"] < -0.3:
        steady -= 3
    if profit_drop or profit_shrinking:
        steady -= 4
    parts["steady"] = max(0, round(steady))

    # Warning signs pull their part of the score down instead of only showing as notes
    if m["payout"] is not None and m["payout"] > 1:
        parts["cover"] = min(parts["cover"], 8)
    if m["yield"] > 0.15:
        parts["yield"] = min(parts["yield"], 18)

    # 6. Price (10): what you pay for each unit of profit
    pe = m["pe"]
    parts["value"] = 0 if pe is None or pe <= 0 else 10 if pe <= 8 else 7 if pe <= 12 else 4 if pe <= 18 else 1 if pe <= 25 else 0

    total = sum(parts.values())

    flags = []
    if m["payout"] is not None and m["payout"] > 1:
        flags.append("payout_over_100")
    if h["fcf"] and h["fcf"][-1][1] < 0 and not fin:
        flags.append("fcf_negative")
    if nets and nets[-1] < 0:
        flags.append("loss")
    if c["divChange"] is not None and c["divChange"] < -0.3:
        flags.append("div_cut")
    if m["yield"] > 0.15:
        flags.append("yield_trap")
    if profit_drop:
        flags.append("profit_drop")
    elif profit_shrinking:
        flags.append("profit_shrinking")
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


# ---------- choosing and fetching a batch ----------

def pick(curated, directory, state, today, budget, daily):
    """Hand-picked companies first (all of them on the daily run, otherwise only new ones), then
    companies never analysed (largest first), then the ones whose analysis is oldest."""
    curated_set = set(curated)
    first = [s for s in curated if daily or s not in state]
    new, due = [], []
    for sym, d in directory.items():
        if sym in curated_set:
            continue
        st = state.get(sym)
        if not st or (not st.get("u") and st.get("f", 0) < 3):
            new.append((-(d.get("mcap") or 0), sym))
            continue
        last = st.get("u") or st.get("t") or ""
        wait = 30 if st.get("f", 0) >= 3 else REFRESH_DAYS
        if last <= (today - dt.timedelta(days=wait)).isoformat():
            due.append((last, sym))
    queue = [s for _, s in sorted(new)] + [s for _, s in sorted(due)]
    return first + queue[:budget]


class Skipped(Exception):
    pass


def run_fetch(todo, fx, state, today, minutes):
    iso = today.isoformat()
    started = time.time()
    ctl = {"stop": False, "pause_until": 0}
    recent, pauses = [], 0

    def work(sym):
        while time.time() < ctl["pause_until"]:
            time.sleep(1)
        if ctl["stop"] or time.time() - started > minutes * 60:
            raise Skipped()
        time.sleep(0.3)
        return score(fetch(sym, fx))

    def batch(symbols, workers):
        nonlocal recent, pauses
        done, errors = 0, {}
        with ThreadPoolExecutor(workers) as pool:
            futures = {pool.submit(work, s): s for s in symbols}
            for i, fut in enumerate(as_completed(futures), 1):
                sym = futures[fut]
                try:
                    c = fut.result()
                except Skipped:
                    continue
                except Exception as e:
                    errors[sym] = str(e)
                    recent.append(0)
                else:
                    done += 1
                    recent.append(1)
                    c["updated"] = iso
                    save_json(os.path.join(DETAIL, sym + ".json"), c)
                    state[sym] = {"u": iso, "t": iso, "f": 0}
                # Yahoo throttling shows up as a run of failures: wait, and give up after the third time
                recent = recent[-80:]
                if len(recent) == 80 and sum(recent) < 20:
                    pauses += 1
                    recent = []
                    if pauses >= 3:
                        ctl["stop"] = True
                        print("Too many failures; stopping this batch early.", flush=True)
                    else:
                        print("Many failures; pausing for 2 minutes.", flush=True)
                        ctl["pause_until"] = time.time() + 120
                if i % 100 == 0:
                    print("%d/%d done (%d ok, %d failed)" % (i, len(symbols), done, len(errors)), flush=True)
        return done, errors

    ok, errors = batch(todo, WORKERS)
    if errors and not ctl["stop"]:
        # Most failures are brief throttling, so give them a second, gentler try
        print("retrying %d that failed" % len(errors), flush=True)
        time.sleep(60)
        recent = []
        again, errors = batch(list(errors), 2)
        ok += again
    for n, (sym, msg) in enumerate(errors.items()):
        st = state.setdefault(sym, {})
        st["t"] = iso
        st["f"] = st.get("f", 0) + 1
        if n < 40:
            print("  fail %s: %s" % (sym, msg), flush=True)
    print("fetched %d, failed %d, of %d planned" % (ok, len(errors), len(todo)), flush=True)


# ---------- index and insights ----------

INDEX_COLS = ["t", "name", "country", "sector", "industry", "score", "yield", "pe", "streak", "flags",
              "mcap", "big", "exch", "na"]


def build_index(curated_set, directory, state, today):
    """Returns (index rows, slim records of analysed companies for the insights)."""
    known = set(directory) | curated_set
    stale_cutoff = (today - dt.timedelta(days=30)).isoformat()
    analysed, slim = [], []
    for fname in os.listdir(DETAIL):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(DETAIL, fname)
        c = load_json(path)
        if not c or c.get("score") is None:
            continue
        sym = c["t"]
        if sym not in known and c.get("updated", "") < stale_cutoff:
            os.remove(path)  # no longer listed, or fell out of the directory
            continue
        d = directory.get(sym) or {}
        usd = c.get("mcapUsd") or d.get("mcap")
        big = sym in curated_set or (usd or 0) >= BIG_USD
        pe = c["m"].get("pe")
        analysed.append([sym, c.get("name") or d.get("name") or sym, c.get("country"), c.get("sector"),
                         c.get("industry"), c["score"], round(c["m"]["yield"], 4),
                         round(pe, 1) if pe is not None else None, c.get("streak") or 0, c.get("flags") or [],
                         int(usd / 1e6) if usd else None, 1 if big else 0, c.get("exch") or d.get("exch"), 0])
        slim.append({"t": sym, "score": c["score"], "price": c.get("price"), "yield": c["m"]["yield"],
                     "exDiv": c.get("exDiv"), "big": big, "mcap": usd or 0})
    done = {r[0] for r in analysed}
    pending = []
    for sym, d in directory.items():
        if sym in done:
            continue
        no_data = 1 if state.get(sym, {}).get("f", 0) >= 3 else 0
        pending.append([sym, d["name"], None, None, None, None, None, None, None, None,
                        int(d["mcap"] / 1e6) if d.get("mcap") else None, 0, d.get("exch"), no_data])
    analysed.sort(key=lambda r: (-r[5], -(r[10] or 0)))
    pending.sort(key=lambda r: -(r[10] or 0))
    slim.sort(key=lambda c: (-c["score"], -int(c["mcap"] / 1e6)))
    return analysed + pending, slim


def snapshot_before(today, days):
    """Most recent snapshot at least `days` days old."""
    cutoff = (today - dt.timedelta(days=days)).isoformat()
    names = sorted(n for n in os.listdir(HIST) if n.endswith(".json") and n[:-5] <= cutoff)
    return load_json(os.path.join(HIST, names[-1])) if names else None


def build_insights(companies, today):
    """companies: slim records sorted best first. The feed only covers larger companies."""
    top = [c["t"] for c in companies if c["big"]][:TOP_N]
    by_t = {c["t"]: c for c in companies}
    out = []

    prev = snapshot_before(today, 1)
    if prev:
        prev_top = set(prev.get("top", []))
        for sym in top:
            # a company analysed for the first time hasn't "joined" anything
            if prev_top and sym not in prev_top and sym in prev.get("c", {}):
                out.append({"kind": "new_top", "t": sym, "score": by_t[sym]["score"]})
        for sym in prev_top - set(top):
            if sym in by_t:
                out.append({"kind": "left_top", "t": sym, "score": by_t[sym]["score"]})
        for sym, old in prev.get("c", {}).items():
            c = by_t.get(sym)
            if c and c["big"] and abs(c["score"] - old["s"]) >= 5 and max(c["score"], old["s"]) >= 65:
                out.append({"kind": "score_change", "t": sym, "from": old["s"], "to": c["score"]})

    week = snapshot_before(today, 7)
    if week:
        for sym, old in week.get("c", {}).items():
            c = by_t.get(sym)
            if c and c["big"] and c["score"] >= 60 and old.get("p") and c["price"]:
                chg = c["price"] / old["p"] - 1
                if chg <= -0.07:
                    out.append({"kind": "price_drop", "t": sym, "chg": round(chg, 4), "yield": c["yield"]})

    horizon = (today + dt.timedelta(days=21)).isoformat()
    deadlines = [c for c in companies
                 if c["big"] and c["score"] >= 65 and c["exDiv"] and today.isoformat() < c["exDiv"] <= horizon]
    for c in sorted(deadlines, key=lambda c: c["exDiv"])[:40]:
        out.append({"kind": "ex_div", "t": c["t"], "date": c["exDiv"], "yield": c["yield"]})

    snap = {
        "date": today.isoformat(),
        "top": top,
        "c": {c["t"]: {"s": c["score"], "p": c["price"], "y": c["yield"]} for c in companies},
    }
    return out, snap


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("symbols", nargs="*", help="only analyse these symbols")
    ap.add_argument("--budget", type=int, default=3000, help="companies to analyse besides the hand-picked list")
    ap.add_argument("--minutes", type=int, default=80, help="stop fetching after this long")
    ap.add_argument("--daily", action="store_true", help="also refresh every hand-picked company")
    ap.add_argument("--no-fetch", action="store_true", help="only rebuild the index from what is already saved")
    args = ap.parse_args()

    os.makedirs(DETAIL, exist_ok=True)
    os.makedirs(HIST, exist_ok=True)
    today = dt.datetime.now(SITE_TZ).date()
    curated = all_tickers()
    curated_set = set(curated)
    state = load_json(STATE) or {}
    saved = load_json(DIRECTORY) or {}
    fx, directory = saved.get("fx") or {}, saved.get("c") or {}

    if not args.no_fetch:
        fx = load_fx(fx)
        if not args.symbols and saved.get("date") != today.isoformat():
            fresh = build_directory(fx, curated_set, directory)
            if fresh and len(fresh) >= 0.6 * len(directory):
                directory = fresh
                save_json(DIRECTORY, {"date": today.isoformat(), "fx": fx, "c": directory})
            else:
                print("directory refresh looks incomplete (%d companies); keeping the previous one" % len(fresh))
        print("directory: %d companies" % len(directory), flush=True)
        todo = [s for s in args.symbols if safe(s)] or pick(curated, directory, state, today, args.budget, args.daily)
        run_fetch(todo, fx, state, today, args.minutes)
        save_json(STATE, state)

    rows, slim = build_index(curated_set, directory, state, today)
    insights, snap = build_insights(slim, today)
    save_json(os.path.join(DATA, "index.json"), {
        "date": today.isoformat(), "benchmark": BENCHMARK, "cols": INDEX_COLS, "rows": rows,
        "analysed": len(slim), "minMcapUsd": MIN_MCAP_USD, "bigUsd": BIG_USD,
    })
    save_json(os.path.join(DATA, "insights.json"), {"date": today.isoformat(), "items": insights})
    save_json(os.path.join(HIST, today.isoformat() + ".json"), snap)
    for old in sorted(n for n in os.listdir(HIST) if n.endswith(".json"))[:-10]:
        os.remove(os.path.join(HIST, old))
    print("index: %d companies, %d analysed" % (len(rows), len(slim)), flush=True)


if __name__ == "__main__":
    main()
