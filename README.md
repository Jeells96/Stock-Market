# 📈 Jaren's Stock Market Tracker — Autonomous Advisor Edition

A self-driving investment advisor that **runs itself**, **keeps score honestly**, and
**can't rewrite its own history**. Live site: the repo's GitHub Pages deployment of
[`index.html`](index.html).

<!-- ADVISOR:START -->
### 📊 Live Track Record — as of 2026-07-31 (run #2)

| Strategy | Equity | Return | vs S&P 500 | Win Rate |
|---|---|---|---|---|
| 🚀 Get Rich Quick | $10,000.00 | +0.00% | +0.00% | — |
| 📈 Dependable Growth | $10,000.00 | +0.00% | +0.00% | — |
| 🏛️ Long-Term Success | $10,000.00 | +0.00% | +0.00% | — |
| 🧭 SPY benchmark | $10,000.00 | +0.00% | — | — |

_Updated automatically every trading day · [full report](REPORT.md) · [verify in commit history](../../commits/main/data)_
<!-- ADVISOR:END -->

## The three strategies

Every strategy manages its own **$10,000 paper-money portfolio**, fully automatically.
An S&P 500 (SPY) buy-and-hold benchmark runs alongside them so there's always an
honest yardstick.

| | Strategy | How it works | Risk |
|---|---|---|---|
| 🚀 | **Get Rich Quick** | Short-term momentum swings on high-beta names. Ranked by 1-week + 1-month momentum, RSI-capped. Target +20%, hard stop −8%, forced exit after 15 trading days. | Very high |
| 📈 | **Dependable Growth** | Quality large caps in confirmed uptrends (price above rising 50- and 200-day averages, positive 6-month return, volatility-capped). Target +15%, stop −10%, exits on a 200-day trend break. | Moderate |
| 🏛️ | **Long-Term Success** | Fixed diversified ETF allocation — 40% VOO, 20% QQQ, 20% SCHD, 10% VXUS, 10% BND — bought once and rebalanced automatically when weights drift. | Lower |
| ⚡ | **Day Trader** | Intraday gap-momentum on a 45-name high-beta watchlist, checked ~every 20 minutes during market hours. +2% target, −1% stop, always flat by the close — never holds overnight. Fills book at the live quoted price of the check, timestamped by the commit. | Extreme |

## How it fires by itself

A scheduled GitHub Action ([`.github/workflows/advisor.yml`](.github/workflows/advisor.yml))
runs [`scripts/advisor.py`](scripts/advisor.py) twice every trading day:

- **Post-close (~5:45pm ET)** — the trading run: marks outcomes on the session that just
  ended, applies stops/targets, and enters new picks at that day's closing price. The
  commit lands while tomorrow's outcome is still unknown — that's the proof.
- **Pre-open (~9:10am ET)** — a catch-up run: replays anything a failed evening run
  missed. It never opens positions (the engine refuses to trade at a stale close, so
  overnight gaps can't be gamed and a delayed cron firing mid-session is harmless).

Each run fetches free Yahoo Finance data (no API key needed), replays every trading day
since the previous run — crediting dividends, applying splits, checking stops and targets
on daily closes only — then commits the updated record to this repository.

The ⚡ Day Trader sleeve has its own schedule
([`.github/workflows/daytrader.yml`](.github/workflows/daytrader.yml)): checks roughly
every 20 minutes during market hours, trading at live quoted prices and committing every
action as it happens. The daily engine safety-flattens anything it might leave open, so
the sleeve can never hold a position overnight.

> **To activate:** this workflow must be on the `main` branch (GitHub only fires schedules
> from the default branch). It can also be triggered manually from the **Actions** tab →
> *Autonomous Investment Advisor* → *Run workflow*.

## How to check on it without opening the website

1. **Commit feed** — every run's commit message is a scoreboard, e.g.
   `📊 2026-08-05: 🚀 +3.1% | 📈 +1.2% | 🏛️ +0.4% | SPY +0.6%`.
   Watch it from the GitHub app, the [commits page](../../commits/main), or subscribe to
   the Atom feed at `https://github.com/jeells96/Stock-Market/commits/main.atom`.
2. **[`REPORT.md`](REPORT.md)** — a full daily report: every position, entry, target,
   stop, P&L, and recent closed trades.
3. **This README** — the summary table above is rewritten by every run.
4. **Failure alerts** — if a run errors, GitHub emails the repo owner automatically.

## Why the track record is trustworthy

- Every pick is committed to git **before its outcome is known**. The commit timestamp
  is GitHub's, not the bot's.
- History can't be silently edited — any tampering would show in the
  [commit history of `data/`](../../commits/main/data).
- Daily snapshots are archived in [`data/history/`](data/history) — one immutable
  JSON per trading day.
- Accounting is deliberately conservative and auditable: all fills at daily **closing**
  prices (no intraday fantasy fills), dividends credited as cash, splits adjusted,
  missed runs replayed day-by-day with no lookahead.

## Repo layout

```
index.html                      the website (Advisor · Scanner · Portfolio · Track Record)
scripts/advisor.py              the autonomous engine (pure Python, stdlib + requests)
.github/workflows/advisor.yml   the schedule that fires it
data/state.json                 canonical engine state (positions, cash, history)
data/site.json                  payload the website renders
data/history/YYYY-MM-DD.json    immutable daily snapshots
REPORT.md                       human-readable daily report
```

---

_Simulated paper-money portfolios for an educational project. **Not financial advice.**_
