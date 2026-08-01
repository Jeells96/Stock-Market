"""Re-runnable price-store seeder.

Pulls ~1 year of daily closes for the full advisor universe from Yahoo's
batched spark endpoint and folds them into data/prices.json. Run it from any
network Yahoo tolerates (a home connection works fine); after seeding, the
scheduled engine keeps the store current forever using Finnhub end-of-day
quotes, with no further Yahoo dependency.

Usage: python3 scripts/seed_prices.py
"""
import importlib.util
import os
import sys

spec = importlib.util.spec_from_file_location(
    "advisor", os.path.join(os.path.dirname(os.path.abspath(__file__)), "advisor.py"))
adv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adv)

symbols = list(dict.fromkeys(
    [adv.BENCHMARK_SYMBOL] + list(adv.LONGTERM_ALLOCATION) + adv.MOMENTUM + adv.SP_CORE))
bars = adv.fetch_universe(symbols)
if len(bars) < len(symbols) * 0.8:
    print(f"Seed failed: only {len(bars)}/{len(symbols)} symbols fetched — try again later.")
    sys.exit(1)
adv.trim_partial_session(bars)
store = adv.load_price_store()
adv.merge_into_store(store, bars)
adv.save_json(adv.PRICES_PATH, store, compact=True)
depths = sorted(len(v) for v in store.values())
print(f"Seeded {len(store)} symbols · sessions min/median/max = "
      f"{depths[0]}/{depths[len(depths) // 2]}/{depths[-1]}")
