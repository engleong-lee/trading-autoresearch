#!/usr/bin/env python3
"""Next-day limit-order execution sanity check for strategy signals."""

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


def load_data():
    return prepare.load_all_data()


def load_strategy():
    return load_module("strategy", ROOT / "strategy.py")


def adjust_signals(signals, data, strategy_mod):
    """Shift entries to next-day limit fills and recompute position size."""
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

        risk_amount = strategy_mod.INITIAL_CAPITAL * strategy_mod.RISK_PCT
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


def generate_realistic_signals(data, start, end):
    """Generate signals using a fresh strategy module, then adjust to next-day fills."""
    strategy_mod = load_strategy()
    signals = strategy_mod.generate_signals(data, start, end)
    adj = adjust_signals(signals, data, strategy_mod)
    return adj


def run_with_signals(adj, start, end, data):
    """Run prepare.run_backtest on signals filtered to the requested period."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    mask = (adj["date"] >= start_ts) & (adj["date"] <= end_ts)
    sub = adj.loc[mask].copy()
    results = prepare.run_backtest(sub, data, start, end)
    return results


def run_realistic(start, end, data=None):
    if data is None:
        data = load_data()
    adj = generate_realistic_signals(data, start, end)
    return run_with_signals(adj, start, end, data)


def print_results(label, results):
    print(f"\n{'='*60}")
    print(f"REALISTIC BACKTEST: {label}")
    print(f"{'='*60}")
    for k, v in results.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    data = load_data()
    adj = generate_realistic_signals(data, "2022-11-01", "2026-08-13")
    print_results("TEST 2024-07 to 2025-12", run_with_signals(adj, "2024-07-01", "2025-12-31", data))
    print_results("FORWARD 2026-01 to 2026-08", run_with_signals(adj, "2026-01-01", "2026-08-13", data))
