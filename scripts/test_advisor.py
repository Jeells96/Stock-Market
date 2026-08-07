"""Offline test harness for advisor.py — verifies the money math with
synthetic data, no network needed. Run: python3 scripts/test_advisor.py"""
import importlib.util
import os
import sys

spec = importlib.util.spec_from_file_location(
    "advisor", os.path.join(os.path.dirname(os.path.abspath(__file__)), "advisor.py"))
adv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adv)


DAYS = ["2026-07-01", "2026-07-02", "2026-07-06", "2026-07-07", "2026-07-08"]


def mk_hist(closes, divs=None, splits=None, name="Test Co"):
    dates = DAYS[: len(closes)]
    return {
        "dates": dates,
        "close": {d: c for d, c in zip(dates, closes)},
        "volume": {d: 1e6 for d in dates},
        "name": name,
        "divs": divs or {},
        "splits": splits or {},
    }


def approx(a, b, tol=0.01):
    assert abs(a - b) < tol, f"expected {b}, got {a}"


fails = 0


def check(label, fn):
    global fails
    try:
        fn()
        print(f"  PASS {label}")
    except AssertionError as e:
        fails += 1
        print(f"  FAIL {label}: {e}")


# ---------- test 1: replay stop-loss exit + cash conservation ----------
def t1():
    strat = {"cash": 0.0, "positions": [{
        "symbol": "XYZ", "name": "XYZ", "shares": 100.0, "entry_price": 100.0,
        "entry_date": DAYS[0], "target_price": 120.0, "stop_price": 92.0,
        "thesis": "", "last_price": 100.0, "bars_held": 0,
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[0], "activity": []}
    bars = {"XYZ": mk_hist([100, 95, 90, 91, 92])}  # day3 close 90 <= stop 92
    cal = DAYS
    marks = adv.replay_strategy("aggressive", strat, bars, cal, DAYS[4])
    assert len(strat["closed"]) == 1, f"expected 1 closed trade, got {len(strat['closed'])}"
    t = strat["closed"][0]
    assert t["reason"] == "stop loss", t["reason"]
    assert t["exit_date"] == DAYS[2], t["exit_date"]
    approx(t["exit_price"], 89.955)        # $90 close net of 5 bps slippage
    approx(t["raw_exit_price"], 90.0)
    approx(strat["cash"], 8995.5)          # 100 sh × $89.955
    approx(t["pnl_pct"], -10.05, tol=0.02)
    approx(marks[DAYS[1]], 9500.0)         # marked at day-2 close pre-exit
    approx(marks[DAYS[2]], 8995.5)
    approx(marks[DAYS[4]], 8995.5)         # all cash after exit
check("stop-loss exit at close, cash conserved", t1)

# ---------- test 2: dividend credit ----------
def t2():
    strat = {"cash": 0.0, "positions": [{
        "symbol": "DIV", "name": "DIV", "shares": 50.0, "entry_price": 40.0,
        "entry_date": DAYS[0], "target_price": None, "stop_price": None,
        "thesis": "", "last_price": 40.0, "bars_held": 0,
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[0], "activity": []}
    bars = {"DIV": mk_hist([40, 40, 40, 40, 40], divs={DAYS[2]: 1.5})}
    adv.replay_strategy("longterm", strat, bars, DAYS, DAYS[4])
    approx(strat["cash"], 75.0)            # 50 sh × $1.50
    assert any("dividend" in a["text"] for a in strat["activity"])
check("dividend credited to cash on ex-date", t2)

# ---------- test 3: split adjustment (no phantom P&L) ----------
def t3():
    strat = {"cash": 0.0, "positions": [{
        "symbol": "SPL", "name": "SPL", "shares": 10.0, "entry_price": 200.0,
        "entry_date": DAYS[0], "target_price": 240.0, "stop_price": 184.0,
        "thesis": "", "last_price": 200.0, "bars_held": 0,
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[0], "activity": []}
    # 2:1 split on day 2 — price halves, no exit should fire, value unchanged
    bars = {"SPL": mk_hist([200, 100, 101, 102, 103], splits={DAYS[1]: 2.0})}
    marks = adv.replay_strategy("aggressive", strat, bars, DAYS, DAYS[4])
    assert len(strat["closed"]) == 0, f"split wrongly triggered exit: {strat['closed']}"
    pos = strat["positions"][0]
    approx(pos["shares"], 20.0)
    approx(pos["entry_price"], 100.0)
    approx(pos["stop_price"], 92.0)
    approx(marks[DAYS[1]], 2000.0)         # 20 sh × $100 — no phantom loss
    approx(marks[DAYS[4]], 2060.0)
check("split adjusts position without phantom P&L", t3)

# ---------- test 4: max-hold time exit ----------
def t4():
    strat = {"cash": 0.0, "positions": [{
        "symbol": "TIM", "name": "TIM", "shares": 10.0, "entry_price": 100.0,
        "entry_date": DAYS[0], "target_price": 200.0, "stop_price": 50.0,
        "thesis": "", "last_price": 100.0, "bars_held": 13,   # 13 bars already held
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[0], "activity": []}
    bars = {"TIM": mk_hist([100, 101, 102, 103, 104])}
    adv.replay_strategy("aggressive", strat, bars, DAYS, DAYS[4])
    assert len(strat["closed"]) == 1
    t = strat["closed"][0]
    assert t["reason"].startswith("time exit"), t["reason"]
    assert t["exit_date"] == DAYS[2], t["exit_date"]  # 13+2=15 bars on day index 2
check("15-bar time exit fires", t4)

# ---------- test 5: growth trend-break needs 200 bars (no false exit) ----------
def t5():
    strat = {"cash": 0.0, "positions": [{
        "symbol": "GRO", "name": "GRO", "shares": 10.0, "entry_price": 100.0,
        "entry_date": DAYS[0], "target_price": 115.0, "stop_price": 90.0,
        "thesis": "", "last_price": 100.0, "bars_held": 0,
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[0], "activity": []}
    bars = {"GRO": mk_hist([100, 99, 98, 97, 96])}  # short history → sma(200) None
    adv.replay_strategy("growth", strat, bars, DAYS, DAYS[4])
    assert len(strat["closed"]) == 0, strat["closed"]
check("growth: no trend-break exit without 200 bars of history", t5)

# ---------- test 6: one share of EVERY qualifying name, cooldown respected ----------
def t6():
    strat = {"cash": 10000.0, "positions": [], "closed": [], "cooldown": {"HOT": DAYS[3]},
             "last_eval_date": DAYS[4], "activity": []}
    ranked = [
        {"symbol": "HOT", "name": "Hot", "price": 10.0, "thesis": "t"},    # cooling down
        {"symbol": "AAA", "name": "A", "price": 20.0, "thesis": "t"},
        {"symbol": "BBB", "name": "B", "price": 30.0, "thesis": "t"},
        {"symbol": "CCC", "name": "C", "price": 40.0, "thesis": "t"},
        {"symbol": "DDD", "name": "D", "price": 50.0, "thesis": "t"},
        {"symbol": "EEE", "name": "E", "price": 60.0, "thesis": "t"},
        {"symbol": "FFF", "name": "F", "price": 70.0, "thesis": "t"},
    ]
    opened = adv.buy_recommendations("aggressive", strat, ranked, DAYS[4], True)
    syms = [p["symbol"] for p in opened]
    assert "HOT" not in syms, f"cooldown ignored: {syms}"
    assert len(opened) == 6, f"every non-cooling candidate is bought — no slot cap ({len(opened)})"
    for p in opened:
        approx(p["shares"], 1.0, tol=1e-9)   # exactly one share each
    spent = 10000.0 - strat["cash"]
    slip = 1.0 + adv.SLIPPAGE_BPS_DAILY / 10000.0
    approx(spent, sum(r["price"] for r in ranked if r["symbol"] != "HOT") * slip, tol=0.01)
    # not trading (pre-open replay) buys nothing
    strat2 = {"cash": 10000.0, "positions": [], "closed": [], "cooldown": {}, "activity": []}
    assert adv.buy_recommendations("aggressive", strat2, ranked, DAYS[4], False) == []
check("one share of every qualifying name, cooldown respected", t6)

# ---------- test 7: longterm inception + rebalance ----------
def t7():
    bars = {s: mk_hist([100, 100, 100, 100, 100], name=s) for s in adv.LONGTERM_ALLOCATION}
    strat = {"cash": 10000.0, "positions": [], "closed": [], "cooldown": {},
             "last_eval_date": DAYS[4], "activity": []}
    adv.manage_longterm(strat, bars, DAYS[4], True)
    approx(strat["cash"], 0.0, tol=0.5)
    total = sum(p["shares"] * 100 for p in strat["positions"])
    approx(total, 10000.0 / 1.0005, tol=0.5)   # net of 5 bps entry slippage
    voo = next(p for p in strat["positions"] if p["symbol"] == "VOO")
    approx(voo["shares"] * 100, 4000.0 / 1.0005, tol=0.5)
    # drift: QQQ doubles → rebalance back to weights
    bars2 = {s: mk_hist([100, 100, 100, 100, 200 if s == "QQQ" else 100], name=s)
             for s in adv.LONGTERM_ALLOCATION}
    adv.manage_longterm(strat, bars2, DAYS[4], True)
    equity = strat["cash"] + sum(p["shares"] * (200 if p["symbol"] == "QQQ" else 100)
                                 for p in strat["positions"])
    qqq = next(p for p in strat["positions"] if p["symbol"] == "QQQ")
    approx(qqq["shares"] * 200 / equity, 0.20, tol=0.005)
    assert any("Rebalanced" in a["text"] for a in strat["activity"])
check("longterm inception buy + drift rebalance", t7)

# ---------- test 8: compute_stats ----------
def t8():
    closed = [{"pnl_pct": 10.0}, {"pnl_pct": -5.0}, {"pnl_pct": 20.0}, {"pnl_pct": -2.0}]
    series = [10000, 11000, 9900, 10500]
    s = adv.compute_stats(closed, series, 1.0)
    approx(s["total_return_pct"], 5.0)
    approx(s["vs_spy_pct"], 4.0)
    approx(s["win_rate_pct"], 50.0)
    approx(s["max_drawdown_pct"], -10.0)   # 11000 → 9900
    approx(s["avg_win_pct"], 15.0)
    approx(s["avg_loss_pct"], -3.5)
check("compute_stats math", t8)

print()
if fails:
    print(f"{fails} FAILURES")
    sys.exit(1)
print("ALL TESTS PASSED")

# ---------- test 9: retro-adjusted series across a split (gap replay) ----------
def t9():
    # Entered pre-split at $200 (stop 184). A 2:1 split hits mid-window; Yahoo
    # serves the WHOLE series in post-split units. No spurious stop may fire.
    strat = {"cash": 0.0, "positions": [{
        "symbol": "RSP", "name": "RSP", "shares": 10.0, "entry_price": 200.0,
        "entry_date": DAYS[0], "target_price": 240.0, "stop_price": 184.0,
        "thesis": "", "last_price": 200.0, "bars_held": 0,
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[0], "activity": []}
    bars = {"RSP": mk_hist([100, 101, 99, 100, 102], splits={DAYS[3]: 2.0})}
    marks = adv.replay_strategy("aggressive", strat, bars, DAYS, DAYS[4])
    assert len(strat["closed"]) == 0, f"spurious exit: {strat['closed']}"
    pos = strat["positions"][0]
    approx(pos["shares"], 20.0)
    approx(pos["stop_price"], 92.0)
    approx(marks[DAYS[1]], 2020.0)
    approx(marks[DAYS[4]], 2040.0)
    assert pos["splits_applied"] == [DAYS[3]]
    # replaying again must NOT re-apply the split
    strat["last_eval_date"] = DAYS[3]
    adv.replay_strategy("aggressive", strat, bars, DAYS, DAYS[4])
    approx(strat["positions"][0]["shares"], 20.0)
check("retro-adjusted split across gap replay, idempotent ledger", t9)

# ---------- test 10: halted symbol force-exits after >10 stale sessions ----------
def t10():
    cal = [f"2026-06-{d:02d}" for d in range(1, 21)]  # 20 sessions
    strat = {"cash": 0.0, "positions": [{
        "symbol": "HLT", "name": "HLT", "shares": 10.0, "entry_price": 50.0,
        "entry_date": cal[0], "target_price": 60.0, "stop_price": 46.0,
        "thesis": "", "last_price": 51.0, "bars_held": 1,
    }], "closed": [], "cooldown": {}, "last_eval_date": cal[1], "activity": []}
    # history exists but last bar is session 2 — halted for 18 sessions
    h = {"dates": cal[:2], "close": {cal[0]: 50.0, cal[1]: 51.0}, "volume": {},
         "name": "HLT", "divs": {}, "splits": {}}
    adv.replay_strategy("aggressive", strat, {"HLT": h}, cal, cal[-1])
    assert len(strat["closed"]) == 1, "halted position not force-exited"
    assert strat["closed"][0]["reason"] == "halted/delisted"
    approx(strat["cash"], 509.745)  # 10 sh × $51 net of 5 bps
check("halted symbol force-exit at last known price", t10)

# ---------- test 11: stale feed cannot roll the window backwards ----------
def t11():
    strat = {"cash": 100.0, "positions": [], "closed": [], "cooldown": {},
             "last_eval_date": DAYS[3], "activity": []}
    marks = adv.replay_strategy("aggressive", strat, {}, DAYS, DAYS[1])  # as_of older
    assert marks == {}, marks
    assert strat["last_eval_date"] == DAYS[3], strat["last_eval_date"]
check("stale as_of does not roll last_eval_date backwards", t11)

# ---------- test 12: fresh-bar filter blocks stale candidates ----------
def t12():
    up = list(range(100, 320))  # long rising series
    h_fresh = {"dates": [f"d{i:03d}" for i in range(len(up))],
               "close": {f"d{i:03d}": float(v) for i, v in enumerate(up)},
               "volume": {}, "name": "F", "divs": {}, "splits": {}}
    h_stale = {"dates": h_fresh["dates"][:-1], "close": dict(list(h_fresh["close"].items())[:-1]),
               "volume": {}, "name": "S", "divs": {}, "splits": {}}
    as_of = h_fresh["dates"][-1]
    ranked = adv.score_growth({"FRESH": h_fresh, "STALE": h_stale}, as_of)
    qual = [r["symbol"] for r in ranked if r["qualified"]]
    assert "STALE" not in qual, qual
    stale_row = next(r for r in ranked if r["symbol"] == "STALE")
    assert stale_row["reason"] == "no fresh price today", stale_row["reason"]
check("stale symbols excluded from new-pick ranking", t12)

print()
if fails:
    print(f"{fails} FAILURES (extended)")
    sys.exit(1)
print("ALL EXTENDED TESTS PASSED")

# ---------- test 13: ex-date morning run (no new session) still applies split ----------
def t13():
    # as_of == last_eval (pre-open run), but the payload already carries the
    # split dated AFTER as_of with the whole series retro-adjusted.
    strat = {"cash": 0.0, "positions": [{
        "symbol": "EXD", "name": "EXD", "shares": 10.0, "entry_price": 200.0,
        "entry_date": DAYS[0], "target_price": 240.0, "stop_price": 184.0,
        "thesis": "", "last_price": 200.0, "bars_held": 1,
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[2], "activity": []}
    bars = {"EXD": mk_hist([100, 101, 102], splits={DAYS[3]: 2.0})}  # split "tomorrow"
    marks = adv.replay_strategy("aggressive", strat, bars, DAYS[:3], DAYS[2])
    assert marks == {}, marks
    pos = strat["positions"][0]
    approx(pos["shares"], 20.0)          # adjusted despite no new sessions
    approx(pos["entry_price"], 100.0)
    val, px = adv.position_value(pos, bars, DAYS[2])
    approx(val, 2040.0)                   # 20 sh × $102 — no fake 50% drawdown
check("split dated after as_of applied when payload is retro-adjusted", t13)

# ---------- test 14: dividends missed on event-less data back-credit later ----------
def t14():
    strat = {"cash": 0.0, "positions": [{
        "symbol": "HEA", "name": "HEA", "shares": 100.0, "entry_price": 40.0,
        "entry_date": DAYS[0], "target_price": None, "stop_price": None,
        "thesis": "", "last_price": 40.0, "bars_held": 0,
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[0], "activity": []}
    # run 1: spark fallback (no events) across the ex-date — nothing credited
    spark = mk_hist([40, 40, 40], name="HEA"); spark["has_events"] = False
    adv.replay_strategy("longterm", strat, {"HEA": spark}, DAYS[:3], DAYS[2])
    approx(strat["cash"], 0.0)
    # run 2: chart data returns with the missed dividend — back-credited once
    full = mk_hist([40, 40, 40, 40, 40], divs={DAYS[1]: 0.75}); full["has_events"] = True
    adv.replay_strategy("longterm", strat, {"HEA": full}, DAYS, DAYS[4])
    approx(strat["cash"], 75.0)          # 100 sh × $0.75, exactly once
    # run 3: idempotent — never credited twice
    strat["last_eval_date"] = DAYS[3]
    adv.replay_strategy("longterm", strat, {"HEA": full}, DAYS, DAYS[4])
    approx(strat["cash"], 75.0)
check("dividend missed during outage back-credits exactly once", t14)

# ---------- test 15: mid-ratio split on event-less data defers exit ----------
def t15():
    strat = {"cash": 0.0, "positions": [{
        "symbol": "MRS", "name": "MRS", "shares": 30.0, "entry_price": 60.0,
        "entry_date": DAYS[0], "target_price": 72.0, "stop_price": 55.2,
        "thesis": "", "last_price": 60.0, "bars_held": 1,
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[1], "activity": []}
    # 3:2 split shows as ~0.67x day-over-day on event-less spark data
    spark = mk_hist([40, 40, 40, 40, 40], name="MRS"); spark["has_events"] = False
    adv.replay_strategy("aggressive", strat, {"MRS": spark}, DAYS, DAYS[4])
    assert len(strat["closed"]) == 0, f"false settlement on unreported split: {strat['closed']}"
check("split-sized gap on event-less data defers instead of settling", t15)

print()
if fails:
    print(f"{fails} FINAL FAILURES")
    sys.exit(1)
print("ALL FINAL TESTS PASSED")

# ---------- test 16: price store merge, overwrite, cap ----------
def t16():
    store = {"AAA": [["2026-01-01", 10.0], ["2026-01-02", 11.0]]}
    bars = {"AAA": mk_hist([11.5, 12.0]), "BBB": mk_hist([5.0])}
    # mk_hist uses DAYS dates (2026-07-*) — merge should append and keep order
    adv.merge_into_store(store, bars)
    assert store["AAA"][0][0] == "2026-01-01"
    assert store["AAA"][-1][1] == 12.0
    assert store["BBB"] == [[DAYS[0], 5.0]]
    # overwrite same date with corrected close
    adv.merge_into_store(store, {"BBB": mk_hist([5.5])})
    assert store["BBB"] == [[DAYS[0], 5.5]]
    # cap at STORE_MAX_SESSIONS
    long_rows = [[f"2025-{m:02d}-{d:02d}", 1.0] for m in range(1, 13) for d in range(1, 29)]
    store["CCC"] = long_rows
    adv.merge_into_store(store, {"CCC": mk_hist([2.0])})
    assert len(store["CCC"]) <= adv.STORE_MAX_SESSIONS
    h = adv.store_history(store, "AAA")
    assert h["dates"][-1] == DAYS[1] and h["has_events"] is False
check("price store merge/overwrite/cap", t16)

print()
if fails:
    print(f"{fails} STORE FAILURES")
    sys.exit(1)
print("ALL STORE TESTS PASSED")

# ---------- test 17: daytrade consolidation — safety flat + cash marks ----------
def t17():
    strat = {"cash": 4000.0, "positions": [{
        "symbol": "LEFT", "name": "LEFT", "shares": 100.0, "entry_price": 60.0,
        "entry_date": DAYS[1], "entry_time": "14:20",
        "target_price": 61.2, "stop_price": 59.4,
        "thesis": "", "last_price": 60.5, "bars_held": 0,
        "splits_applied": [], "divs_credited": [],
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[1], "activity": []}
    bars = {"LEFT": mk_hist([60, 60, 61, 62, 63])}
    marks = adv.consolidate_daytrade(strat, bars, DAYS, DAYS[3])
    assert strat["positions"] == [], "leftover day-trade position not flattened"
    assert strat["closed"][0]["reason"] == "overnight safety flat"
    approx(strat["cash"], 4000.0 + 100 * 62.0 * 0.999)   # as-of close net of 10 bps
    approx(marks[DAYS[2]], strat["cash"])
    approx(marks[DAYS[3]], strat["cash"])
    assert strat["last_eval_date"] == DAYS[3]
check("daytrade consolidation flattens leftovers, marks cash", t17)

# ---------- test 18: daytrade entry — gap band + confirmation ----------
def t18():
    m = adv.STRATEGY_META["daytrade"]
    # in band + rising vs prior check → enter
    assert adv.daytrade_should_enter(103.0, 100.0, 102.5, m) is True
    # first sighting (no prior mark) → never enter
    assert adv.daytrade_should_enter(103.0, 100.0, None, m) is False
    # fading vs prior check → never enter, even in band
    assert adv.daytrade_should_enter(103.0, 100.0, 103.5, m) is False
    # gap too small → no
    assert adv.daytrade_should_enter(101.5, 100.0, 101.0, m) is False
    # monster gap (>8%) fades — skip
    assert adv.daytrade_should_enter(109.0, 100.0, 108.0, m) is False
    # bad prev close → no
    assert adv.daytrade_should_enter(103.0, 0.0, 102.0, m) is False
    # sparse-cron fallback: no prior mark, but climbed since the open → enter
    assert adv.daytrade_should_enter(103.0, 100.0, None, m, open_px=102.0, allow_open_confirm=True) is True
    # no prior mark and fading off the open → no
    assert adv.daytrade_should_enter(103.0, 100.0, None, m, open_px=103.5, allow_open_confirm=True) is False
    # fallback disabled during opening-range noise → no
    assert adv.daytrade_should_enter(103.0, 100.0, None, m, open_px=102.0, allow_open_confirm=False) is False
    # prior mark always wins over the open fallback
    assert adv.daytrade_should_enter(103.0, 100.0, 103.5, m, open_px=100.5, allow_open_confirm=True) is False
check("daytrade entry: gap band + still-climbing confirmation", t18)

# ---------- test 18b: aggressive breakeven ratchet in replay ----------
def t18b():
    strat = {"cash": 0.0, "positions": [{
        "symbol": "RATCH", "name": "RATCH", "shares": 10.0, "entry_price": 100.0,
        "entry_date": DAYS[0], "target_price": 120.0, "stop_price": 92.0,
        "thesis": "", "last_price": 100.0, "bars_held": 0,
        "splits_applied": [], "divs_credited": [],
    }], "closed": [], "cooldown": {}, "last_eval_date": DAYS[0], "activity": []}
    # +11% on day 2 arms the ratchet; drop back to entry on day 4 exits at breakeven
    bars = {"RATCH": mk_hist([100, 111, 108, 99, 98])}
    adv.replay_strategy("aggressive", strat, bars, DAYS, DAYS[4])
    assert len(strat["closed"]) == 1, strat["positions"]
    t = strat["closed"][0]
    assert t["reason"] == "breakeven stop", t["reason"]
    approx(t["exit_price"], 99.0 * 0.9995) # out near entry (net), NOT at the old −8% stop
    approx(t["pnl_pct"], -1.05, tol=0.02)
    assert any("breakeven" in a["text"] for a in strat["activity"])
check("aggressive: winner ratchets stop to breakeven, never a full loss", t18b)

# ---------- test 18c: quality-momentum ranking rejects spikes ----------
def t18c():
    import math as _m
    def series(daily):
        out = [100.0]
        for i in range(250):
            out.append(out[-1] * (1 + daily(i)))
        return out
    def hist_from(closes, sym):
        n = len(closes)
        dates = [f"2026-{(i//28)+1:02d}-{(i%28)+1:02d}" for i in range(n)]
        return {"dates": dates, "close": {d: c for d, c in zip(dates, closes)},
                "volume": {}, "name": sym, "divs": {}, "splits": {}}
    # steady riser with normal wiggle (a monotone line would read RSI 100)
    smooth = series(lambda i: 0.006 if i % 3 else -0.004)
    spike  = series(lambda i: 0.09 if i > 246 else (0.002 if i % 3 else -0.002))  # flat, then vertical
    bars = {"SMOOTH": hist_from(smooth, "SMOOTH"), "SPIKE": hist_from(spike, "SPIKE")}
    as_of = bars["SMOOTH"]["dates"][-1]
    ranked = adv.score_aggressive(bars, as_of)
    qual = [r["symbol"] for r in ranked if r["qualified"]]
    assert "SPIKE" not in qual, f"vertical spike should be filtered: {qual}"
    assert "SMOOTH" in qual, f"steady riser should qualify: {qual}"
    spike_row = next(r for r in ranked if r["symbol"] == "SPIKE")
    assert spike_row["reason"], "rejected symbol must carry a verdict reason"
check("quality momentum: steady riser in, vertical spike out", t18c)

# ---------- test 19: ensure_strategies migrates old state ----------
def t19():
    state = adv.bootstrap_state(DAYS[0])
    del state["strategies"]["daytrade"]
    adv.ensure_strategies(state, DAYS[2])
    dt = state["strategies"]["daytrade"]
    approx(dt["cash"], adv.STARTING_CAPITAL)
    assert dt["positions"] == [] and dt["last_eval_date"] == DAYS[2]
check("ensure_strategies migrates in the daytrade sleeve", t19)

print()
if fails:
    print(f"{fails} DAYTRADE FAILURES")
    sys.exit(1)
print("ALL DAYTRADE TESTS PASSED")

# ---------- test 20: institutional metrics math ----------
def t20():
    # strategy grows 1%/day steadily; benchmark 0.5%/day → beta ≈ 2 vs bench... 
    # use mixed series with known relationship: s = 2*b day by day
    dates = [f"2026-03-{d:02d}" for d in range(1, 22)]
    hist = {}
    s, b = 10000.0, 10000.0
    moves = [0.01, -0.005, 0.008, -0.002, 0.006, 0.004, -0.006, 0.01, -0.004, 0.002,
             0.007, -0.003, 0.005, -0.001, 0.009, 0.003, -0.007, 0.006, -0.002, 0.004]
    hist[dates[0]] = {"aggressive": s, "benchmark": b}
    for i, m in enumerate(moves):
        b *= (1 + m)
        s *= (1 + 2 * m)          # exactly 2x the benchmark's daily move
        hist[dates[i + 1]] = {"aggressive": s, "benchmark": b}
    closed = [{"pnl_pct": 10.0}, {"pnl_pct": 6.0}, {"pnl_pct": -4.0},
              {"pnl_pct": 8.0}, {"pnl_pct": -2.0}]
    m = adv.pro_metrics(dates, hist, "aggressive", closed)
    assert m["sample_days"] == 20, m["sample_days"]
    approx(m["beta"], 2.0, tol=0.05)
    assert m["ann_vol_pct"] and m["ann_vol_pct"] > 0
    assert m["sharpe"] is not None
    approx(m["profit_factor"], 24.0 / 6.0, tol=0.01)     # 24 gross win / 6 gross loss
    assert m["ann_return_pct"] is not None               # n>=20 unlocks annualization
    # a flat (all-cash) curve must yield nulls, not zeros pretending to be data
    flat_hist = {d: {"daytrade": 10000.0, "benchmark": hist[d]["benchmark"]} for d in dates}
    f = adv.pro_metrics(dates, flat_hist, "daytrade", [])
    assert f["sharpe"] is None and f["profit_factor"] is None
    approx(f["ann_vol_pct"], 0.0)
check("pro metrics: beta recovery, profit factor, null-safety", t20)

print()
if fails:
    print(f"{fails} METRICS FAILURES")
    sys.exit(1)
print("ALL METRICS TESTS PASSED")

# ---------- test 21: self-tuning ladder + blacklist + gates ----------
def t21():
    meta = dict(adv.STRATEGY_META["daytrade"])
    # gate: under 25 trades → no change
    few = [{"pnl_pct": -2.0, "reason": "stop −2%", "symbol": "A"}] * 10
    ch, why = adv.daytrade_ladder_decision(few, meta)
    assert ch is None and "need 25" in why
    # 30 trades, 30% win rate, losses mostly stop-outs → widen one notch
    trades = ([{"pnl_pct": 3.0, "reason": "target +3%", "symbol": "W"}] * 9
              + [{"pnl_pct": -2.0, "reason": "stop −2%", "symbol": "L"}] * 15
              + [{"pnl_pct": -0.5, "reason": "end of day — flat by the close", "symbol": "L"}] * 6)
    ch, why = adv.daytrade_ladder_decision(trades, meta)
    assert ch and ch["stop_pct"] == 0.025 and ch["min_gap"] == 0.025, ch
    # already at ladder top → no further widening
    top = dict(meta, stop_pct=0.03, min_gap=0.03)
    ch2, _ = adv.daytrade_ladder_decision(trades, top)
    assert ch2 is None or ("stop_pct" not in ch2 and "min_gap" not in ch2)
    # healthy stats → no change
    good = ([{"pnl_pct": 1.0, "reason": "target +3%", "symbol": "W"}] * 13
            + [{"pnl_pct": -1.0, "reason": "stop −2%", "symbol": "L"}] * 12)
    ch3, why3 = adv.daytrade_ladder_decision(good, meta)
    assert ch3 is None and "no change" in why3
    # blacklist: 3 trades totalling −6% benches the symbol; expiry honored
    closed = [{"symbol": "BAD", "pnl_pct": -2.0}] * 3 + [{"symbol": "OK", "pnl_pct": 1.0}] * 3
    bl = adv.daytrade_blacklist_update(closed, "2026-08-01", {})
    assert "BAD" in bl and "OK" not in bl
    assert bl["BAD"] == "2026-09-30"
    bl2 = adv.daytrade_blacklist_update([], "2026-10-01", bl)
    assert "BAD" not in bl2, "expired bench should clear"
check("self-tuning: ladder gates, notch caps, blacklist lifecycle", t21)

# ---------- test 22: walk-forward tuner gates on store depth ----------
def t22():
    store = {"SPY": [[f"2026-01-{d:02d}", 100.0] for d in range(1, 20)]}
    ch, why = adv.tune_daily_strategy("aggressive", store, {})
    assert ch is None and "arms itself" in why, why
    # param overrides fold into STRATEGY_META (and never touch the Nest Egg)
    import copy
    saved = copy.deepcopy(adv.STRATEGY_META)
    try:
        adv.apply_param_overrides({"overrides": {"daytrade": {"stop_pct": 0.025},
                                                 "longterm": {"slots": 99}}})
        assert adv.STRATEGY_META["daytrade"]["stop_pct"] == 0.025
        assert adv.STRATEGY_META["longterm"]["slots"] != 99, "Nest Egg must never be tuned"
    finally:
        adv.STRATEGY_META.clear(); adv.STRATEGY_META.update(saved)
check("walk-forward gate + override safety (Nest Egg untouchable)", t22)

print()
if fails:
    print(f"{fails} TUNING FAILURES")
    sys.exit(1)
print("ALL TUNING TESTS PASSED")

# ---------- test 23: board scorers give every symbol a verdict ----------
def t23():
    metrics = {
        "GOOD": {"r5": 2.0, "r13w": 20.0, "r26w": 35.0, "r52w": 60.0, "vol": 30.0,
                 "hi52": 120.0, "lo52": 60.0, "advol": 5.0},
        "SPIKE": {"r5": 22.0, "r13w": 25.0, "r26w": 30.0, "r52w": 40.0, "vol": 50.0,
                  "hi52": 100.0, "lo52": 40.0, "advol": 5.0},
        "DOWN": {"r5": -1.0, "r13w": -8.0, "r26w": -12.0, "r52w": -20.0, "vol": 30.0,
                 "hi52": 100.0, "lo52": 50.0, "advol": 5.0},
    }
    quotes = {"GOOD": 110.0, "SPIKE": 95.0, "DOWN": 55.0}
    board = adv.score_aggressive_metrics(metrics, quotes)
    assert len(board) == 3, "every scanned symbol must appear on the board"
    by = {r["symbol"]: r for r in board}
    assert by["GOOD"]["qualified"] and by["GOOD"]["score"] is not None
    assert not by["SPIKE"]["qualified"] and "spike" in by["SPIKE"]["reason"]
    assert not by["DOWN"]["qualified"] and "not rising" in by["DOWN"]["reason"]
    assert by["SPIKE"]["needs"] and "cool" in by["SPIKE"]["needs"], by["SPIKE"]["needs"]
    assert by["DOWN"]["needs"] and "positive" in by["DOWN"]["needs"], by["DOWN"]["needs"]
    assert board[0]["symbol"] == "GOOD", "qualified rows sort to the top"
    g = adv.score_growth_metrics(metrics, quotes)
    gby = {r["symbol"]: r for r in g}
    assert gby["GOOD"]["qualified"]
    assert not gby["DOWN"]["qualified"]
check("board scorers: verdict + reason for every symbol, buys on top", t23)

# ---------- test 24: board stays full when every slot is taken ----------
def t24():
    # native scorers must return a verdict row for EVERY symbol (the empty-board
    # bug: fully-invested strategies published boards with zero rows)
    up = list(range(100, 320))
    def hist(name, dates_cut=0):
        ds = [f"d{i:03d}" for i in range(len(up) - dates_cut)]
        return {"dates": ds, "close": {d: float(up[i]) for i, d in enumerate(ds)},
                "volume": {}, "name": name, "divs": {}, "splits": {}}
    bars = {"AAA": hist("AAA"), "BBB": hist("BBB"), "OLD": hist("OLD", 1)}
    as_of = bars["AAA"]["dates"][-1]
    board = adv.score_growth(bars, as_of)
    assert len(board) == 3, "every scanned symbol must get a verdict row"
    # a fully-invested strategy still shows the whole board, holdings flagged
    state = adv.bootstrap_state("2026-01-02")
    gro = state["strategies"]["growth"]
    gro["positions"] = [{"symbol": "AAA", "shares": 1.0, "entry_price": 100.0}] * 5
    gro["positions"] = [dict(p, symbol=s) for p, s in zip(gro["positions"], ["AAA", "BBB", "CCC", "DDD", "EEE"])]
    gro["positions"][0]["entry_date"] = "2026-01-02"  # AAA bought at this very close
    gro["cooldown"] = {"COOL": "2026-01-01"}
    bars["COOL"] = hist("COOL")
    bars["FRES"] = hist("FRES")
    board = adv.score_growth(bars, as_of)
    boards = adv.build_boards(state, [], board, "price-history", "", True, "2026-01-02", bars)
    rows = boards["growth"]["rows"]
    assert len(rows) == 5, "board publishes every verdict"
    byfres = next(r for r in rows if r["symbol"] == "FRES")
    assert byfres["buy"], "slots are never full — a qualifying name is always suggested AND bought"
    byrow = {r["symbol"]: r for r in rows}
    assert byrow["AAA"]["held"] and byrow["BBB"]["held"]
    assert not byrow["AAA"]["buy"], "held names are never buy-highlighted"
    assert byrow["AAA"]["fresh"] and not byrow["BBB"]["fresh"], "same-close buys are flagged fresh"
    assert "every" in boards["growth"]["note"].lower() and "bought" in boards["growth"]["note"], \
        "board states the every-suggestion-is-a-purchase rule"
    old = byrow["OLD"]
    assert old["needs"], "every rejected symbol says what it needs"
    assert byrow["COOL"]["qualified"] and not byrow["COOL"]["buy"]
    assert "cool-down" in byrow["COOL"]["needs"], "qualified-but-blocked names say what's in the way"
    # the Nest Egg board lists all five permanent holdings with their roles
    lt_rows = boards["longterm"]["rows"]
    assert len(lt_rows) == len(adv.LONGTERM_ALLOCATION), "longterm board shows the whole allocation"
    assert all(r["reason"] for r in lt_rows), "every fund explains its role"
    assert "target" in lt_rows[0]["reason"] or "buys at" in lt_rows[0]["reason"]
check("board publishes every verdict even when fully invested", t24)

# ---------- test 25: news check — bad headlines pull a name off the buy list ----------
def t25():
    assert adv._headline_sentiment("Shares surge after earnings beat") == 1
    assert adv._headline_sentiment("SEC probe widens; shares plunge") == -1
    assert adv._headline_sentiment("Company announces annual meeting date") == 0
    board = [
        {"symbol": "CLEAN", "qualified": True, "reason": "meets every test", "needs": "", "score": 1.2},
        {"symbol": "DIRTY", "qualified": True, "reason": "meets every test", "needs": "", "score": 1.1},
        {"symbol": "JUNK", "qualified": False, "reason": "not rising", "needs": "needs climbs", "score": None},
    ]
    news = {
        "CLEAN": {"n": 3, "pos": 2, "neg": 0, "verdict": "positive", "top": []},
        "DIRTY": {"n": 5, "pos": 0, "neg": 3, "verdict": "negative", "top": []},
    }
    adv.apply_news_to_board(board, news)
    by = {r["symbol"]: r for r in board}
    assert by["CLEAN"]["qualified"], "positive news never disqualifies"
    assert not by["DIRTY"]["qualified"], "negative news pulls the name off the buy list"
    assert "news" in by["DIRTY"]["reason"] and by["DIRTY"]["needs"]
    assert board[0]["symbol"] == "CLEAN" and board[1]["symbol"] == "DIRTY", \
        "flagged name drops below qualified but stays above ordinary rejects"
    sig = adv.summarize_news([
        {"h": "a", "s": "x", "d": "2026-08-01", "sent": -1},
        {"h": "b", "s": "x", "d": "2026-08-02", "sent": -1},
        {"h": "c", "s": "x", "d": "2026-08-03", "sent": 1},
    ])
    assert sig["verdict"] == "negative" and sig["neg"] == 2 and sig["pos"] == 1
    assert adv.summarize_news([])["verdict"] == "quiet"
check("news check: sentiment words, buy-list veto, verdict math", t25)

# ---------- test 26: intraday news veto reads the published board verdicts ----------
def t26():
    site = {"strategies": {"daytrade": {"board": {"rows": [
        {"symbol": "TSLA", "news": {"verdict": "negative"}},
        {"symbol": "NVDA", "news": {"verdict": "positive"}},
        {"symbol": "AMD", "news": None},
        {"symbol": "GME"}]}}}}
    assert adv.daytrade_news_veto(site) == {"TSLA"}
    assert adv.daytrade_news_veto({}) == set()
    assert adv.daytrade_news_veto(None) == set()
    assert adv.daytrade_news_veto({"strategies": {"daytrade": {"board": None}}}) == set()
check("intraday news veto reads published verdicts", t26)

print()
if fails:
    print(f"{fails} BOARD FAILURES")
    sys.exit(1)
print("ALL BOARD TESTS PASSED")
