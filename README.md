# 📈 Jaren's Stock Market Tracker — Autonomous Advisor Edition

A self-driving investment advisor that **runs itself**, **keeps score honestly**, and
**can't rewrite its own history**. Live site: the repo's GitHub Pages deployment of
[`index.html`](index.html).

<!-- ADVISOR:START -->
### 📊 Live Track Record — as of 2026-08-06 (run #12)

| Strategy | Equity | Return | vs S&P 500 | Win Rate |
|---|---|---|---|---|
| ⚡ Day Trader | $10,000.00 | +0.00% | -2.88% | — |
| 🚀 Get Rich Quick | $9,778.72 | -2.21% | -5.09% | — |
| 📈 Dependable Growth | $9,885.02 | -1.15% | -4.03% | — |
| 🏛️ Long-Term Success | $10,227.02 | +2.27% | -0.61% | — |
| 🧭 SPY benchmark | $10,288.21 | +2.88% | — | — |

_Updated automatically every trading day · [full report](REPORT.md) · [verify in commit history](../../commits/main/data)_
<!-- ADVISOR:END -->

## The four strategies

Every strategy manages its own **$10,000 paper-money portfolio**, fully automatically.
An S&P 500 (SPY) buy-and-hold benchmark runs alongside them so there's always an
honest yardstick.

| | Strategy | How it works | Risk |
|---|---|---|---|
| 🚀 | **Get Rich Quick** | Quality momentum on high-beta names: ranked by volatility-adjusted 3-month + 1-month climbs (1-week spikes filtered out, RSI 45–75 band), new entries only when SPY is above its 50-day trend. Buys **one share of every qualifying name** — no slot cap, so every recommendation is scored. Target +20%, stop −8% ratcheting to breakeven at +10%, forced exit after 15 trading days. | Very high |
| 📈 | **Dependable Growth** | Quality large caps in confirmed uptrends (price above rising 50- and 200-day averages, positive 6-month return, volatility-capped). Buys **one share of every qualifying name** — no slot cap. Target +15%, stop −10%, exits on a 200-day trend break. | Moderate |
| 🏛️ | **Long-Term Success** | Fixed diversified ETF allocation — 40% VOO, 20% QQQ, 20% SCHD, 10% VXUS, 10% BND — bought once and rebalanced automatically when weights drift. | Lower |
| ⚡ | **Day Trader** | Intraday momentum on a 45-name watchlist. One rule, decidable from a single quote so the website's verdict and the engine's action can never disagree: **up 2–8% on the day and still above its opening price**, before 11:30am ET, only when SPY isn't down 1%+, and never a name with negative news that week. +3% target, −2% stop ratcheting to breakeven at +1.5%, always flat by the close. Fills book at the live quoted price of the check, timestamped by the commit. | Extreme |

## How it fires by itself

A scheduled GitHub Action ([`.github/workflows/advisor.yml`](.github/workflows/advisor.yml))
runs [`scripts/advisor.py`](scripts/advisor.py) twice every trading day:

- **Post-close (~5:45pm ET)** — the trading run: marks outcomes on the session that just
  ended, applies stops/targets, and enters new picks at that day's closing price. The
  commit lands while tomorrow's outcome is still unknown — that's the proof.
- **Pre-open (~6:40am ET)** — a catch-up run: replays anything a failed evening run
  missed. It never opens positions (the engine refuses to trade at a stale close, so
  overnight gaps can't be gamed and a delayed cron firing mid-session is harmless).
  Scheduled early on purpose — GitHub's cron often fires late, and this leaves hours of
  slack before the open.

Each run replays every trading day since the previous run — crediting dividends, applying
splits, checking stops and targets on daily closes only — then commits the updated record
to this repository.

**Data resilience.** Yahoo and Stooq both rate-limit datacenter IPs, so the engine falls
back through Yahoo chart → Yahoo spark → Stooq → Finnhub end-of-day quotes, and keeps its
own rolling price store (`data/prices.json`) that grows one close per symbol per session.
Stock ranking works from day one either way: when the store is still shallow, the two
stock-picking strategies rank on Finnhub's precomputed trailing return windows (3-month,
6-month, 1-year, volatility, 52-week range) instead of waiting months for stored history.
If every source is blocked on a pre-open catch-up pass, the run exits quietly rather than
failing — the post-close run does the real work.

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

- **Every suggestion is a purchase.** The site never highlights a "suggested buy" it
  doesn't take into its own paper portfolio, so every recommendation — not a cherry-picked
  subset — gets scored in the public record.
- **New money can't flatter the numbers.** Buying one share of everything that qualifies
  means the pot sometimes needs topping up. Each sleeve is therefore unitized like a fund:
  contributions buy units at the prevailing unit price, and every published percentage is
  what a fixed $10,000 stake would have done — a true time-weighted return, directly
  comparable to the S&P line beside it.
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
