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
# The public free-tier key already shipped in index.html — an env secret
# overrides it. 60 calls/min, resets every minute.
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "").strip() or \
    "d874rh1r01ql0hsk9qogd874rh1r01ql0hsk9qp0"
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

# ⚡ Day Trader watchlist: the most liquid high-beta movers. Small on purpose —
# one Finnhub quote each per intraday check stays well inside 60 calls/min.
DAYTRADE_WATCHLIST = [
    "TSLA", "NVDA", "AMD", "SMCI", "COIN", "MSTR", "MARA", "RIOT", "PLTR", "HOOD",
    "SOFI", "GME", "AMC", "RDDT", "DKNG", "CVNA", "AFRM", "UPST", "IONQ", "RGTI",
    "ASTS", "RKLB", "ACHR", "JOBY", "HIMS", "CELH", "ELF", "DUOL", "SNOW", "NET",
    "CRWD", "MDB", "DDOG", "ROKU", "TTD", "SE", "MELI", "NU", "U", "PATH",
    "VRT", "SQ", "SHOP", "ARM", "MU",
]

STRATEGY_META = {
    "aggressive": {
        "label": "Get Rich Quick",
        "icon": "🚀",
        "tagline": "High-octane momentum swings. Big targets, hard stops, fast exits.",
        "horizon": "Days to weeks",
        "risk_note": "Very high risk. Buys quality momentum (3-month climbers, not 1-week spikes), only when the broad market is above its 50-day trend. Once a trade is up 10% its stop moves to breakeven, so a winner cannot become a loser.",
        "slots": 5,
        "target_pct": 0.20,
        "stop_pct": 0.08,
        "max_hold_bars": 15,
        # once a position is up this much, the stop ratchets to the entry
        # price — a winner is never allowed to become a loser
        "be_trigger": 0.10,
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
    "daytrade": {
        "label": "Day Trader",
        "icon": "⚡",
        "tagline": "Intraday momentum bursts. In and out the same day — always flat by the close.",
        "horizon": "Minutes to hours",
        "risk_note": (
            "Extreme risk, smallest edges. Buys only 2-8% morning gaps that are still climbing "
            "20 minutes after first sighting, and only when the broad market is steady. Checks run "
            "about every 20 minutes; fills book at the quoted price of the check minus 10 bps modeled "
            "slippage, timestamped by the git commit. Stop jumps to breakeven once up 1.5%. "
            "Never holds overnight."
        ),
        "slots": 3,
        # ±1%/2% sat inside normal 20-minute noise for these names — widened so
        # a stop means a real reversal, not a wiggle
        "target_pct": 0.03,
        "stop_pct": 0.02,
        # entry rule: up min_gap..max_gap vs yesterday's close. Monster gaps
        # (>8%) statistically fade — they are skipped on purpose.
        "min_gap": 0.02,
        "max_gap": 0.08,
        # stop ratchets to entry once up this much — no round-trips to red
        "be_trigger": 0.015,
        # entries only in the morning momentum window; afternoon setups have
        # less runway and worse odds. Everything flattens at eod_flat.
        "entry_cutoff": (11, 30),
        "eod_flat": (15, 40),
        # no new entries when SPY itself is down this much on the day
        "tape_filter": -0.01,
    },
}

STRATEGY_KEYS = ("daytrade", "aggressive", "growth", "longterm")

# aggressive won't re-enter a symbol for this many days after exiting it
COOLDOWN_DAYS = 7
# modeled slippage per fill, in basis points (1 bp = 0.01%). Commissions are
# zero at modern retail brokers, so this is the only modeled friction. The
# intraday sleeve pays double — momentum names have wider effective spreads.
# The SPY benchmark is left frictionless, the standard convention.
SLIPPAGE_BPS_DAILY = 5
SLIPPAGE_BPS_INTRADAY = 10

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


METRICS_PATH = os.path.join(DATA_DIR, "metrics.json")


def load_metrics_cache():
    if os.path.exists(METRICS_PATH):
        with open(METRICS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"date": "", "metrics": {}}


def finnhub_metric(sym):
    """Finnhub's free /stock/metric — trailing return windows, volatility, and
    the 52-week range for one symbol. This is the key that lets the ranking
    models work from day one: the vendor has already computed the 3-month and
    6-month windows that would otherwise need a year of stored closes."""
    if not FINNHUB_KEY:
        return None
    try:
        j = http_get_json(
            f"https://finnhub.io/api/v1/stock/metric?symbol={urllib.parse.quote(sym)}"
            f"&metric=all&token={FINNHUB_KEY}", timeout=12)
        m = (j or {}).get("metric") or {}
        return m or None
    except Exception:  # noqa: BLE001
        return None


def fetch_universe_metrics(symbols, today):
    """Daily-cached metric pull for the scanning universe (1 call/symbol,
    paced under the 60/min free limit). Cached per calendar day — these
    windows only move once a session."""
    cache = load_metrics_cache()
    if cache.get("date") == today and len(cache.get("metrics") or {}) >= len(symbols) * 0.8:
        print(f"  metrics: reusing today's cache ({len(cache['metrics'])} symbols)")
        return cache["metrics"]
    out = dict(cache.get("metrics") or {}) if cache.get("date") == today else {}
    todo = [s for s in symbols if s not in out]
    print(f"Fetching Finnhub metrics for {len(todo)} symbols (~{len(todo) * 1.05 / 60:.0f} min)…")
    for i, sym in enumerate(todo):
        m = finnhub_metric(sym)
        if m:
            out[sym] = {
                "r5": m.get("5DayPriceReturnDaily"),
                "r13w": m.get("13WeekPriceReturnDaily"),
                "r26w": m.get("26WeekPriceReturnDaily"),
                "r52w": m.get("52WeekPriceReturnDaily"),
                "vol": m.get("3MonthADReturnStd"),
                "beta": m.get("beta"),
                "hi52": m.get("52WeekHigh"),
                "lo52": m.get("52WeekLow"),
                "advol": m.get("10DayAverageTradingVolume"),
            }
        if i < len(todo) - 1:
            time.sleep(1.05)
        if (i + 1) % 100 == 0:
            print(f"  … {i + 1}/{len(todo)}")
    save_json(METRICS_PATH, {"date": today, "metrics": out}, compact=True)
    print(f"  metrics: {len(out)}/{len(symbols)} symbols")
    return out


def _range_pos(px, lo, hi):
    """Where the price sits in its 52-week range, 0..1. A durable uptrend puts
    price in the upper part of its own range — the metric-path stand-in for
    'above rising 50- and 200-day averages'."""
    if not px or not lo or not hi or hi <= lo:
        return None
    return max(0.0, min(1.0, (px - lo) / (hi - lo)))


def score_aggressive_metrics(metrics, quotes):
    """🚀 Fast Mover ranking from vendor metric windows. Same philosophy as the
    history-based model: volatility-adjusted 3-month momentum leads, longer
    trend confirms, the last week is only a sanity band so verticals and
    collapses are both excluded."""
    rows = []
    for sym, m in metrics.items():
        px = quotes.get(sym)
        if not px or px < 2.0:
            continue
        r13, r26, r5 = m.get("r13w"), m.get("r26w"), m.get("r5")
        vol = m.get("vol")
        if r13 is None or r26 is None or r5 is None or not vol or vol <= 0:
            continue
        if r13 <= 0 or r26 <= 0:
            continue                       # rising on both horizons
        if r5 < -2.0 or r5 > 15.0:
            continue                       # not collapsing, not vertical
        pos = _range_pos(px, m.get("lo52"), m.get("hi52"))
        if pos is None or pos < 0.55:
            continue                       # must be in the upper half of its range
        if pos > 0.995 and r5 > 8.0:
            continue                       # blow-off top at a 52-week high
        advol = m.get("advol")
        if advol is not None and advol * px < 20.0:
            continue                       # ~$20M/day liquidity floor (advol in millions)
        rows.append({"symbol": sym, "name": sym, "price": px,
                     "r13": r13, "r26": r26, "r5": r5, "vol": vol, "pos": pos,
                     "q13": r13 / vol, "q26": r26 / vol})
    z13 = zscores([r["q13"] for r in rows])
    z26 = zscores([r["q26"] for r in rows])
    for i, r in enumerate(rows):
        r["score"] = 0.60 * z13[i] + 0.40 * z26[i]
        r["basis"] = "metrics"
        r["thesis"] = (
            f"Up {r['r13']:.0f}% over three months and {r['r26']:.0f}% over six, "
            f"sitting {r['pos'] * 100:.0f}% of the way up its 52-week range — a steady climb "
            f"rather than a one-week spike ({r['r5']:+.1f}% this week). Target +20%, stop −8% "
            f"moving to breakeven at +10%, out after 15 trading days regardless."
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def score_growth_metrics(metrics, quotes):
    """📈 Steady Climber ranking from vendor metric windows: durable multi-month
    uptrend, near the top of its own range, with volatility capped."""
    rows = []
    for sym, m in metrics.items():
        px = quotes.get(sym)
        if not px or px < 5.0:
            continue
        r13, r26, r52 = m.get("r13w"), m.get("r26w"), m.get("r52w")
        vol = m.get("vol")
        if r13 is None or r26 is None or r52 is None or not vol or vol <= 0:
            continue
        if r26 <= 0 or r52 <= 0 or r13 <= 0:
            continue                       # rising over 3, 6 and 12 months
        if vol > 45.0:
            continue                       # volatility cap
        pos = _range_pos(px, m.get("lo52"), m.get("hi52"))
        if pos is None or pos < 0.65:
            continue                       # trading near its highs = confirmed uptrend
        rows.append({"symbol": sym, "name": sym, "price": px,
                     "r13": r13, "r26": r26, "r52": r52, "vol": vol, "pos": pos,
                     "q26": r26 / vol, "q13": r13 / vol})
    z26 = zscores([r["q26"] for r in rows])
    z13 = zscores([r["q13"] for r in rows])
    for i, r in enumerate(rows):
        r["score"] = 0.55 * z26[i] + 0.25 * z13[i] + 0.20 * r["pos"] * 2
        r["basis"] = "metrics"
        r["thesis"] = (
            f"A confirmed climb: up {r['r26']:.0f}% over six months and {r['r52']:.0f}% over the year, "
            f"trading {r['pos'] * 100:.0f}% of the way up its 52-week range with moderate "
            f"({r['vol']:.0f}%) volatility. Target +15%, stop −10%, and it steps aside if the "
            f"trend breaks."
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


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
    """Current quote from Finnhub: {'c': last price, 't': last trade unix ts}."""
    if not FINNHUB_KEY:
        return None
    try:
        j = http_get_json(
            f"https://finnhub.io/api/v1/quote?symbol={urllib.parse.quote(sym)}&token={FINNHUB_KEY}",
            timeout=10,
        )
        c, t = j.get("c"), j.get("t")
        if isinstance(c, (int, float)) and c > 0 and isinstance(t, (int, float)) and t > 0:
            return {"c": float(c), "t": int(t)}
    except Exception:  # noqa: BLE001
        pass
    return None


def ny_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc) - timedelta(hours=4)


def finnhub_close_bars(symbols):
    """Absolute-floor data source: outside market hours, Finnhub's quote price
    IS the most recent completed session's official close. Builds single-bar
    histories good for marks, exits, and same-day entries — no indicator
    history, no div/split events (the ledgers heal those later). Never used
    while the market is open, so a live price can't masquerade as a close."""
    now = ny_now()
    if now.weekday() < 5 and (9, 25) <= (now.hour, now.minute) < (16, 10):
        print("  Finnhub floor unavailable mid-session — no completed close to read.")
        return {}
    out = {}
    print(f"Fetching {len(symbols)} end-of-day closes from Finnhub…")
    for sym in symbols:
        q = finnhub_quote(sym)
        if q:
            d = market_date(q["t"], "America/New_York")
            out[sym] = {"dates": [d], "close": {d: q["c"]}, "volume": {},
                        "name": sym, "divs": {}, "splits": {}, "has_events": False}
        time.sleep(1.1)  # free tier: 60 calls/min
    print(f"  {len(out)}/{len(symbols)} ok")
    return out


STOOQ_HOSTS = ["https://stooq.com", "https://stooq.pl"]


def fetch_history_stooq(sym):
    """Daily close history from Stooq's free CSV endpoint (no key). Prices are
    split-adjusted; no div/split events, so has_events=False and the ledgers
    heal events when a Yahoo fetch next succeeds."""
    mapped = sym.lower().replace(".", "-") + ".us"
    for host in STOOQ_HOSTS:
        try:
            url = f"{host}/q/d/l/?s={mapped}&i=d"
            if requests is not None:
                r = requests.get(url, headers=UA, timeout=15)
                r.raise_for_status()
                text = r.text
            else:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    text = resp.read().decode("utf-8", "replace")
            lines = text.strip().splitlines()
            if not lines or not lines[0].lower().startswith("date"):
                continue
            dates, close_map, vol_map = [], {}, {}
            for line in lines[-280:]:
                parts = line.split(",")
                if len(parts) < 5 or parts[0].lower() == "date":
                    continue
                d, c = parts[0], parts[4]
                try:
                    px = float(c)
                except ValueError:
                    continue
                if px <= 0 or len(d) != 10:
                    continue
                dates.append(d)
                close_map[d] = px
                try:
                    vol_map[d] = float(parts[5]) if len(parts) > 5 and parts[5] else 0.0
                except ValueError:
                    vol_map[d] = 0.0
            if len(dates) >= 2:
                return {"dates": dates, "close": close_map, "volume": vol_map,
                        "name": sym, "divs": {}, "splits": {}, "has_events": False}
        except Exception:  # noqa: BLE001
            continue
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
    missing = [s for s in symbols if s not in out]
    if missing:
        print(f"  spark gave {len(out)}/{len(symbols)} — Stooq fallback for {missing}")
        for i, sym in enumerate(missing):
            h = fetch_history_stooq(sym)
            if h:
                out[sym] = h
            if i < len(missing) - 1:
                time.sleep(0.3)
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
    missing = [s for s in symbols if s not in out]
    if missing and len(missing) <= 250:
        print(f"  spark gave {len(out)}/{len(symbols)} — Stooq gap-fill for {len(missing)} symbols")
        for i, sym in enumerate(missing):
            h = fetch_history_stooq(sym)
            if h:
                out[sym] = h
            if i < len(missing) - 1:
                time.sleep(0.25)
    elif missing:
        # spark failed wholesale — try Stooq for the entire universe, capped
        print(f"  spark gave only {len(out)}/{len(symbols)} — Stooq sweep (capped at 250)")
        for i, sym in enumerate(missing[:250]):
            h = fetch_history_stooq(sym)
            if h:
                out[sym] = h
            time.sleep(0.25)
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
    """Rank the momentum universe on QUALITY momentum, not raw spike size.

    The evidence this leans on:
      * 1-week winners tend to mean-revert; 1–6 month winners tend to persist.
        So the ranking anchors on the 3-month move, confirms with 1-month, and
        only uses the last week as a sanity band (not collapsing, not vertical).
      * Volatility-adjusted momentum beats raw momentum — a smooth +30% is a
        far better sign than a spiky +30%. Returns are divided by volatility
        before ranking (a Sharpe-style score).
      * RSI is used as a band, not a cap: 45–75 means genuine strength that
        isn't yet euphoric. Chasing RSI>75 verticals is how momentum dies.
    """
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
        r63 = pct_return(closes, 63) if len(closes) >= 64 else r20
        if r5 is None or r20 is None or r63 is None:
            continue
        if r20 <= 0 or r63 <= 0:
            continue                      # must be rising on both horizons
        if r5 < -0.02 or r5 > 0.15:
            continue                      # not collapsing, not a vertical spike
        rsi = rsi14(closes[-80:])
        if rsi is None or rsi < 45 or rsi > 75:
            continue                      # strength without euphoria
        vol = annualized_vol(closes)
        if not vol or vol <= 0:
            continue
        rows.append({
            "symbol": sym, "name": h["name"], "price": px,
            "r5": r5, "r20": r20, "r63": r63, "rsi": rsi, "vol": vol,
            "q63": r63 / vol, "q20": r20 / vol,
        })
    z63 = zscores([r["q63"] for r in rows])
    z20 = zscores([r["q20"] for r in rows])
    for i, r in enumerate(rows):
        r["score"] = 0.55 * z63[i] + 0.45 * z20[i]
        r["thesis"] = (
            f"Up {r['r63'] * 100:.0f}% in three months and {r['r20'] * 100:.1f}% this month — "
            f"a steady climb, not a one-week spike (RSI {r['rsi']:.0f}). Target +20%, stop −8%, "
            f"stop jumps to breakeven once up 10%, out after 15 trading days no matter what."
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
            key: {"cash": STARTING_CAPITAL, "positions": [], "closed": [],
                  "cooldown": {}, "last_eval_date": as_of, "activity": []}
            for key in STRATEGY_KEYS
        },
        "equity_history": {},
        "runs": 0,
    }


def ensure_strategies(state, as_of):
    """Migration: states created before a strategy existed gain it here,
    seeded with fresh capital from the day it first appears."""
    for key in STRATEGY_KEYS:
        if key not in state["strategies"]:
            state["strategies"][key] = {
                "cash": STARTING_CAPITAL, "positions": [], "closed": [],
                "cooldown": {}, "last_eval_date": as_of, "activity": [
                    {"date": as_of, "text": f"{STRATEGY_META[key]['label']} sleeve started with "
                     f"${STARTING_CAPITAL:,.0f}"}],
            }


def save_json(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, separators=(",", ":"), sort_keys=True)
        else:
            json.dump(obj, f, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- price store
# A rolling daily-close store committed to the repo. External history APIs
# (Yahoo, Stooq) block datacenter IPs unpredictably, but Finnhub's quote
# endpoint always works — so the store gets seeded once with a year of
# history and then grows itself by one close per session, forever. Ranking
# indicators read from here whenever richer live history isn't available.

PRICES_PATH = os.path.join(DATA_DIR, "prices.json")
STORE_MAX_SESSIONS = 280


def load_price_store():
    if os.path.exists(PRICES_PATH):
        with open(PRICES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def merge_into_store(store, bars):
    for sym, h in bars.items():
        merged = {r[0]: r[1] for r in store.get(sym, [])}
        for d in h["dates"]:
            merged[d] = round(h["close"][d], 4)
        store[sym] = [[d, merged[d]] for d in sorted(merged)][-STORE_MAX_SESSIONS:]


def store_history(store, sym):
    rows = store.get(sym) or []
    if not rows:
        return None
    return {"dates": [r[0] for r in rows], "close": {r[0]: r[1] for r in rows},
            "volume": {}, "name": sym, "divs": {}, "splits": {}, "has_events": False}

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


def close_position(strat, pos, exit_date, exit_price, reason, slippage_bps=None):
    """Book an exit NET of modeled slippage: the observed price is haircut by
    slippage_bps (defaults to SLIPPAGE_BPS_DAILY). Commissions are zero — the
    modern retail reality — so slippage is the only modeled friction. The raw
    observed price is kept on the trade record for auditability."""
    bps = SLIPPAGE_BPS_DAILY if slippage_bps is None else slippage_bps
    fill = exit_price * (1.0 - bps / 10000.0)
    pnl_pct = (fill / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    strat["cash"] += pos["shares"] * fill
    strat["closed"].append({
        "symbol": pos["symbol"], "name": pos.get("name", pos["symbol"]),
        "entry_date": pos["entry_date"], "entry_price": round(pos["entry_price"], 4),
        "exit_date": exit_date, "exit_price": round(fill, 4),
        "raw_exit_price": round(exit_price, 4), "slippage_bps": bps,
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
            # breakeven ratchet: once up be_trigger, the stop rises to the
            # entry price — a winner is never allowed to become a loser
            be = meta.get("be_trigger")
            if (be and key == "aggressive" and not pos.get("be_locked")
                    and pos.get("stop_price") and px >= pos["entry_price"] * (1 + be)):
                pos["stop_price"] = max(pos["stop_price"], pos["entry_price"])
                pos["be_locked"] = True
                strat["activity"].append(
                    {"date": d, "text": f"{pos['symbol']} up {be * 100:.0f}% — stop moved to breakeven"})
            exited = False
            if key in ("aggressive", "growth"):
                if pos.get("stop_price") and px <= pos["stop_price"]:
                    close_position(strat, pos, d, px,
                                   "breakeven stop" if pos.get("be_locked") else "stop loss")
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


def consolidate_daytrade(strat, bars, calendar, as_of):
    """Daily-mode bookkeeping for the intraday sleeve. Day trades open and
    close within a session via the intraday runs; here we only (a) safety-flat
    anything left open — should never happen, but the sleeve must NEVER hold
    overnight — and (b) record one equity mark per session (cash, since flat)."""
    start = strat.get("last_eval_date") or as_of
    marks = {}
    for pos in list(strat["positions"]):
        h = bars.get(pos["symbol"])
        px = (last_close_at(h, as_of) if h else None) or pos.get("last_price") or pos["entry_price"]
        strat["positions"].remove(pos)
        close_position(strat, pos, as_of, px, "overnight safety flat",
                       slippage_bps=SLIPPAGE_BPS_INTRADAY)
    for d in calendar:
        if start < d <= as_of:
            marks[d] = strat["cash"]
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
    # entries pay modeled slippage too: fill slightly above the observed close
    fill = px * (1.0 + SLIPPAGE_BPS_DAILY / 10000.0)
    shares = budget / fill
    pos = {
        "symbol": cand["symbol"], "name": cand.get("name", cand["symbol"]),
        "shares": shares, "entry_price": fill, "entry_date": as_of,
        "raw_entry_price": px,
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
    if not held:  # inception buy — entries pay modeled slippage
        for sym, (w, role) in LONGTERM_ALLOCATION.items():
            budget = equity * w
            fill = prices[sym] * (1.0 + SLIPPAGE_BPS_DAILY / 10000.0)
            strat["positions"].append({
                "symbol": sym, "name": bars[sym]["name"], "shares": budget / fill,
                "entry_price": fill, "raw_entry_price": prices[sym], "entry_date": as_of,
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
        # rebalancing trades pay slippage on the notional actually traded
        traded = sum(abs(equity * w - (held[s]["shares"] * prices[s] if s in held else 0.0))
                     for s, (w, _) in LONGTERM_ALLOCATION.items())
        cost = traded * SLIPPAGE_BPS_DAILY / 10000.0
        strat["cash"] = equity - sum(equity * w for w, _ in LONGTERM_ALLOCATION.values()) - cost
        strat["activity"].append({"date": as_of, "text":
            f"Rebalanced back to target weights (${cost:,.2f} modeled slippage)"})

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


def pro_metrics(dates, hist, key, closed, slots=None):
    """Institutional-grade statistics from the daily equity marks, computed
    only when there is enough sample to mean anything (else null → shown as
    an em dash, never a fake number).

    All figures are GROSS: fills at official closes (intraday sleeve at live
    quotes), no commissions, no modeled slippage. Stated, not hidden.
    """
    pairs = []
    prev = None
    for d in dates:
        s, b = hist[d].get(key), hist[d].get("benchmark")
        if s is None or b is None:
            prev = None
            continue
        if prev is not None and prev[0] > 0 and prev[1] > 0:
            pairs.append((s / prev[0] - 1.0, b / prev[1] - 1.0))
        prev = (s, b)
    out = {"ann_vol_pct": None, "sharpe": None, "beta": None, "alpha_ann_pct": None,
           "ann_return_pct": None, "profit_factor": None, "sample_days": len(pairs),
           "sortino": None, "calmar": None, "best_day_pct": None, "worst_day_pct": None,
           "underwater_days": None, "turnover_yr": None, "t_stat": None}
    n = len(pairs)
    if n >= 5:
        rs = [p[0] for p in pairs]
        rb = [p[1] for p in pairs]
        mean_s = sum(rs) / n
        mean_b = sum(rb) / n
        var_s = sum((r - mean_s) ** 2 for r in rs) / (n - 1)
        var_b = sum((r - mean_b) ** 2 for r in rb) / (n - 1)
        sd_s = math.sqrt(var_s)
        out["ann_vol_pct"] = round(sd_s * math.sqrt(252) * 100, 1)
        if sd_s > 0:
            out["sharpe"] = round(mean_s / sd_s * math.sqrt(252), 2)
        if var_b > 0:
            cov = sum((rs[i] - mean_s) * (rb[i] - mean_b) for i in range(n)) / (n - 1)
            beta = cov / var_b
            out["beta"] = round(beta, 2)
            out["alpha_ann_pct"] = round((mean_s - beta * mean_b) * 252 * 100, 1)
        # Sortino: penalize only downside deviation
        downs = [r for r in rs if r < 0]
        if downs:
            dd_dev = math.sqrt(sum(r * r for r in downs) / n)
            if dd_dev > 0:
                out["sortino"] = round(mean_s / dd_dev * math.sqrt(252), 2)
        out["best_day_pct"] = round(max(rs) * 100, 2)
        out["worst_day_pct"] = round(min(rs) * 100, 2)
        # t-statistic of daily excess return vs the benchmark — the honest
        # "is this luck?" number. |t| < 2 means the record proves nothing yet.
        ex = [rs[i] - rb[i] for i in range(n)]
        mean_ex = sum(ex) / n
        var_ex = sum((e - mean_ex) ** 2 for e in ex) / (n - 1)
        if var_ex > 0:
            out["t_stat"] = round(mean_ex / math.sqrt(var_ex / n), 2)
        # longest underwater stretch (sessions spent below a prior equity peak)
        curve = []
        for d in dates:
            v = hist[d].get(key)
            if v is not None:
                curve.append(v)
        peak, streak, worst_streak = -1.0, 0, 0
        for v in curve:
            if v >= peak:
                peak = v
                streak = 0
            else:
                streak += 1
                worst_streak = max(worst_streak, streak)
        out["underwater_days"] = worst_streak
    if n >= 20:
        first = None
        for d in dates:
            if hist[d].get(key) is not None:
                first = hist[d][key]
                break
        last = None
        for d in reversed(dates):
            if hist[d].get(key) is not None:
                last = hist[d][key]
                break
        if first and last and first > 0:
            out["ann_return_pct"] = round(((last / first) ** (252.0 / n) - 1.0) * 100, 1)
    wins = sum(t["pnl_pct"] for t in closed if t["pnl_pct"] > 0)
    losses = -sum(t["pnl_pct"] for t in closed if t["pnl_pct"] < 0)
    if losses > 0 and len(closed) >= 5:
        out["profit_factor"] = round(wins / losses, 2)
    if out["ann_return_pct"] is not None:
        # Calmar: annualized return over worst drawdown (needs both to exist)
        curve = [hist[d][key] for d in dates if hist[d].get(key) is not None]
        peak, mdd = -1.0, 0.0
        for v in curve:
            peak = max(peak, v)
            if peak > 0:
                mdd = min(mdd, v / peak - 1.0)
        if mdd < -0.001:
            out["calmar"] = round((out["ann_return_pct"] / 100.0) / abs(mdd), 2)
    if n >= 20 and slots and len(closed) >= 3:
        # rough annualized turnover: round trips per year, scaled by slot share
        out["turnover_yr"] = round(len(closed) * (252.0 / n) / slots, 1)
    return out


def build_watchlists(state, ranked_agg, ranked_gro, rank_basis, regime_note,
                     universe_ok, can_trade):
    """What each strategy is watching right now, and why it hasn't bought.
    The list is published even on days when nothing qualifies, so the site can
    always show its homework instead of an empty page."""
    out = {}
    ranked_by_key = {"aggressive": ranked_agg, "growth": ranked_gro}
    for key, ranked in ranked_by_key.items():
        strat = state["strategies"][key]
        meta = STRATEGY_META[key]
        free = meta["slots"] - len(strat["positions"])
        held = {p["symbol"] for p in strat["positions"]}
        cooling = set(strat.get("cooldown") or {})
        items = []
        for r in ranked[:8]:
            sym = r["symbol"]
            if sym in held:
                status = "already owned"
            elif sym in cooling:
                status = "recently traded — cooling off"
            elif free <= 0:
                status = "next in line when a slot frees up"
            else:
                status = "qualified — buying at the next close"
            items.append({
                "symbol": sym,
                "price": round(r.get("price") or 0, 2),
                "status": status,
                "why": r.get("thesis", ""),
                "r3m": round(r["r13"], 1) if r.get("r13") is not None else (
                    round(r["r63"] * 100, 1) if r.get("r63") is not None else None),
                "r6m": round(r["r26"], 1) if r.get("r26") is not None else None,
                "range_pos": round(r["pos"] * 100) if r.get("pos") is not None else None,
            })
        if regime_note and key == "aggressive":
            note = f"Nothing is being bought right now — {regime_note}."
        elif not ranked and rank_basis == "none":
            note = ("The scanner is still gathering enough market history to rank safely. "
                    "It grows every trading day and starts naming candidates as soon as it can.")
        elif not ranked:
            note = ("Nothing in the universe currently meets this strategy's standards. "
                    "That is a decision, not an outage — it re-checks at every close.")
        elif free <= 0:
            note = "All slots are full. These are the names queued for the next opening."
        elif not can_trade:
            note = ("These qualify now; purchases are made on the post-close run so every "
                    "fill uses an official closing price.")
        else:
            note = "Ranked highest by this strategy's model at the latest close."
        out[key] = {"note": note, "items": items,
                    "scanned": len(MOMENTUM if key == "aggressive" else SP_CORE),
                    "basis": rank_basis}
    # the intraday sleeve watches a fixed list all session
    dt = state["strategies"].get("daytrade") or {"positions": []}
    out["daytrade"] = {
        "note": ("These are the names it watches all session. It buys only the ones that gap "
                 "up 2–8% before 11:30 in the morning and are still climbing 20 minutes later."),
        "items": [{"symbol": s, "price": None, "status": "watching for a morning gap",
                   "why": "", "r3m": None, "r6m": None, "range_pos": None}
                  for s in DAYTRADE_WATCHLIST[:12]],
        "scanned": len(DAYTRADE_WATCHLIST), "basis": "intraday gaps"}
    out["longterm"] = {
        "note": ("This strategy owns its full allocation permanently and rebalances when the "
                 "weights drift — there is no watchlist by design."),
        "items": [], "scanned": len(LONGTERM_ALLOCATION), "basis": "fixed allocation"}
    return out


def build_site_payload(state, bars, as_of, new_picks, data_source, watchlists=None):
    hist = state["equity_history"]
    dates = sorted(hist.keys())
    curves = {"dates": dates}
    for k in ("daytrade", "aggressive", "growth", "longterm", "benchmark"):
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
                "bars_held": pos.get("bars_held", 0),
                "entry_time": pos.get("entry_time"),
                "thesis": pos.get("thesis", ""),
                "is_new": any(np["symbol"] == pos["symbol"] and np.get("entry_date") == pos["entry_date"]
                               for np in new_picks.get(key, [])),
            })
        equity = strat["cash"] + sum(p["current_value"] for p in positions)
        strategies[key] = {
            "key": key, "label": meta["label"], "icon": meta["icon"],
            "tagline": meta["tagline"], "horizon": meta["horizon"], "risk_note": meta["risk_note"],
            "equity": round(equity, 2), "cash": round(strat["cash"], 2),
            "max_hold_bars": meta.get("max_hold_bars"),
            **stats,
            "positions": positions,
            "recent_trades": strat["closed"][-12:][::-1],
            "recent_activity": strat["activity"][-8:][::-1],
            "pro": pro_metrics(dates, hist, key, strat["closed"], slots=meta.get("slots")),
            "watchlist": (watchlists or {}).get(key),
        }
    tuning_params = load_params()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tuning = {
        "reviews": len(tuning_params.get("history", [])),
        "last_change": (tuning_params.get("history") or [None])[-1],
        "benched": sorted(s for s, d in (tuning_params.get("blacklist") or {}).items()
                          if d >= today_str),
    }
    return {
        "version": 1,
        "tuning": tuning,
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
        "data_source": data_source,
        "methodology_note": (
            "All trades are simulated with paper money and booked NET of modeled slippage "
            "(5 bps per fill, 10 bps intraday; commissions are zero, matching modern retail; "
            "the SPY benchmark is frictionless by convention). Daily strategies fill at official "
            "closing prices. "
            "Every position is re-checked at every market close without exception; a sell enters the "
            "record only when the model actually fires the signal, booked at that day's real closing "
            "price — never at the planned target or stop. Picks are committed to git before outcomes "
            "are known — the commit history is the proof. Not financial advice."
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
    for key in STRATEGY_KEYS:
        st = s[key]
        wr = f"{st['win_rate_pct']:.0f}%" if st["win_rate_pct"] is not None else "—"
        lines.append(
            f"| {st['icon']} **{st['label']}** | ${st['equity']:,.2f} | {fmt_ret(st['total_return_pct'])} "
            f"| {fmt_ret(st['vs_spy_pct'])} | {st['max_drawdown_pct']:.2f}% | {st['closed_trades']} | {wr} |")
    lines.append(
        f"| 🧭 S&P 500 (SPY) benchmark | ${b['equity']:,.2f} | {fmt_ret(b['total_return_pct'])} | — | — | — | — |")
    lines.append("")
    for key in STRATEGY_KEYS:
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
    for key in STRATEGY_KEYS:
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
    parts = [f"{s[k]['icon']} {fmt_ret(s[k]['total_return_pct'])}" for k in STRATEGY_KEYS]
    msg = f"📊 {site['as_of_date']}: " + " | ".join(parts) + f" | SPY {fmt_ret(b['total_return_pct'])}"
    if trades_today:
        msg += f" · {trades_today} trade{'s' if trades_today != 1 else ''}"
    lines = [msg, ""]
    for key in STRATEGY_KEYS:
        st = s[key]
        pos_txt = ", ".join(
            f"{p['symbol']} {fmt_ret(p['pnl_pct'])}" for p in st["positions"][:6]) or "all cash"
        lines.append(f"{st['icon']} {st['label']}: ${st['equity']:,.2f} — {pos_txt}")
    lines.append(f"🧭 SPY benchmark: ${b['equity']:,.2f}")
    return "\n".join(lines)

# ---------------------------------------------------------------- main


# ---------------------------------------------------------------- self-tuning
# The learning loop. The machine re-examines its own rules every Sunday
# against ALL accumulated live data, and adapts them along PREDEFINED ladders
# with strict sample-size gates and out-of-sample validation. Predefined
# ladders (not free optimization) keep the degrees of freedom tiny — that is
# what separates adaptation from curve-fitting. Every change is committed to
# the public record with its before/after evidence, so each era of rules is
# auditable against the results it produced. The Nest Egg is never tuned:
# its entire thesis is that patience beats tinkering.

PARAMS_PATH = os.path.join(DATA_DIR, "params.json")

DAYTRADE_STOP_LADDER = [0.02, 0.025, 0.03]
DAYTRADE_GAP_LADDER = [0.02, 0.025, 0.03]
DAYTRADE_TARGET_LADDER = [0.03, 0.04]
LADDER_MIN_TRADES = 25
BLACKLIST_MIN_TRADES = 3
BLACKLIST_LOSS_PCT = -4.0
BLACKLIST_DAYS = 60
TUNE_MIN_SESSIONS = 220     # store depth needed before daily-strategy tuning
TUNE_VALIDATION = 63        # out-of-sample window (sessions)
TUNE_ADOPT_MARGIN = 1.0     # adopt only if better by this much (mean basket %, net)


def load_params():
    if os.path.exists(PARAMS_PATH):
        with open(PARAMS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"overrides": {}, "blacklist": {}, "history": [], "reviewed_trades": {}}


def apply_param_overrides(params):
    """Fold adopted overrides into STRATEGY_META at startup — one authority."""
    for key, over in (params.get("overrides") or {}).items():
        if key in STRATEGY_META and key != "longterm":
            STRATEGY_META[key].update(over)


def next_notch(ladder, current):
    """The next step up a predefined ladder, or None if already at the top."""
    best_i = min(range(len(ladder)), key=lambda i: abs(ladder[i] - current))
    return ladder[best_i + 1] if best_i + 1 < len(ladder) else None


def daytrade_ladder_decision(trades, meta):
    """Adaptation from the sleeve's own completed trades. Returns
    (changes_dict_or_None, reason). One notch maximum per review."""
    n = len(trades)
    if n < LADDER_MIN_TRADES:
        return None, f"only {n} completed trades since last review (need {LADDER_MIN_TRADES})"
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losers = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / n
    stop_outs = [t for t in losers if "stop" in (t.get("reason") or "")]
    changes = {}
    if win_rate < 0.40 and losers and len(stop_outs) / len(losers) >= 0.6:
        ns = next_notch(DAYTRADE_STOP_LADDER, meta["stop_pct"])
        ng = next_notch(DAYTRADE_GAP_LADDER, meta["min_gap"])
        if ns:
            changes["stop_pct"] = ns
        if ng:
            changes["min_gap"] = ng
        if changes:
            return changes, (f"win rate {win_rate * 100:.0f}% with {len(stop_outs)}/{len(losers)} "
                             f"losses at the stop — widening the stop and demanding stronger gaps")
    elif win_rate >= 0.55 and wins:
        avg_win = sum(t["pnl_pct"] for t in wins) / len(wins)
        nt = next_notch(DAYTRADE_TARGET_LADDER, meta["target_pct"])
        if avg_win >= meta["target_pct"] * 100 * 0.8 and nt:
            return {"target_pct": nt}, (f"win rate {win_rate * 100:.0f}% with winners averaging "
                                        f"{avg_win:.1f}% — raising the profit target")
    return None, f"win rate {win_rate * 100:.0f}% over {n} trades — within tolerances, no change"


def daytrade_blacklist_update(closed, today, existing):
    """Symbols that keep losing get benched for BLACKLIST_DAYS."""
    out = {s: d for s, d in (existing or {}).items() if d >= today}
    by_sym = {}
    for t in closed:
        by_sym.setdefault(t["symbol"], []).append(t["pnl_pct"])
    until = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=BLACKLIST_DAYS)).strftime("%Y-%m-%d")
    for sym, pnls in by_sym.items():
        if sym not in out and len(pnls) >= BLACKLIST_MIN_TRADES and sum(pnls) <= BLACKLIST_LOSS_PCT:
            out[sym] = until
    return out


def simulate_exit_params(key, store, symbols, session_lo, session_hi,
                         stop_pct, target_pct, max_hold):
    """Approximate exit-parameter backtest on the rolling store: every 5th
    session in [lo, hi), rank the universe as of that session, open the top
    slots equal-weight at the close (net of slippage) and walk each basket
    forward on closes with the given exits. Returns mean basket return (net,
    in %) or None if too little data. Growth's trend-break exit is omitted —
    this deliberately tunes only the stop/target/hold triad."""
    cal = [r[0] for r in store.get(BENCHMARK_SYMBOL) or []]
    if len(cal) < session_hi:
        return None
    sym_rows = {s: store.get(s) or [] for s in symbols}
    meta = STRATEGY_META[key]
    scorer = score_aggressive if key == "aggressive" else score_growth
    basket_returns = []
    for t in range(session_lo, session_hi - 1, 5):
        as_of = cal[t]
        bars_view = {}
        for s, rows in sym_rows.items():
            upto = [r for r in rows if r[0] <= as_of]
            if len(upto) < 65:
                continue
            bars_view[s] = {"dates": [r[0] for r in upto],
                            "close": {r[0]: r[1] for r in upto},
                            "volume": {}, "name": s, "divs": {}, "splits": {},
                            "has_events": False}
        ranked = scorer(bars_view, as_of)
        picks = ranked[:meta["slots"]]
        if not picks:
            continue
        rets = []
        for p in picks:
            rows = sym_rows[p["symbol"]]
            idx = next((i for i, r in enumerate(rows) if r[0] == as_of), None)
            if idx is None:
                continue
            entry = rows[idx][1] * (1 + SLIPPAGE_BPS_DAILY / 10000.0)
            stop = rows[idx][1] * (1 - stop_pct)
            target = rows[idx][1] * (1 + target_pct)
            exit_px = None
            path = rows[idx + 1: idx + 1 + (max_hold or 15)]
            for _, c in path:
                if c <= stop or c >= target:
                    exit_px = c
                    break
            if exit_px is None:
                exit_px = path[-1][1] if path else rows[idx][1]
            fill = exit_px * (1 - SLIPPAGE_BPS_DAILY / 10000.0)
            rets.append((fill / entry - 1.0) * 100.0)
        if rets:
            basket_returns.append(sum(rets) / len(rets))
    if len(basket_returns) < 8:
        return None
    return sum(basket_returns) / len(basket_returns)


DAILY_TUNE_GRID = {
    "aggressive": {"stop_pct": [0.06, 0.08, 0.10], "target_pct": [0.15, 0.20, 0.25],
                   "max_hold_bars": [10, 15, 20]},
    "growth": {"stop_pct": [0.08, 0.10, 0.12], "target_pct": [0.12, 0.15, 0.20],
               "max_hold_bars": [None]},
}


def tune_daily_strategy(key, store, params):
    """Walk-forward: choose the grid winner on the training window, then adopt
    it ONLY if it also beats the incumbent on the held-out validation window
    by a real margin. Returns (changes_or_None, reason)."""
    cal = [r[0] for r in store.get(BENCHMARK_SYMBOL) or []]
    n = len(cal)
    if n < TUNE_MIN_SESSIONS:
        return None, f"store holds {n} sessions (need {TUNE_MIN_SESSIONS}) — the loop arms itself as history grows"
    symbols = MOMENTUM if key == "aggressive" else SP_CORE
    val_lo, val_hi = n - TUNE_VALIDATION, n
    train_lo, train_hi = 65, n - TUNE_VALIDATION
    meta = STRATEGY_META[key]
    incumbent = (meta["stop_pct"], meta["target_pct"], meta.get("max_hold_bars"))
    grid = DAILY_TUNE_GRID[key]
    best, best_train = None, None
    for sp in grid["stop_pct"]:
        for tp in grid["target_pct"]:
            for mh in grid["max_hold_bars"]:
                r = simulate_exit_params(key, store, symbols, train_lo, train_hi, sp, tp, mh)
                if r is not None and (best_train is None or r > best_train):
                    best, best_train = (sp, tp, mh), r
    if best is None:
        return None, "not enough qualifying history in the training window"
    if best == incumbent:
        return None, f"incumbent parameters are already the training winner ({best_train:+.2f}% per basket)"
    inc_val = simulate_exit_params(key, store, symbols, val_lo, val_hi, *incumbent)
    new_val = simulate_exit_params(key, store, symbols, val_lo, val_hi, *best)
    if inc_val is None or new_val is None:
        return None, "validation window too thin to compare safely"
    if new_val - inc_val < TUNE_ADOPT_MARGIN:
        return None, (f"challenger {best} beat training but not validation "
                      f"({new_val:+.2f}% vs incumbent {inc_val:+.2f}%) — keeping current rules")
    changes = {"stop_pct": best[0], "target_pct": best[1]}
    if best[2] is not None:
        changes["max_hold_bars"] = best[2]
    return changes, (f"walk-forward adopted stop {best[0] * 100:.0f}% / target {best[1] * 100:.0f}%"
                     f"{f' / hold {best[2]}d' if best[2] else ''}: validation {new_val:+.2f}% per basket "
                     f"vs incumbent {inc_val:+.2f}%")


def run_tuning():
    """Weekly self-tuning pass. Only writes params.json (and a commit message)
    when something actually changes — quiet weeks leave no trace but a log."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    params = load_params()
    apply_param_overrides(params)
    state = load_state()
    store = load_price_store()
    notes, changed = [], False

    if state:
        dt = state["strategies"].get("daytrade", {})
        closed = dt.get("closed", [])
        reviewed = params["reviewed_trades"].get("daytrade", 0)
        fresh = closed[reviewed:]
        changes, reason = daytrade_ladder_decision(fresh, STRATEGY_META["daytrade"])
        print(f"⚡ ladder: {reason}")
        if changes:
            params["overrides"].setdefault("daytrade", {}).update(changes)
            params["reviewed_trades"]["daytrade"] = len(closed)
            params["history"].append({"date": today, "strategy": "daytrade",
                                      "changes": changes, "reason": reason})
            notes.append(f"⚡ {reason}")
            changed = True
        new_bl = daytrade_blacklist_update(closed, today, params.get("blacklist", {}))
        if new_bl != params.get("blacklist", {}):
            added = sorted(set(new_bl) - set(params.get("blacklist", {})))
            params["blacklist"] = new_bl
            if added:
                params["history"].append({"date": today, "strategy": "daytrade",
                                          "changes": {"blacklist": added},
                                          "reason": f"benched {', '.join(added)} for {BLACKLIST_DAYS} days "
                                                    f"after repeated losses"})
                notes.append(f"⚡ benched {', '.join(added)}")
            changed = True

    for key in ("aggressive", "growth"):
        changes, reason = tune_daily_strategy(key, store, params)
        print(f"{STRATEGY_META[key]['icon']} walk-forward: {reason}")
        if changes:
            params["overrides"].setdefault(key, {}).update(changes)
            params["history"].append({"date": today, "strategy": key,
                                      "changes": changes, "reason": reason})
            notes.append(f"{STRATEGY_META[key]['icon']} {reason}")
            changed = True

    if changed:
        save_json(PARAMS_PATH, params)
        with open(COMMIT_MSG_PATH, "w", encoding="utf-8") as f:
            f.write("📐 Sunday tuning: " + " · ".join(notes)[:400])
        print("Tuning changes adopted and recorded.")
    else:
        print("Tuning pass complete — no changes earned adoption this week.")


# ---------------------------------------------------------------- intraday mode


def prev_close_from_store(store, sym, today):
    """Most recent close strictly before `today` from the rolling price store."""
    for d, c in reversed(store.get(sym) or []):
        if d < today:
            return c
    return None


def daytrade_should_enter(px, prev_close, prior_mark, meta):
    """Entry test for one symbol at one intraday check.

    Three conditions, all evidence-driven:
      1. Gap band: up min_gap..max_gap vs yesterday's close. Small gaps are
         noise; monster gaps (>max_gap) statistically fade after the open.
      2. Confirmation: the price must be HIGHER than it was at the previous
         ~20-minute check (prior_mark). A gapper that is already fading never
         gets bought — this one check removes the worst gap-chase losers.
      3. No prior mark → no entry. The first sighting only registers the
         candidate; the machine needs to see momentum survive one interval.
    """
    if not prev_close or prev_close <= 0:
        return False
    gap = px / prev_close - 1.0
    if gap < meta["min_gap"] or gap > meta["max_gap"]:
        return False
    return prior_mark is not None and px > prior_mark


def run_intraday():
    """⚡ Day Trader loop — runs every ~20 minutes during market hours from its
    own schedule. Touches ONLY the daytrade sleeve: quotes the watchlist, exits
    on ±target/stop at the live quoted price, force-flattens everything in the
    last minutes of the session, and enters fresh gap-momentum names before the
    cutoff. Exits 0 without changes when the market is closed."""
    meta = STRATEGY_META["daytrade"]
    now = ny_now()
    hm = (now.hour, now.minute)
    if now.weekday() >= 5 or hm < (9, 35) or hm >= (16, 0):
        print(f"Market closed (NY {now:%a %H:%M}) — nothing to do.")
        return
    today = now.strftime("%Y-%m-%d")

    spy = finnhub_quote(BENCHMARK_SYMBOL)
    if not spy or market_date(spy["t"], "America/New_York") != today:
        print("SPY quote missing or stale (holiday?) — nothing to do.")
        return

    state = load_state()
    if state is None:
        print("No state yet — the daily engine bootstraps first. Nothing to do.")
        return
    ensure_strategies(state, today)
    strat = state["strategies"]["daytrade"]
    store = load_price_store()
    changed = False
    events = []
    time_str = now.strftime("%H:%M")

    # per-day confirmation memory: last check's price for each candidate
    intraday = state.get("intraday") or {}
    if intraday.get("date") != today:
        intraday = {"date": today, "marks": {}}
    marks = intraday["marks"]

    # -- exits first: stop / target / end-of-day flat, at live quoted prices
    eod = hm >= meta["eod_flat"]
    for pos in list(strat["positions"]):
        q = finnhub_quote(pos["symbol"])
        time.sleep(1.1)
        if not q:
            continue
        px = q["c"]
        pos["last_price"] = px
        # breakeven ratchet: once up 1.5%, the stop moves to the entry price —
        # a working trade is never allowed to turn into a loss
        if not pos.get("be_locked") and px >= pos["entry_price"] * (1 + meta["be_trigger"]):
            pos["stop_price"] = max(pos["stop_price"], pos["entry_price"])
            pos["be_locked"] = True
            events.append(f"{pos['symbol']} up {((px / pos['entry_price']) - 1) * 100:.1f}% — "
                          f"stop moved to breakeven")
            changed = True
        reason = None
        if px <= pos["stop_price"]:
            reason = "breakeven stop" if pos.get("be_locked") else "stop −2%"
        elif px >= pos["target_price"]:
            reason = "target +3%"
        elif eod:
            reason = "end of day — flat by the close"
        if reason:
            strat["positions"].remove(pos)
            close_position(strat, pos, today, px, reason, slippage_bps=SLIPPAGE_BPS_INTRADAY)
            t = strat["closed"][-1]
            t["entry_time"] = pos.get("entry_time")
            t["exit_time"] = time_str
            events.append(f"sold {pos['symbol']} @ ${px:,.2f} ({t['pnl_pct']:+.2f}%, {reason})")
            changed = True

    # -- entries: confirmed gap-momentum in the morning window only, and only
    #    when the broad market itself isn't selling off
    tape_ok = (spy["c"] / (prev_close_from_store(store, BENCHMARK_SYMBOL, today) or spy["c"]) - 1.0) \
        >= meta["tape_filter"]
    traded_today = {t["symbol"] for t in strat["closed"] if t["exit_date"] == today}
    held = {p["symbol"] for p in strat["positions"]}
    in_window = hm < meta["entry_cutoff"]
    if in_window and tape_ok and len(strat["positions"]) < meta["slots"] and strat["cash"] > 100:
        blacklist = load_params().get("blacklist", {})
        candidates = []
        for sym in DAYTRADE_WATCHLIST:
            if sym in held or sym in traded_today:
                continue
            if blacklist.get(sym, "") >= today:
                continue          # benched after repeated losses
            prev_close = prev_close_from_store(store, sym, today)
            if not prev_close:
                continue
            q = finnhub_quote(sym)
            time.sleep(1.1)
            if not q or market_date(q["t"], "America/New_York") != today:
                continue
            px = q["c"]
            gap = px / prev_close - 1.0
            if daytrade_should_enter(px, prev_close, marks.get(sym), meta):
                candidates.append({"symbol": sym, "px": px, "gap": gap})
            # remember this check's price — next run's confirmation baseline
            if meta["min_gap"] <= gap <= meta["max_gap"]:
                marks[sym] = px
                changed = True
            elif sym in marks:
                del marks[sym]
                changed = True
        candidates.sort(key=lambda c: c["gap"], reverse=True)
        empty = meta["slots"] - len(strat["positions"])
        for cand in candidates[:empty]:
            budget = min(strat["cash"] / empty, strat["cash"])
            if budget < 100:
                break
            px = cand["px"]
            fill = px * (1.0 + SLIPPAGE_BPS_INTRADAY / 10000.0)
            pos = {
                "symbol": cand["symbol"], "name": cand["symbol"],
                "shares": budget / fill, "entry_price": fill, "entry_date": today,
                "raw_entry_price": px,
                "entry_time": time_str,
                "target_price": px * (1 + meta["target_pct"]),
                "stop_price": px * (1 - meta["stop_pct"]),
                "thesis": (f"Up {cand['gap'] * 100:.1f}% on the day at {time_str} ET and still "
                           f"climbing 20 minutes after first sighting. Out at +3%, −2%, or the "
                           f"closing bell — and the stop moves to breakeven once up 1.5%."),
                "last_price": px, "bars_held": 0, "be_locked": False,
                "splits_applied": [], "divs_credited": [],
            }
            strat["cash"] -= budget
            strat["positions"].append(pos)
            strat["activity"].append(
                {"date": today, "text": f"⚡ {time_str} ET BUY {cand['symbol']} @ ${px:,.2f} "
                 f"(+{cand['gap'] * 100:.1f}% confirmed gap)"})
            events.append(f"bought {cand['symbol']} @ ${px:,.2f} (+{cand['gap'] * 100:.1f}% confirmed gap)")
            marks.pop(cand["symbol"], None)
            empty -= 1
            changed = True
    elif in_window and not tape_ok:
        print(f"  tape filter: SPY down more than {abs(meta['tape_filter']) * 100:.0f}% — no new entries.")

    state["intraday"] = intraday
    open_mv = sum(p["shares"] * (p.get("last_price") or p["entry_price"]) for p in strat["positions"])
    equity = strat["cash"] + open_mv
    if not changed and not strat["positions"]:
        print(f"⚡ {time_str} ET — no signals, flat. Equity ${equity:,.2f}. No commit needed.")
        return

    # positions are marked to the live quote; the daily equity curve still only
    # gets one point per session (recorded flat at the daily run)
    strat["activity"] = strat["activity"][-60:]
    state["last_run_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_picks = {"daytrade": [p for p in strat["positions"] if p["entry_date"] == today]}
    site = build_site_payload(
        state, {}, max(state["equity_history"].keys(), default=today), new_picks,
        "live Finnhub quotes (intraday) + rolling price store")
    site["intraday_date"] = today
    site["intraday_equity"] = round(equity, 2)
    save_json(STATE_PATH, state)
    save_json(SITE_PATH, site)
    day_pnl = sum(t["pnl_pct"] for t in strat["closed"] if t["exit_date"] == today)
    msg = f"⚡ {time_str} ET: " + ("; ".join(events) if events else "marking positions")
    msg += f" · equity ${equity:,.2f}"
    if day_pnl:
        msg += f" · day trades net {day_pnl:+.2f}%"
    with open(COMMIT_MSG_PATH, "w", encoding="utf-8") as f:
        f.write(msg)
    print(msg)


def main():
    if "--tune" in sys.argv:
        print(f"=== Self-tuning pass {datetime.now(timezone.utc).isoformat()} ===")
        run_tuning()
        return
    if "--intraday" in sys.argv or os.environ.get("ADVISOR_INTRADAY") == "1":
        print(f"=== Day-trader intraday check {datetime.now(timezone.utc).isoformat()} ===")
        apply_param_overrides(load_params())
        run_intraday()
        return
    print(f"=== Advisor run {datetime.now(timezone.utc).isoformat()} ===")
    apply_param_overrides(load_params())

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
    finnhub_floor = False
    if BENCHMARK_SYMBOL not in bars_critical and FINNHUB_KEY:
        # absolute floor: end-of-day closes from Finnhub keep the daily record
        # alive even when every history source is blocked
        print("History sources down — falling back to Finnhub end-of-day closes.")
        floor_bars = finnhub_close_bars(critical)
        if BENCHMARK_SYMBOL in floor_bars:
            bars_critical = floor_bars
            finnhub_floor = True
    if BENCHMARK_SYMBOL not in bars_critical:
        # No data this pass. That is routine for the pre-open catch-up run when
        # every history source is rate-limited and the market has not closed yet
        # (the Finnhub floor refuses to treat a live price as a close). Exit
        # quietly if the record is already current — the post-close run does the
        # real work. Only shout when the record is genuinely falling behind.
        prior = load_state()
        recorded = sorted((prior or {}).get("equity_history", {}).keys())
        last = recorded[-1] if recorded else None
        behind = 99
        if last:
            behind = (datetime.now(timezone.utc).date()
                      - datetime.strptime(last, "%Y-%m-%d").date()).days
        if prior and behind <= 4:
            print(f"No market data available this pass (last record {last}, {behind}d ago) — "
                  "nothing to do. The post-close run will catch up.")
            sys.exit(0)
        print("FATAL: no benchmark data and the record is falling behind — "
              f"last recorded session {last}.")
        sys.exit(1)
    missing_held = [s for s in held_syms if s not in bars_critical]
    if missing_held:
        print(f"  warning: no data for held positions: {missing_held}")

    # 2) wide scanning universe. Live batched closes when Yahoo/Stooq work;
    #    in floor mode, one Finnhub quote per symbol (60/min) still captures
    #    today's close for every universe name so the price store keeps growing.
    universe_syms = [s for s in dict.fromkeys(MOMENTUM + SP_CORE) if s not in bars_critical]
    if finnhub_floor:
        print("  (floor mode: quoting the universe via Finnhub to feed the price store…)")
        bars_universe = finnhub_close_bars(universe_syms)
    else:
        bars_universe = fetch_universe(universe_syms)

    bars_live = {**bars_universe, **bars_critical}  # full-history data wins
    trim_partial_session(bars_live)

    # 3) fold everything into the rolling price store, then build the working
    #    bars: live history with events when we have it, store depth otherwise
    store = load_price_store()
    merge_into_store(store, bars_live)
    save_json(PRICES_PATH, store, compact=True)
    bars = {}
    for sym in set(list(store.keys()) + list(bars_live.keys())):
        live = bars_live.get(sym)
        if live and live.get("has_events", True):
            bars[sym] = live
        else:
            bars[sym] = store_history(store, sym) or live
    src_bits = []
    if any(h.get("has_events") for h in bars_live.values()):
        src_bits.append("Yahoo/Stooq daily history with div+split events")
    if finnhub_floor:
        src_bits.append("Finnhub official end-of-day closes")
    src_bits.append(f"rolling price store ({len(store)} symbols)")
    data_source = " + ".join(src_bits)
    print(f"  data sources this run: {data_source}")

    fresh_deep = sum(
        1 for s in universe_syms
        if bars.get(s) and len(bars[s]["dates"]) >= 65)
    universe_ok = fresh_deep >= 80
    if not universe_ok:
        print(f"  warning: only {fresh_deep} universe symbols have 65+ sessions of history — "
              "no new momentum/growth picks this run (store grows daily until ranking unlocks).")

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
    ensure_strategies(state, as_of)

    # 1) replay portfolios day-by-day since last run
    for key in STRATEGY_KEYS:
        strat = state["strategies"][key]
        n_before = len(strat["closed"])
        marks = replay_strategy(key, strat, bars, calendar, as_of)
        for d, v in marks.items():
            state["equity_history"].setdefault(d, {})[key] = round(v, 2)
        closed_now = strat["closed"][n_before:]
        for t in closed_now:
            print(f"  {STRATEGY_META[key]['icon']} closed {t['symbol']}: {t['pnl_pct']:+.2f}% ({t['reason']})")

    # the ⚡ intraday sleeve trades via its own schedule; the daily run just
    # safety-flattens anything left open and records one equity point per session
    dt_marks = consolidate_daytrade(state["strategies"]["daytrade"], bars, calendar, as_of)
    for d, v in dt_marks.items():
        state["equity_history"].setdefault(d, {})["daytrade"] = round(v, 2)

    bench_marks = replay_benchmark(state["benchmark"], bars, calendar, as_of, can_trade)
    for d, v in bench_marks.items():
        state["equity_history"].setdefault(d, {})["benchmark"] = round(v, 2)

    # 2) longterm allocation management (inception buy / drift rebalance)
    manage_longterm(state["strategies"]["longterm"], bars, as_of, can_trade)

    # 3) fill empty aggressive/growth slots with freshly ranked picks.
    #    Two ranking paths: the native price-history model once the store is
    #    deep enough, otherwise Finnhub's precomputed return windows — which
    #    let both models work from day one instead of waiting months.
    new_picks = {"aggressive": [], "growth": [], "longterm": []}
    ranked_agg, ranked_gro, rank_basis = [], [], "none"
    agg_wants = STRATEGY_META["aggressive"]["slots"] - len(state["strategies"]["aggressive"]["positions"])
    gro_wants = STRATEGY_META["growth"]["slots"] - len(state["strategies"]["growth"]["positions"])

    if universe_ok:
        rank_basis = "price-history"
        agg_universe = {s: h for s, h in bars.items() if s in set(MOMENTUM)}
        gro_universe = {s: h for s, h in bars.items() if s in set(SP_CORE)}
        ranked_agg = score_aggressive(agg_universe, as_of)
        ranked_gro = score_growth(gro_universe, as_of)
    elif FINNHUB_KEY and (agg_wants > 0 or gro_wants > 0):
        rank_basis = "metrics"
        print("  price store still shallow — ranking from Finnhub metric windows instead.")
        uni = list(dict.fromkeys(MOMENTUM + SP_CORE))
        metrics = fetch_universe_metrics(uni, ny_today)
        quotes = {s: (bars[s]["close"][bars[s]["dates"][-1]]
                      if bars.get(s) and bars[s]["dates"] else None) for s in uni}
        quotes = {s: p for s, p in quotes.items() if p}
        ranked_agg = score_aggressive_metrics(
            {s: m for s, m in metrics.items() if s in set(MOMENTUM)}, quotes)
        ranked_gro = score_growth_metrics(
            {s: m for s, m in metrics.items() if s in set(SP_CORE)}, quotes)

    # regime filter: high-beta momentum bleeds when the broad market is below
    # its own 50-day trend — the Fast Mover sits in cash then
    risk_on, regime_note = True, ""
    spy_h = bars.get(BENCHMARK_SYMBOL)
    spy_closes = [spy_h["close"][d] for d in spy_h["dates"]] if spy_h else []
    spy_s50 = sma(spy_closes, 50)
    if spy_s50 is not None:
        risk_on = spy_closes[-1] > spy_s50
    elif rank_basis == "metrics":
        # no 50-day history yet: use the benchmark's own 52-week range position
        bm = (load_metrics_cache().get("metrics") or {}).get(BENCHMARK_SYMBOL) or {}
        pos = _range_pos(spy_closes[-1] if spy_closes else None, bm.get("lo52"), bm.get("hi52"))
        if pos is not None:
            risk_on = pos >= 0.40
    if not risk_on:
        regime_note = "the market itself is in a downtrend, so it is deliberately holding cash"
        print("  regime filter: market in a downtrend — Fast Mover takes no new positions.")
        ranked_agg = []
    if ranked_agg or ranked_gro:
        print(f"Ranked candidates ({rank_basis}): aggressive {len(ranked_agg)}, growth {len(ranked_gro)}")
    new_picks["aggressive"] = fill_slots(
        "aggressive", state["strategies"]["aggressive"], ranked_agg, as_of, can_trade)
    new_picks["growth"] = fill_slots(
        "growth", state["strategies"]["growth"], ranked_gro, as_of, can_trade)
    if new_picks["aggressive"] or new_picks["growth"]:
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
    for key in STRATEGY_KEYS:
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
        1 for k in ("daytrade", "aggressive", "growth") for t in state["strategies"][k]["closed"]
        if t["exit_date"] == as_of) + sum(len(v) for v in new_picks.values())
    watchlists = build_watchlists(
        state, ranked_agg, ranked_gro, rank_basis, regime_note, universe_ok, can_trade)
    site = build_site_payload(state, bars, as_of, new_picks, data_source,
                              watchlists=watchlists)

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
    for key in STRATEGY_KEYS:
        st = site["strategies"][key]
        print(f"  {st['icon']} {st['label']}: ${st['equity']:,.2f} ({fmt_ret(st['total_return_pct'])})")
    print(f"  🧭 SPY: ${site['benchmark']['equity']:,.2f} ({fmt_ret(site['benchmark']['total_return_pct'])})")


if __name__ == "__main__":
    main()
