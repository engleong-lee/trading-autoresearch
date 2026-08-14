#!/usr/bin/env python3
"""Forward test on the extra 2026 OOS window."""

import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


prepare = load_module("prepare", ROOT / "prepare.py")
strategy = load_module("strategy", ROOT / "strategy.py")

START = "2026-01-01"
END = "2026-08-13"

data = prepare.load_all_data()
print(f"Loaded data, testing {START} to {END}")

signals = strategy.generate_signals(data, START, END)
results = prepare.run_backtest(signals, data, START, END)

print("\nFORWARD TEST (2026)")
for k, v in results.items():
    print(f"  {k}: {v}")
