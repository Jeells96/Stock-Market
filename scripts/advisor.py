#!/usr/bin/env python3
"""
Autonomous Investment Advisor engine for Jaren's Stock Market Tracker.

Runs unattended on GitHub Actions (see .github/workflows/advisor.yml).
Manages three simulated (paper-money) portfolios, each seeded with $10,000:

  aggressive  "Get Rich Quick"     — short-term momentum swings, hard stops
  growth      "Dependable Growth"  — trend-following quality large caps
  longterm    "Long-Term Success"  — diversified buy-and-hold ETF allocation

plus an SPY buy-and-hold benchmark for honest comparison.

Every run:
  1. Fetches ~1 year of daily bars (Yahoo Finance, no API key needed).
  2. Replays every trading day since the last run: credits dividends,
     applies splits, checks stops/targets/trend-breaks on daily CLOSES,
     and records a daily equity point for each portfolio.
  3. Fills empty slots with new ranked picks (entered at the latest close).
  4. Writes machine-readable state + a website payload + human reports.

The git commit that follows each run timestamps the picks BEFORE their
outcomes are known, which is what makes the track record verifiable.

Honest-accounting rules (kept deliberately simple and auditable):
  * All entries/exits happen at daily closing prices. No intraday fills.
  * Dividends are credited to cash on the ex-date. Splits adjust positions.
  * If a day's close breaches both stop and target logic, stops win.
  * Missed runs are replayed day by day — no lookahead, no cherry-picking.
"""

import json
import math
import os
import re
import shutil
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:  # allow bare-python fallback
    requests = None
    import urllib.request

# ---------------------------------------------------------------- paths

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
SITE_PATH = os.path.join(DATA_DIR, "site.json")
REPORT_PATH = os.path.join(REPO_ROOT, "REPORT.md")
README_PATH = os.path.join(REPO_ROOT, "README.md")
COMMIT_MSG_PATH = os.path.join(DATA_DIR, "commit_msg.txt")  # gitignored

STARTING_CAPITAL = 10_000.0
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
# Skip the (expensive, aggressively rate-limited) chart endpoint entirely and
# run on batched spark closes alone. Dividends/splits are then invisible for
# the run, but the per-position ledgers back-fill them on the next full run.
SPARK_ONLY = os.environ.get("ADVISOR_SPARK_ONLY", "") == "1"

# ---------------------------------------------------------------- universes

SP_CORE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "JPM", "V",
    "UNH", "XOM", "JNJ", "WMT", "MA", "PG", "LLY", "HD", "CVX", "ABBV",
    "KO", "PEP", "MRK", "AVGO", "COST", "BAC", "ADBE", "CRM", "TMO", "CSCO",
    "NFLX", "ABT", "MCD", "DHR", "WFC", "TXN", "NEE", "ORCL", "DIS", "AMD",
    "PM", "CMCSA", "VZ", "NKE", "UPS", "RTX", "PFE", "INTC", "HON", "INTU",
    "AMGN", "COP", "UNP", "LOW", "SPGI", "LIN", "CAT", "GS", "MS", "ISRG",
    "BLK", "NOW", "BKNG", "DE", "AXP", "SCHW", "TJX", "C", "BMY", "SYK",
    "MMC", "BA", "ELV", "GE", "MDT", "ADP", "PLD", "LMT", "REGN", "VRTX",
    "GILD", "BSX", "TMUS", "CI", "CB", "ZTS", "SO", "DUK", "MO", "PGR",
    "MDLZ", "SLB", "ETN", "ADI", "ICE", "CME", "SHW", "KLAC", "APD", "NSC",
    "HCA", "FCX", "WM", "BDX", "CL", "EOG", "FDX", "ITW", "CSX", "EQIX",
    "NOC", "MMM", "PNC", "TGT", "EMR", "MPC", "PSX", "AON", "ORLY", "MCK",
    "PSA", "ROP", "MSI", "USB", "CARR", "COF", "APH", "AJG", "TFC", "GD",
    "SRE", "NXPI", "AZO", "KMB", "MAR", "AIG", "WMB", "TT", "ECL", "WELL",
    "OKE", "KMI", "ROST", "TRV", "GIS", "ALL", "HLT", "STZ", "ANET", "FTNT",
    "SYY", "DOW", "KDP", "LRCX", "ABNB", "CTAS", "BK", "AFL", "NUE", "IDXX",
    "JCI", "VLO", "MET", "OXY", "PCAR", "SPG", "AMP", "PAYX", "EW", "TEL",
    "HSY", "CMG", "DXCM", "CTSH", "PRU", "DLR", "LEN", "DHI", "MNST", "CRWD",
    "DG", "KR", "YUM", "ADM", "EXC", "XEL", "FAST", "SBUX", "WEC", "ED",
    "ROK", "BKR", "HAL", "RSG", "VRSK", "TROW", "CDNS", "SNPS", "CDW", "GWW",
    "KEYS", "NDAQ", "NTAP", "STT", "RJF", "PHM", "HBAN", "RF", "CFG", "TSCO",
    "ULTA", "DRI", "PANW", "QCOM", "MU", "AMAT", "PYPL", "UBER", "SHOP",
]

MOMENTUM = [
    "GME", "AMC", "SOFI", "PLTR", "HOOD", "COIN", "MSTR", "MARA", "RIOT", "CLSK",
    "WULF", "HUT", "BITF", "CIFR", "HIVE", "ARM", "SMCI", "AI", "BBAI", "SOUN",
    "UPST", "LCID", "RIVN", "QS", "ENPH", "SEDG", "RUN", "FSLR", "ARRY", "BE",
    "PLUG", "STEM", "JKS", "CSIQ", "TSLA", "NVDA", "TSM", "INTC", "MU", "ON",
    "MCHP", "SWKS", "QRVO", "LSCC", "CRWD", "ZS", "NET", "SNOW", "DDOG", "MDB",
    "OKTA", "DOCN", "ESTC", "CFLT", "S", "TENB", "RPD", "CYBR", "VRNS", "FTNT",
    "PANW", "GTLB", "DT", "PCTY", "PAYC", "WDAY", "APP", "SE", "GRAB", "MELI",
    "NU", "STNE", "PAGS", "CART", "RDDT", "HIMS", "IONQ", "RGTI", "QBTS", "ASTS",
    "RKLB", "LUNR", "ACHR", "JOBY", "OSCR", "ROOT", "LMND", "AFRM", "DKNG", "CVNA",
    "CELH", "ELF", "DUOL", "TOST", "DASH", "ABNB", "UBER", "SHOP", "SQ", "PYPL",
    "ROKU", "TTD", "U", "PATH", "TWLO", "VRT", "NVO", "LLY", "VKTX", "SMR",
    "OKLO", "VST", "CEG", "NRG", "TLN", "GEV", "PWR", "MOD", "ANET", "AVGO",
]

LONGTERM_ALLOCATION = {
    # symbol: (target weight, role)
    "VOO":  (0.40, "S&P 500 core — owns the 500 biggest US companies"),
    "QQQ":  (0.20, "Nasdaq-100 — concentrated big-tech growth"),
    "SCHD": (0.20, "Dividend quality — cash-generating value companies"),
    "VXUS": (0.10, "International — diversification outside the US"),
    "BND":  (0.10, "Bonds — ballast that cushions stock drawdowns"),
}

BENCHMARK_SYMBOL = "SPY"

STRATEGY_META = {
    "aggressive": {
        "label": "Get Rich Quick",
        "icon": "🚀",
        "tagline": "High-octane momentum swings. Big targets, hard stops, fast exits.",
        "horizon": "Days to weeks",
        "risk_note": "Very high risk. Expect large swings and frequent losing trades — the bet is that winners outrun losers.",
        "slots": 5,
        "target_pct": 0.20,
        "stop_pct": 0.08,
        "max_hold_bars": 15,
    },
    "growth": {
        "label": "Dependable Growth",
        "icon": "📈",
        "tagline": "Quality large caps in confirmed uptrends. Steady compounding.",
        "horizon": "Weeks to months",
        "risk_note": "Moderate risk. Rides established trends and steps aside when a trend breaks.",
        "slots": 5,
        "target_pct": 0.15,
        "stop_pct": 0.10,
        "max_hold_bars": None,  # exits on trend break instead
    },
    "longterm": {
        "label": "Long-Term Success",
        "icon": "🏛️",
        "tagline": "Diversified ETF portfolio, rebalanced automatically. Time in the market.",
        "horizon": "Years",
        "risk_note": "Lower risk. Boring on purpose — broad diversification and compounding do the work.",
        "slots": len(LONGTERM_ALLOCATION),
    },
}

# aggressive won't re-enter a symbol for this many days after exiting it
COOLDOWN_DAYS = 7
# abort the run (leaving state untouched) if fetch coverage drops below this
MIN_FETCH_COVERAGE = 0.5

# ---------------------------------------------------------------- data fetch

YAHOO_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def http_get_json(url, timeout=20):
    if requests is not None:
        r = requests.get(url, headers=UA, timeout=timeout)
        r.raise_for_status()
        return r.json()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def yahoo_symbol(sym):
    return sym.replace(".", "-")


def fetch_history(sym, range_="1y"):
    """Fetch daily bars + div/split events for ONE symbol from Yahoo's chart API.

    Result: {"dates": [...], "close": {date: px}, "volume": {date: v},
             "name": str, "divs": {date: per_share}, "splits": {date: ratio}}
    Closes are split-adjusted raw closes (NOT dividend-adjusted) so that
    recorded entry prices stay comparable; dividends are handled as cash.

    Yahoo rate-limits burst traffic aggressively, so this retries with long
    backoff and host rotation. Use only for the small set of portfolio-critical
    symbols — the wide scanning universe goes through fetch_universe() instead.
    """
    q = urllib.parse.urlencode({"range": range_, "interval": "1d", "events": "div,split"})
    last_err = None
    backoffs = [4, 10, 20]
    for attempt in range(4):
        host = YAHOO_HOSTS[attempt % len(YAHOO_HOSTS)]
        url = f"{host}/v8/finance/chart/{urllib.parse.quote(yahoo_symbol(sym))}?{q}"
        try:
            j = http_get_json(url)
            result = (j.get("chart") or {}).get("result") or []
            if not result:
                return None
            r = result[0]
            ts = r.get("timestamp") or []
            quote = ((r.get("indicators") or {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []
            meta = r.get("meta") or {}
            tz = meta.get("exchangeTimezoneName") or "America/New_York"
            dates, close_map, vol_map = [], {}, {}
            for i, t in enumerate(ts):
                c = closes[i] if i < len(closes) else None
                if c is None or not isinstance(c, (int, float)) or c <= 0:
                    continue
                d = market_date(t, tz)
                if d not in close_map:
                    dates.append(d)
                close_map[d] = float(c)
                v = volumes[i] if i < len(volumes) else None
                vol_map[d] = float(v) if isinstance(v, (int, float)) else 0.0
            events = r.get("events") or {}
            divs = {}
            for ev in (events.get("dividends") or {}).values():
                amt = ev.get("amount")
                if isinstance(amt, (int, float)) and amt > 0:
                    divs[market_date(ev.get("date", 0), tz)] = float(amt)
            splits = {}
            for ev in (events.get("splits") or {}).values():
                num, den = ev.get("numerator"), ev.get("denominator")
                if isinstance(num, (int, float)) and isinstance(den, (int, float)) and den:
                    ratio = float(num) / float(den)
                    if ratio > 0:
                        splits[market_date(ev.get("date", 0), tz)] = ratio
            if not dates:
                return None
            return {
                "dates": dates,
                "close": close_map,
                "volume": vol_map,
                "name": meta.get("shortName") or meta.get("longName") or sym,
                "divs": divs,
                "splits": splits,
                "has_events": True,
            }
        except Exception as e:  # noqa: BLE001 — any fetch error → retry
            last_err = e
            if attempt < len(backoffs):
                wait = backoffs[attempt] if is_rate_limit(e) else 2.0 + attempt * 2.0
                time.sleep(wait)
    print(f"  ! fetch failed for {sym}: {last_err}")
    return None


def is_rate_limit(err):
    return "429" in str(err)


def market_date(unix_ts, tz_name):
    """Convert an exchange timestamp to the local trading date (YYYY-MM-DD)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(unix_ts, ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return datetime.fromtimestamp(unix_ts, timezone.utc).strftime("%Y-%m-%d")


def trim_partial_session(bars):
    """Yahoo's daily series includes the in-progress session (with the live
    price as its 'close') once trading starts. Scheduled runs are timed to
    avoid market hours, but GitHub cron can fire late and manual runs can
    happen any time — so before 16:10 ET, drop today's bar everywhere. All
    fills and marks must come from completed sessions only."""
    try:
        from zoneinfo import ZoneInfo
        now_ny = datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 — worst case assume EDT
        now_ny = datetime.now(timezone.utc) - timedelta(hours=4)
    if (now_ny.hour, now_ny.minute) >= (16, 10):
        return
    today_ny = now_ny.strftime("%Y-%m-%d")
    trimmed = 0
    for h in bars.values():
        if h["dates"] and h["dates"][-1] == today_ny:
            d = h["dates"].pop()
            h["close"].pop(d, None)
            h["volume"].pop(d, None)
            trimmed += 1
    if trimmed:
        print(f"  trimmed in-progress session bar ({today_ny}) from {trimmed} symbols")


def finnhub_quote(sym):
    """Fallback current quote (used only to sanity-check, never required)."""
    if not FINNHUB_KEY:
        return None
    try:
        j = http_get_json(
            f"https://finnhub.io/api/v1/quote?symbol={urllib.parse.quote(sym)}&token={FINNHUB_KEY}",
            timeout=10,
        )
        c = j.get("c")
        return float(c) if isinstance(c, (int, float)) and c > 0 else None
    except Exception:  # noqa: BLE001
        return None


def fetch_portfolio_bars(symbols):
    """Full-history fetches (with div/split events) for the small set of
    portfolio-critical symbols. If Yahoo's chart endpoint is rate-limiting,
    fall back to batched spark closes so the run can still mark portfolios —
    dividends/splits for those symbols are simply skipped until the chart
    endpoint recovers (ex-dates are quarterly, so overlap is rare)."""
    out = {}
    symbols = list(dict.fromkeys(symbols))
    if SPARK_ONLY:
        print(f"Fetching {len(symbols)} portfolio symbols (spark-only mode)…")
        for b in range(0, len(symbols), SPARK_BATCH):
            out.update(fetch_spark_batch(symbols[b:b + SPARK_BATCH]))
        print(f"  {len(out)}/{len(symbols)} ok")
        return out
    print(f"Fetching {len(symbols)} portfolio symbols (full history + events)…")
    fail_streak = 0
    for i, sym in enumerate(symbols):
        if fail_streak >= 3:
            print("  chart endpoint failing repeatedly — skipping straight to spark fallback")
            break
        h = fetch_history(sym)
        if h:
            out[sym] = h
            fail_streak = 0
        else:
            fail_streak += 1
        if i < len(symbols) - 1:
            time.sleep(1.2)
    missing = [s for s in symbols if s not in out]
    if missing:
        print(f"  chart endpoint gave {len(out)}/{len(symbols)} — spark fallback for {missing}")
        for b in range(0, len(missing), SPARK_BATCH):
            out.update(fetch_spark_batch(missing[b:b + SPARK_BATCH]))
    print(f"  {len(out)}/{len(symbols)} ok")
    return out


SPARK_BATCH = 20


def fetch_spark_batch(symbols):
    """One spark call: closes for up to SPARK_BATCH symbols. Returns {sym: hist}."""
    joined = ",".join(yahoo_symbol(s) for s in symbols)
    back = {yahoo_symbol(s): s for s in symbols}
    q = urllib.parse.urlencode({"symbols": joined, "range": "1y", "interval": "1d"})
    backoffs = [8, 25, 50]
    last_err = None
    for attempt in range(4):
        host = YAHOO_HOSTS[attempt % len(YAHOO_HOSTS)]
        try:
            j = http_get_json(f"{host}/v8/finance/spark?{q}", timeout=30)
            out = {}
            for ysym, payload in (j or {}).items():
                sym = back.get(ysym, ysym)
                ts = (payload or {}).get("timestamp") or []
                closes = (payload or {}).get("close") or []
                dates, close_map = [], {}
                for i, t in enumerate(ts):
                    c = closes[i] if i < len(closes) else None
                    if c is None or not isinstance(c, (int, float)) or c <= 0:
                        continue
                    d = market_date(t, "America/New_York")
                    if d not in close_map:
                        dates.append(d)
                    close_map[d] = float(c)
                if dates:
                    out[sym] = {"dates": dates, "close": close_map, "volume": {},
                                "name": sym, "divs": {}, "splits": {},
                                "has_events": False}
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < len(backoffs):
                wait = backoffs[attempt] if is_rate_limit(e) else 3.0
                time.sleep(wait)
    print(f"  ! spark batch failed ({symbols[0]}…): {last_err}")
    return {}


def fetch_universe(symbols):
    """Batched close-only histories for the wide scanning universe."""
    symbols = list(dict.fromkeys(symbols))
    out = {}
    n_batches = math.ceil(len(symbols) / SPARK_BATCH)
    print(f"Fetching {len(symbols)} universe symbols in {n_batches} spark batches…")
    for b in range(n_batches):
        chunk = symbols[b * SPARK_BATCH:(b + 1) * SPARK_BATCH]
        out.update(fetch_spark_batch(chunk))
        if b < n_batches - 1:
            time.sleep(2.0)
    print(f"  {len(out)}/{len(symbols)} ok")
    return out

# ---------------------------------------------------------------- indicators


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def pct_return(closes, n):
    if len(closes) < n + 1 or closes[-n - 1] == 0:
        return None
    return closes[-1] / closes[-n - 1] - 1.0


def rsi14(closes):
    n = 14
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)


def annualized_vol(closes, n=63):
    if len(closes) < n + 1:
        return None
    rets = []
    for i in range(len(closes) - n, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def zscores(values):
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return {i: 0.0 for i in range(len(values))}
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    sd = math.sqrt(var)
    out = {}
    for i, v in enumerate(values):
        out[i] = 0.0 if (v is None or sd == 0) else (v - mean) / sd
    return out

# ---------------------------------------------------------------- candidate scoring


def score_aggressive(bars, as_of):
    """Rank the momentum universe (close-based only — the curated universe is
    already liquid, and the batched data source doesn't carry volume)."""
    rows = []
    for sym, h in bars.items():
        if not h["dates"] or h["dates"][-1] != as_of:
            continue  # stale/halted symbol — never buy at an old price
        closes = [h["close"][d] for d in h["dates"]]
        if len(closes) < 65:
            continue
        px = closes[-1]
        if px < 2.0:
            continue
        r5, r20 = pct_return(closes, 5), pct_return(closes, 20)
        if r5 is None or r20 is None or r20 <= 0 or r5 <= 0:
            continue
        rsi = rsi14(closes[-80:])
        if rsi is not None and rsi >= 78:
            continue
        s20 = sma(closes, 20)
        dist20 = (px / s20 - 1.0) if s20 else 0.0
        vol = annualized_vol(closes)
        rows.append({
            "symbol": sym, "name": h["name"], "price": px,
            "r5": r5, "r20": r20, "dist20": dist20, "rsi": rsi, "vol": vol,
        })
    z5 = zscores([r["r5"] for r in rows])
    z20 = zscores([r["r20"] for r in rows])
    zd = zscores([r["dist20"] for r in rows])
    for i, r in enumerate(rows):
        r["score"] = 0.50 * z5[i] + 0.35 * z20[i] + 0.15 * zd[i]
        r["thesis"] = (
            f"Up {r['r5'] * 100:.1f}% this week and {r['r20'] * 100:.1f}% this month, "
            f"still under RSI {r['rsi']:.0f}. Momentum play — target +20%, hard stop −8%, "
            f"out after 15 trading days no matter what."
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def score_growth(bars, as_of):
    rows = []
    for sym, h in bars.items():
        if not h["dates"] or h["dates"][-1] != as_of:
            continue  # stale/halted symbol — never buy at an old price
        closes = [h["close"][d] for d in h["dates"]]
        if len(closes) < 210:
            continue
        px = closes[-1]
        s50, s200 = sma(closes, 50), sma(closes, 200)
        if not s50 or not s200 or not (px > s50 > s200):
            continue
        r126, r63 = pct_return(closes, 126), pct_return(closes, 63)
        if r126 is None or r126 <= 0 or r63 is None:
            continue
        vol = annualized_vol(closes)
        if vol is None or vol > 0.45:
            continue
        blocks_up = 0
        for b in range(6):
            end = len(closes) - b * 21 - 1
            start = end - 21
            if start >= 0 and closes[start] > 0:
                blocks_up += 1 if closes[end] > closes[start] else 0
        consistency = blocks_up / 6.0
        rows.append({
            "symbol": sym, "name": h["name"], "price": px,
            "r126": r126, "r63": r63, "vol": vol, "consistency": consistency,
        })
    z126 = zscores([r["r126"] for r in rows])
    z63 = zscores([r["r63"] for r in rows])
    zv = zscores([r["vol"] for r in rows])
    for i, r in enumerate(rows):
        r["score"] = 0.35 * z126[i] + 0.25 * z63[i] + 0.20 * r["consistency"] * 2 - 0.20 * zv[i]
        r["thesis"] = (
            f"Confirmed uptrend — price above rising 50- and 200-day averages, "
            f"up {r['r126'] * 100:.0f}% over six months with {r['vol'] * 100:.0f}% volatility "
            f"and gains in {int(r['consistency'] * 6)} of the last 6 months. "
            f"Target +15%, stop −10% or a break of the 200-day trend."
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows

# ---------------------------------------------------------------- state


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def bootstrap_state(as_of):
    return {
        "schema": 1,
        "inception_date": as_of,
        "starting_capital": STARTING_CAPITAL,
        "benchmark": {"symbol": BENCHMARK_SYMBOL, "shares": 0.0, "cash": STARTING_CAPITAL,
                      "entry_date": None, "last_eval_date": as_of},
        "strategies": {
            "aggressive": {"cash": STARTING_CAPITAL, "positions": [], "closed": [],
                           "cooldown": {}, "last_eval_date": as_of, "activity": []},
            "growth": {"cash": STARTING_CAPITAL, "positions": [], "closed": [],
                       "cooldown": {}, "last_eval_date": as_of, "activity": []},
            "longterm": {"cash": STARTING_CAPITAL, "positions": [], "closed": [],
                         "cooldown": {}, "last_eval_date": as_of, "activity": []},
        },
        "equity_history": {},
        "runs": 0,
    }


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)

# ---------------------------------------------------------------- portfolio replay


def last_close_at(h, d):
    """Last known close for history h on or before date d."""
    if d in h["close"]:
        return h["close"][d]
    px = None
    for date in h["dates"]:
        if date > d:
            break
        px = h["close"][date]
    return px


def position_value(pos, bars, d):
    h = bars.get(pos["symbol"])
    px = last_close_at(h, d) if h else None
    if px is None:
        px = pos.get("last_price") or pos["entry_price"]
    return pos["shares"] * px, px


def close_position(strat, pos, exit_date, exit_price, reason):
    pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    strat["cash"] += pos["shares"] * exit_price
    strat["closed"].append({
        "symbol": pos["symbol"], "name": pos.get("name", pos["symbol"]),
        "entry_date": pos["entry_date"], "entry_price": round(pos["entry_price"], 4),
        "exit_date": exit_date, "exit_price": round(exit_price, 4),
        "pnl_pct": round(pnl_pct, 2), "reason": reason,
    })
    strat["cooldown"][pos["symbol"]] = exit_date


def apply_pending_splits(pos, h, activity):
    """Yahoo retro-adjusts the ENTIRE price series the moment a split takes
    effect, so the stored position must be converted BEFORE the day walk —
    otherwise pre-split stops/targets get compared against post-split prices.
    If the event appears in the payload, the payload's prices are already
    adjusted — regardless of whether the ex-date is past as_of — so apply
    immediately. A per-position ledger makes this idempotent and lets a split
    that was invisible during a data outage be applied late (self-healing)."""
    applied = set(pos.get("splits_applied") or [])
    for s_date in sorted(h["splits"]):
        ratio = h["splits"][s_date]
        if s_date <= pos["entry_date"] or s_date in applied or ratio <= 0:
            continue
        pos["shares"] *= ratio
        pos["entry_price"] /= ratio
        if pos.get("target_price"):
            pos["target_price"] /= ratio
        if pos.get("stop_price"):
            pos["stop_price"] /= ratio
        if pos.get("last_price"):
            pos["last_price"] /= ratio
        applied.add(s_date)
        activity.append({"date": s_date, "text": f"{pos['symbol']} split {ratio:g}:1 — position adjusted"})
    pos["splits_applied"] = sorted(applied)


def sessions_between(calendar, after, upto):
    """Number of trading sessions in calendar strictly after `after`, up to `upto`."""
    return sum(1 for d in calendar if after < d <= upto)


def replay_strategy(key, strat, bars, calendar, as_of):
    """Replay each trading day since last evaluation: dividends, splits,
    exit rules, and a daily equity mark. Mutates strat, returns equity marks."""
    meta = STRATEGY_META[key]
    start = strat.get("last_eval_date") or as_of

    # --- once-per-run position maintenance (NOT once per replayed day). Runs
    # BEFORE the no-new-days early-out: a run on a split's ex-date morning has
    # no new session yet, but the fetched series is already retro-adjusted, so
    # the position must be converted before today's equity mark is taken.
    kept = []
    for pos in strat["positions"]:
        h = bars.get(pos["symbol"])
        if h is None:
            pos["missing_runs"] = pos.get("missing_runs", 0) + 1
            if pos["missing_runs"] >= 3:
                close_position(strat, pos, as_of, pos.get("last_price") or pos["entry_price"],
                               "data unavailable")
                continue
        else:
            pos["missing_runs"] = 0
            # heal a symbol-as-name from an event-less bootstrap
            if pos.get("name") == pos["symbol"] and h.get("name") and h["name"] != pos["symbol"]:
                pos["name"] = h["name"]
            apply_pending_splits(pos, h, strat["activity"])
            # back-credit dividends whose ex-dates were already replayed but
            # went uncredited (event-less fallback data at the time) — the
            # ledger makes the day-walk and this path mutually exclusive
            credited = set(pos.get("divs_credited") or [])
            for d_ex in sorted(h["divs"]):
                amt = h["divs"][d_ex]
                if d_ex <= pos["entry_date"] or d_ex > start or d_ex in credited:
                    continue
                strat["cash"] += pos["shares"] * amt
                credited.add(d_ex)
                strat["activity"].append(
                    {"date": d_ex, "text": f"{pos['symbol']} back-credited ${amt:.2f}/sh dividend "
                     f"missed during a data outage (+${pos['shares'] * amt:.2f})"})
            pos["divs_credited"] = sorted(credited)
        kept.append(pos)
    strat["positions"] = kept

    if as_of <= start:
        # nothing new — also refuses to roll the window backwards on a stale feed
        strat["last_eval_date"] = max(start, as_of)
        return {}

    days = [d for d in calendar if start < d <= as_of]
    marks = {}
    for d in days:
        still_open = []
        for pos in strat["positions"]:
            h = bars.get(pos["symbol"])
            if h is None:
                still_open.append(pos)
                continue
            # dividends: credit cash on ex-date (amounts arrive split-adjusted,
            # consistent with the adjusted share count); the ledger prevents
            # any double-credit with the back-credit path
            amt = h["divs"].get(d)
            if amt and d > pos["entry_date"]:
                credited = set(pos.get("divs_credited") or [])
                if d not in credited:
                    strat["cash"] += pos["shares"] * amt
                    credited.add(d)
                    pos["divs_credited"] = sorted(credited)
                    strat["activity"].append(
                        {"date": d, "text": f"{pos['symbol']} paid ${amt:.2f}/sh dividend (+${pos['shares'] * amt:.2f})"})
            px = h["close"].get(d)
            if px is None:
                still_open.append(pos)
                continue
            # Event-less fallback data (spark) is still retro-split-adjusted, so
            # an unreported split would look like a giant one-day gap versus our
            # stored basis and fire a false stop/target. On a split-sized move,
            # defer this position until real event data confirms what happened.
            prev = pos.get("last_price")
            if not h.get("has_events", True) and prev and (px < prev * 0.88 or px > prev * 1.12):
                still_open.append(pos)
                continue
            pos["last_price"] = px
            pos["bars_held"] = pos.get("bars_held", 0) + 1
            exited = False
            if key in ("aggressive", "growth"):
                if pos.get("stop_price") and px <= pos["stop_price"]:
                    close_position(strat, pos, d, px, "stop loss")
                    exited = True
                elif pos.get("target_price") and px >= pos["target_price"]:
                    close_position(strat, pos, d, px, "target hit")
                    exited = True
                elif key == "aggressive" and meta.get("max_hold_bars") and pos["bars_held"] >= meta["max_hold_bars"]:
                    close_position(strat, pos, d, px, "time exit (15 days)")
                    exited = True
                elif key == "growth":
                    # trend break: close >2% below the 200-day average as of day d
                    closes_to_d = [h["close"][x] for x in h["dates"] if x <= d]
                    s200 = sma(closes_to_d, 200)
                    if s200 and px < s200 * 0.98:
                        close_position(strat, pos, d, px, "trend break (200-day)")
                        exited = True
            if not exited:
                still_open.append(pos)
        strat["positions"] = still_open
        total = strat["cash"]
        for pos in strat["positions"]:
            val, _ = position_value(pos, bars, d)
            total += val
        marks[d] = total

    # --- halted/suspended/delisted: a symbol whose history exists but whose
    # last bar is >10 sessions old will never trigger a close-based exit —
    # force-exit at the last known price so it can't freeze a slot forever
    for pos in list(strat["positions"]):
        h = bars.get(pos["symbol"])
        if h and h["dates"] and sessions_between(calendar, h["dates"][-1], as_of) > 10:
            strat["positions"].remove(pos)
            close_position(strat, pos, as_of, pos.get("last_price") or pos["entry_price"],
                           "halted/delisted")

    strat["last_eval_date"] = max(start, as_of)
    return marks


def replay_benchmark(bench, bars, calendar, as_of, can_trade):
    h = bars.get(bench["symbol"])
    marks = {}
    if h is None:
        return marks
    start = bench.get("last_eval_date") or as_of
    if bench["shares"] == 0.0 and bench["cash"] > 0 and can_trade:
        px = last_close_at(h, as_of)
        if px:
            bench["shares"] = bench["cash"] / px
            bench["cash"] = 0.0
            bench["entry_date"] = as_of
            bench["entry_price"] = px
    # splits are applied up-front — the fetched series is already post-split —
    # and BEFORE the no-new-days early-out, same as replay_strategy
    # (apply on a position-shaped view, then copy back)
    if bench.get("entry_date"):
        view = {"symbol": bench["symbol"], "entry_date": bench["entry_date"],
                "splits_applied": bench.get("splits_applied") or [],
                "shares": bench["shares"], "entry_price": bench.get("entry_price") or 0.0}
        apply_pending_splits(view, h, [])
        bench["shares"] = view["shares"]
        bench["entry_price"] = view["entry_price"]
        bench["splits_applied"] = view["splits_applied"]
        # back-credit dividends missed during event-less fallback windows
        credited = set(bench.get("divs_credited") or [])
        for d_ex in sorted(h["divs"]):
            amt = h["divs"][d_ex]
            if d_ex <= bench["entry_date"] or d_ex > start or d_ex in credited:
                continue
            bench["cash"] += bench["shares"] * amt
            credited.add(d_ex)
        bench["divs_credited"] = sorted(credited)
    if as_of <= start:
        bench["last_eval_date"] = max(start, as_of)
        return marks
    days = [d for d in calendar if start < d <= as_of]
    for d in days:
        amt = h["divs"].get(d)
        if amt and bench.get("entry_date") and d > bench["entry_date"]:
            credited = set(bench.get("divs_credited") or [])
            if d not in credited:
                bench["cash"] += bench["shares"] * amt
                credited.add(d)
                bench["divs_credited"] = sorted(credited)
        px = h["close"].get(d)
        if px is not None:
            marks[d] = bench["cash"] + bench["shares"] * px
    bench["last_eval_date"] = max(start, as_of)
    return marks


def open_position(strat, cand, as_of, budget, target_pct=None, stop_pct=None):
    px = cand["price"]
    shares = budget / px
    pos = {
        "symbol": cand["symbol"], "name": cand.get("name", cand["symbol"]),
        "shares": shares, "entry_price": px, "entry_date": as_of,
        "target_price": px * (1 + target_pct) if target_pct else None,
        "stop_price": px * (1 - stop_pct) if stop_pct else None,
        "thesis": cand.get("thesis", ""), "last_price": px, "bars_held": 0,
        "splits_applied": [], "divs_credited": [],
    }
    strat["cash"] -= budget
    strat["positions"].append(pos)
    strat["activity"].append({"date": as_of, "text": f"BUY {cand['symbol']} @ ${px:,.2f} — {cand.get('thesis', '')[:80]}"})
    return pos


def fill_slots(key, strat, ranked, as_of, can_trade):
    """Fill empty slots with the best-ranked candidates not held / cooling down."""
    meta = STRATEGY_META[key]
    if not can_trade:
        return []
    held = {p["symbol"] for p in strat["positions"]}
    cutoff = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%d")
    cooling = {s for s, d in strat["cooldown"].items() if d >= cutoff}
    strat["cooldown"] = {s: d for s, d in strat["cooldown"].items() if d >= cutoff}
    opened = []
    empty = meta["slots"] - len(strat["positions"])
    if empty <= 0 or strat["cash"] <= 50:
        return opened
    budget_each = strat["cash"] / empty
    for cand in ranked:
        if empty <= 0:
            break
        if cand["symbol"] in held or cand["symbol"] in cooling:
            continue
        opened.append(open_position(strat, cand, as_of, min(budget_each, strat["cash"]),
                                    meta.get("target_pct"), meta.get("stop_pct")))
        held.add(cand["symbol"])
        empty -= 1
    return opened


def manage_longterm(strat, bars, as_of, can_trade):
    """Buy the fixed allocation at inception; afterwards rebalance on drift."""
    if not can_trade:
        return
    prices = {}
    for sym in LONGTERM_ALLOCATION:
        h = bars.get(sym)
        px = last_close_at(h, as_of) if h else None
        if px:
            prices[sym] = px
    if len(prices) < len(LONGTERM_ALLOCATION):
        return  # missing data — try again next run
    held = {p["symbol"]: p for p in strat["positions"]}
    equity = strat["cash"] + sum(p["shares"] * prices[p["symbol"]] for p in strat["positions"])
    if not held:  # inception buy
        for sym, (w, role) in LONGTERM_ALLOCATION.items():
            budget = equity * w
            strat["positions"].append({
                "symbol": sym, "name": bars[sym]["name"], "shares": budget / prices[sym],
                "entry_price": prices[sym], "entry_date": as_of,
                "target_price": None, "stop_price": None,
                "thesis": role, "last_price": prices[sym], "bars_held": 0,
                "splits_applied": [], "divs_credited": [],
            })
            strat["cash"] -= budget
        strat["activity"].append({"date": as_of, "text": f"Inception buy — ${equity:,.0f} across {len(LONGTERM_ALLOCATION)} ETFs"})
        return
    # drift check
    drifted = False
    for sym, (w, _) in LONGTERM_ALLOCATION.items():
        pos = held.get(sym)
        cur_w = (pos["shares"] * prices[sym] / equity) if pos else 0.0
        if abs(cur_w - w) > 0.05:
            drifted = True
    if drifted or strat["cash"] > equity * 0.05:
        for sym, (w, role) in LONGTERM_ALLOCATION.items():
            target_val = equity * w
            pos = held.get(sym)
            if pos:
                pos["shares"] = target_val / prices[sym]
                pos["last_price"] = prices[sym]
            else:
                strat["positions"].append({
                    "symbol": sym, "name": bars[sym]["name"], "shares": target_val / prices[sym],
                    "entry_price": prices[sym], "entry_date": as_of,
                    "target_price": None, "stop_price": None,
                    "thesis": role, "last_price": prices[sym], "bars_held": 0,
                    "splits_applied": [], "divs_credited": [],
                })
        strat["cash"] = equity - sum(equity * w for w, _ in LONGTERM_ALLOCATION.values())
        strat["activity"].append({"date": as_of, "text": "Rebalanced back to target weights"})

# ---------------------------------------------------------------- stats & output


def compute_stats(closed, equity_series, bench_return_pct):
    wins = [t for t in closed if t["pnl_pct"] > 0]
    losses = [t for t in closed if t["pnl_pct"] <= 0]
    total_ret = None
    max_dd = 0.0
    if equity_series:
        total_ret = (equity_series[-1] / STARTING_CAPITAL - 1.0) * 100.0
        peak = equity_series[0]
        for v in equity_series:
            peak = max(peak, v)
            if peak > 0:
                max_dd = min(max_dd, (v / peak - 1.0) * 100.0)
    return {
        "total_return_pct": round(total_ret, 2) if total_ret is not None else 0.0,
        "vs_spy_pct": round((total_ret or 0.0) - bench_return_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "closed_trades": len(closed),
        "win_rate_pct": round(len(wins) / len(closed) * 100.0, 1) if closed else None,
        "avg_win_pct": round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else None,
        "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else None,
    }


def build_site_payload(state, bars, as_of, new_picks):
    hist = state["equity_history"]
    dates = sorted(hist.keys())
    curves = {"dates": dates}
    for k in ("aggressive", "growth", "longterm", "benchmark"):
        curves[k] = [round(hist[d].get(k), 2) if hist[d].get(k) is not None else None for d in dates]
    bench_equity = None
    for d in reversed(dates):
        if hist[d].get("benchmark") is not None:
            bench_equity = hist[d]["benchmark"]
            break
    bench_ret = ((bench_equity or STARTING_CAPITAL) / STARTING_CAPITAL - 1.0) * 100.0

    strategies = {}
    for key, meta in STRATEGY_META.items():
        strat = state["strategies"][key]
        series = [hist[d][key] for d in dates if hist[d].get(key) is not None]
        stats = compute_stats(strat["closed"], series, bench_ret)
        positions = []
        for pos in strat["positions"]:
            val, px = position_value(pos, bars, as_of)
            positions.append({
                "symbol": pos["symbol"], "name": pos.get("name", pos["symbol"]),
                "entry_date": pos["entry_date"], "entry_price": round(pos["entry_price"], 2),
                "shares": round(pos["shares"], 4),
                "current_price": round(px, 2), "current_value": round(val, 2),
                "pnl_pct": round((px / pos["entry_price"] - 1.0) * 100.0, 2) if pos["entry_price"] else 0.0,
                "target_price": round(pos["target_price"], 2) if pos.get("target_price") else None,
                "stop_price": round(pos["stop_price"], 2) if pos.get("stop_price") else None,
                "thesis": pos.get("thesis", ""),
                "is_new": pos["entry_date"] == as_of and any(
                    p["symbol"] == pos["symbol"] for p in new_picks.get(key, [])),
            })
        equity = strat["cash"] + sum(p["current_value"] for p in positions)
        strategies[key] = {
            "key": key, "label": meta["label"], "icon": meta["icon"],
            "tagline": meta["tagline"], "horizon": meta["horizon"], "risk_note": meta["risk_note"],
            "equity": round(equity, 2), "cash": round(strat["cash"], 2),
            **stats,
            "positions": positions,
            "recent_trades": strat["closed"][-12:][::-1],
            "recent_activity": strat["activity"][-8:][::-1],
        }
    return {
        "version": 1,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of_date": as_of,
        "inception_date": state["inception_date"],
        "starting_capital": STARTING_CAPITAL,
        "runs": state["runs"],
        "benchmark": {
            "symbol": BENCHMARK_SYMBOL,
            "equity": round(bench_equity or STARTING_CAPITAL, 2),
            "total_return_pct": round(bench_ret, 2),
        },
        "strategies": strategies,
        "equity_curves": curves,
        "data_source": "Yahoo Finance daily closes (split-adjusted; dividends credited as cash)",
        "methodology_note": (
            "All trades are simulated with paper money at daily closing prices. "
            "Picks are committed to git before outcomes are known — the commit history is the proof. "
            "Not financial advice."
        ),
    }


def fmt_ret(v):
    return f"{v:+.2f}%" if v is not None else "—"


def build_report(site):
    s = site["strategies"]
    b = site["benchmark"]
    lines = [
        "# 📊 Advisor Daily Report",
        "",
        f"**As of {site['as_of_date']}** · generated {site['generated_at_utc']} · run #{site['runs']} · "
        f"inception {site['inception_date']} · each portfolio started with "
        f"${site['starting_capital']:,.0f} of paper money",
        "",
        "| Strategy | Equity | Total Return | vs S&P 500 | Max Drawdown | Trades | Win Rate |",
        "|---|---|---|---|---|---|---|",
    ]
    for key in ("aggressive", "growth", "longterm"):
        st = s[key]
        wr = f"{st['win_rate_pct']:.0f}%" if st["win_rate_pct"] is not None else "—"
        lines.append(
            f"| {st['icon']} **{st['label']}** | ${st['equity']:,.2f} | {fmt_ret(st['total_return_pct'])} "
            f"| {fmt_ret(st['vs_spy_pct'])} | {st['max_drawdown_pct']:.2f}% | {st['closed_trades']} | {wr} |")
    lines.append(
        f"| 🧭 S&P 500 (SPY) benchmark | ${b['equity']:,.2f} | {fmt_ret(b['total_return_pct'])} | — | — | — | — |")
    lines.append("")
    for key in ("aggressive", "growth", "longterm"):
        st = s[key]
        lines.append(f"## {st['icon']} {st['label']} — ${st['equity']:,.2f} ({fmt_ret(st['total_return_pct'])})")
        lines.append("")
        if st["positions"]:
            lines.append("| Position | Entry | Now | P&L | Target | Stop |")
            lines.append("|---|---|---|---|---|---|")
            for p in st["positions"]:
                new = " 🆕" if p["is_new"] else ""
                tgt = f"${p['target_price']:,.2f}" if p["target_price"] else "—"
                stp = f"${p['stop_price']:,.2f}" if p["stop_price"] else "—"
                lines.append(
                    f"| **{p['symbol']}**{new} ({p['entry_date']}) | ${p['entry_price']:,.2f} "
                    f"| ${p['current_price']:,.2f} | {fmt_ret(p['pnl_pct'])} | {tgt} | {stp} |")
        else:
            lines.append("_No open positions (all cash)._")
        lines.append("")
        if st["recent_trades"]:
            lines.append("<details><summary>Recent closed trades</summary>")
            lines.append("")
            lines.append("| Trade | Entry → Exit | P&L | Reason |")
            lines.append("|---|---|---|---|")
            for t in st["recent_trades"]:
                lines.append(
                    f"| {t['symbol']} | {t['entry_date']} ${t['entry_price']:,.2f} → "
                    f"{t['exit_date']} ${t['exit_price']:,.2f} | {fmt_ret(t['pnl_pct'])} | {t['reason']} |")
            lines.append("")
            lines.append("</details>")
            lines.append("")
    lines += [
        "---",
        "",
        "### How to verify this track record",
        "",
        "Every pick in this report was committed to git **before** its outcome was known.",
        "Check the [commit history of `data/`](../../commits/main/data) — each daily run is a",
        "timestamped, immutable record. Nothing here can be edited after the fact without",
        "leaving a trace in git history.",
        "",
        "_Simulated paper-money portfolios. Educational project — not financial advice._",
        "",
    ]
    return "\n".join(lines)


ADVISOR_START = "<!-- ADVISOR:START -->"
ADVISOR_END = "<!-- ADVISOR:END -->"


def update_readme(site):
    s = site["strategies"]
    b = site["benchmark"]
    block = [
        ADVISOR_START,
        f"### 📊 Live Track Record — as of {site['as_of_date']} (run #{site['runs']})",
        "",
        "| Strategy | Equity | Return | vs S&P 500 | Win Rate |",
        "|---|---|---|---|---|",
    ]
    for key in ("aggressive", "growth", "longterm"):
        st = s[key]
        wr = f"{st['win_rate_pct']:.0f}%" if st["win_rate_pct"] is not None else "—"
        block.append(f"| {st['icon']} {st['label']} | ${st['equity']:,.2f} | {fmt_ret(st['total_return_pct'])} "
                     f"| {fmt_ret(st['vs_spy_pct'])} | {wr} |")
    block.append(f"| 🧭 SPY benchmark | ${b['equity']:,.2f} | {fmt_ret(b['total_return_pct'])} | — | — |")
    block += ["", f"_Updated automatically every trading day · [full report](REPORT.md) · "
                  f"[verify in commit history](../../commits/main/data)_", ADVISOR_END]
    block_text = "\n".join(block)
    if os.path.exists(README_PATH):
        with open(README_PATH, encoding="utf-8") as f:
            content = f.read()
        if ADVISOR_START in content and ADVISOR_END in content:
            pattern = re.escape(ADVISOR_START) + r".*?" + re.escape(ADVISOR_END)
            content = re.sub(pattern, block_text, content, flags=re.S)
        else:
            content = content.rstrip() + "\n\n" + block_text + "\n"
    else:
        content = block_text + "\n"
    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def build_commit_message(site, trades_today):
    s = site["strategies"]
    b = site["benchmark"]
    parts = [f"{s[k]['icon']} {fmt_ret(s[k]['total_return_pct'])}" for k in ("aggressive", "growth", "longterm")]
    msg = f"📊 {site['as_of_date']}: " + " | ".join(parts) + f" | SPY {fmt_ret(b['total_return_pct'])}"
    if trades_today:
        msg += f" · {trades_today} trade{'s' if trades_today != 1 else ''}"
    lines = [msg, ""]
    for key in ("aggressive", "growth", "longterm"):
        st = s[key]
        pos_txt = ", ".join(
            f"{p['symbol']} {fmt_ret(p['pnl_pct'])}" for p in st["positions"][:6]) or "all cash"
        lines.append(f"{st['icon']} {st['label']}: ${st['equity']:,.2f} — {pos_txt}")
    lines.append(f"🧭 SPY benchmark: ${b['equity']:,.2f}")
    return "\n".join(lines)

# ---------------------------------------------------------------- main


def main():
    print(f"=== Advisor run {datetime.now(timezone.utc).isoformat()} ===")

    state = load_state()
    held_syms = []
    if state:
        for strat in state["strategies"].values():
            held_syms += [p["symbol"] for p in strat["positions"]]

    # 1) critical symbols (full history + div/split events) — must succeed
    critical = list(dict.fromkeys([BENCHMARK_SYMBOL] + list(LONGTERM_ALLOCATION) + held_syms))
    bars_critical = fetch_portfolio_bars(critical)
    if BENCHMARK_SYMBOL not in bars_critical:
        # Yahoo rate-limit penalties are usually minutes-long; wait one long
        # beat and try again before failing the run
        print("Benchmark fetch failed — cooling down 180s before one more attempt…")
        time.sleep(180)
        bars_critical = fetch_portfolio_bars(critical)
    if BENCHMARK_SYMBOL not in bars_critical:
        print("FATAL: no benchmark data — aborting without touching state.")
        sys.exit(1)
    missing_held = [s for s in held_syms if s not in bars_critical]
    if missing_held:
        print(f"  warning: no data for held positions: {missing_held}")

    # 2) wide scanning universe (batched closes) — a bad day here only means
    #    we skip opening NEW positions; existing ones are still managed
    universe_syms = [s for s in dict.fromkeys(MOMENTUM + SP_CORE) if s not in bars_critical]
    bars_universe = fetch_universe(universe_syms)
    universe_ok = len(bars_universe) >= len(universe_syms) * MIN_FETCH_COVERAGE
    if not universe_ok:
        print(f"  warning: universe coverage too low ({len(bars_universe)}/{len(universe_syms)}) — "
              "no new positions will be opened this run.")

    bars = {**bars_universe, **bars_critical}  # full-history data wins
    trim_partial_session(bars)

    calendar = bars[BENCHMARK_SYMBOL]["dates"]
    if not calendar:
        print("FATAL: benchmark has no completed sessions — aborting without touching state.")
        sys.exit(1)
    as_of = calendar[-1]
    print(f"Market data as of {as_of} ({len(calendar)} sessions, {len(bars)} symbols)")

    # Entries only happen when the freshest completed session is TODAY — i.e.
    # on the post-close run, at a close that printed minutes ago. A pre-open or
    # weekend run trading at the previous session's close would book
    # overnight/weekend gaps nobody could capture. The one exception is the
    # very first run (inception): every portfolio INCLUDING the SPY benchmark
    # enters at the same last close, so the comparison starts fair and the
    # commit still precedes every outcome.
    try:
        from zoneinfo import ZoneInfo
        ny_today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        ny_today = (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")
    can_trade = (as_of == ny_today) or state is None
    if not can_trade:
        print(f"Trading disabled this run (last session {as_of} != NY today {ny_today}) — "
              "evaluate/publish only; entries happen on the post-close run.")

    if state is None:
        print("Bootstrapping fresh state (inception day).")
        state = bootstrap_state(as_of)
    else:
        recorded = sorted(state["equity_history"].keys())
        if recorded and as_of < recorded[-1]:
            print(f"Feed is stale (as_of {as_of} < last recorded {recorded[-1]}) — nothing to do.")
            sys.exit(0)

    # 1) replay portfolios day-by-day since last run
    for key in ("aggressive", "growth", "longterm"):
        strat = state["strategies"][key]
        n_before = len(strat["closed"])
        marks = replay_strategy(key, strat, bars, calendar, as_of)
        for d, v in marks.items():
            state["equity_history"].setdefault(d, {})[key] = round(v, 2)
        closed_now = strat["closed"][n_before:]
        for t in closed_now:
            print(f"  {STRATEGY_META[key]['icon']} closed {t['symbol']}: {t['pnl_pct']:+.2f}% ({t['reason']})")

    bench_marks = replay_benchmark(state["benchmark"], bars, calendar, as_of, can_trade)
    for d, v in bench_marks.items():
        state["equity_history"].setdefault(d, {})["benchmark"] = round(v, 2)

    # 2) longterm allocation management (inception buy / drift rebalance)
    manage_longterm(state["strategies"]["longterm"], bars, as_of, can_trade)

    # 3) fill empty aggressive/growth slots with freshly ranked picks
    new_picks = {"aggressive": [], "growth": [], "longterm": []}
    if universe_ok:
        agg_universe = {s: h for s, h in bars.items() if s in set(MOMENTUM)}
        gro_universe = {s: h for s, h in bars.items() if s in set(SP_CORE)}
        ranked_agg = score_aggressive(agg_universe, as_of)
        ranked_gro = score_growth(gro_universe, as_of)
        print(f"Ranked candidates: aggressive {len(ranked_agg)}, growth {len(ranked_gro)}")
        new_picks["aggressive"] = fill_slots(
            "aggressive", state["strategies"]["aggressive"], ranked_agg, as_of, can_trade)
        new_picks["growth"] = fill_slots(
            "growth", state["strategies"]["growth"], ranked_gro, as_of, can_trade)
        # upgrade fresh picks with full history (real company name + div/split
        # events) so the next replay has proper accounting from day one
        fresh_syms = [] if SPARK_ONLY else [
            p["symbol"] for picks in new_picks.values() for p in picks
            if p["symbol"] not in bars_critical]
        for sym in fresh_syms:
            time.sleep(1.2)
            h = fetch_history(sym)
            if h:
                bars[sym] = h
                for k in ("aggressive", "growth"):
                    for pos in state["strategies"][k]["positions"]:
                        if pos["symbol"] == sym:
                            pos["name"] = h["name"]
    for key, picks in new_picks.items():
        for p in picks:
            print(f"  {STRATEGY_META[key]['icon']} NEW {p['symbol']} @ ${p['entry_price']:,.2f}")

    # 4) refresh today's equity marks after entries/rebalance (cash↔stock is neutral,
    #    but recompute so held-position last_price fields are current)
    for key in ("aggressive", "growth", "longterm"):
        strat = state["strategies"][key]
        total = strat["cash"]
        for pos in strat["positions"]:
            val, px = position_value(pos, bars, as_of)
            pos["last_price"] = px
            total += val
        state["equity_history"].setdefault(as_of, {})[key] = round(total, 2)
    if state["equity_history"].get(as_of, {}).get("benchmark") is None:
        h = bars[BENCHMARK_SYMBOL]
        px = last_close_at(h, as_of)
        if px:
            b = state["benchmark"]
            state["equity_history"].setdefault(as_of, {})["benchmark"] = round(b["cash"] + b["shares"] * px, 2)

    # trim unbounded lists
    for strat in state["strategies"].values():
        strat["activity"] = strat["activity"][-60:]

    state["runs"] += 1
    state["last_run_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 5) outputs
    trades_today = sum(
        1 for k in ("aggressive", "growth") for t in state["strategies"][k]["closed"]
        if t["exit_date"] == as_of) + sum(len(v) for v in new_picks.values())
    site = build_site_payload(state, bars, as_of, new_picks)

    save_json(STATE_PATH, state)
    save_json(SITE_PATH, site)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    shutil.copyfile(SITE_PATH, os.path.join(HISTORY_DIR, f"{as_of}.json"))

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(build_report(site))
    update_readme(site)
    with open(COMMIT_MSG_PATH, "w", encoding="utf-8") as f:
        f.write(build_commit_message(site, trades_today))

    print("Run complete.")
    for key in ("aggressive", "growth", "longterm"):
        st = site["strategies"][key]
        print(f"  {st['icon']} {st['label']}: ${st['equity']:,.2f} ({fmt_ret(st['total_return_pct'])})")
    print(f"  🧭 SPY: ${site['benchmark']['equity']:,.2f} ({fmt_ret(site['benchmark']['total_return_pct'])})")


if __name__ == "__main__":
    main()
