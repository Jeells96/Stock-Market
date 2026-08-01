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
    approx(t["exit_price"], 90.0)
    approx(strat["cash"], 9000.0)          # 100 sh × $90
    approx(t["pnl_pct"], -10.0)
    approx(marks[DAYS[1]], 9500.0)         # marked at day-2 close pre-exit
    approx(marks[DAYS[2]], 9000.0)
    approx(marks[DAYS[4]], 9000.0)         # all cash after exit
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

# ---------- test 6: fill_slots budget + cooldown ----------
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
    opened = adv.fill_slots("aggressive", strat, ranked, DAYS[4], True)
    syms = [p["symbol"] for p in opened]
    assert "HOT" not in syms, f"cooldown ignored: {syms}"
    assert len(opened) == 5, len(opened)
    approx(strat["cash"], 0.0, tol=0.5)
    total = sum(p["shares"] * p["entry_price"] for p in opened)
    approx(total, 10000.0, tol=0.5)
check("fill_slots: 5 slots, equal budget, cooldown respected", t6)

# ---------- test 7: longterm inception + rebalance ----------
def t7():
    bars = {s: mk_hist([100, 100, 100, 100, 100], name=s) for s in adv.LONGTERM_ALLOCATION}
    strat = {"cash": 10000.0, "positions": [], "closed": [], "cooldown": {},
             "last_eval_date": DAYS[4], "activity": []}
    adv.manage_longterm(strat, bars, DAYS[4], True)
    approx(strat["cash"], 0.0, tol=0.5)
    total = sum(p["shares"] * 100 for p in strat["positions"])
    approx(total, 10000.0, tol=0.5)
    voo = next(p for p in strat["positions"] if p["symbol"] == "VOO")
    approx(voo["shares"] * 100, 4000.0, tol=0.5)
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
    approx(strat["cash"], 510.0)  # 10 sh × last known $51
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
    syms = [r["symbol"] for r in ranked]
    assert "STALE" not in syms, syms
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
