#!/usr/bin/env python3
"""Conservative execution sanity check: enter at next day's open."""

import importlib.util
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).parent

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


prepare = load_module("prepare", ROOT / "prepare.py")
strategy = load_module("strategy", ROOT / "strategy.py")


def adjust_signals(signals, data):
    """Shift entries to next day's open and recompute position size."""
    rows = []
    for _, sig in signals.iterrows():
        ticker = sig["ticker"]
        df = data.get(ticker)
        if df is None:
            continue
        future = df.loc[df.index > sig["date"]]
        if len(future) == 0:
            continue
        next_day = future.index[0]
        next_open = float(future.iloc[0]["Open"])
        next_high = float(future.iloc[0]["High"])
        next_low = float(future.iloc[0]["Low"])

        # Limit-order fill at the strategy's intended entry price, next day
        limit = sig["price"]
        if not (next_low <= limit <= next_high):
            continue
        fill_price = min(limit, next_open)

        risk_per_share = fill_price - sig["stop_loss"]
        if not np.isfinite(risk_per_share) or risk_per_share <= 0:
            continue

        risk_amount = strategy.INITIAL_CAPITAL * strategy.RISK_PCT
        shares = int(risk_amount / risk_per_share)
        if shares <= 0:
            continue

        rows.append({
            "date": next_day,
            "ticker": ticker,
            "action": "BUY",
            "price": fill_price,
            "stop_loss": sig["stop_loss"],
            "take_profit": sig["take_profit"],
            "position_size": shares,
        })
    return pd.DataFrame(rows)


def run(label, start, end):
    print(f"\n{'='*60}")
    print(f"REALISTIC BACKTEST: {label}")
    print(f"{'='*60}")
    data = prepare.load_all_data()
    signals = strategy.generate_signals(data, start, end)
    adj = adjust_signals(signals, data)
    results = prepare.run_backtest(adj, data, start, end)
    for k, v in results.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    run("TEST 2024-07 to 2025-12", "2024-07-01", "2025-12-31")
    run("FORWARD 2026-01 to 2026-08", "2026-01-01", "2026-08-13")
