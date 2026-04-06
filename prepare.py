"""
prepare.py — IMMUTABLE evaluation harness for autoresearch trading loop.

DO NOT MODIFY THIS FILE. The agent must never touch this.
This file:
  1. Downloads & caches historical daily OHLCV data (yfinance)
  2. Defines train/validation/test time splits
  3. Runs the backtest engine against strategy.py signals
  4. Computes the scalar metric: Sharpe Ratio (annualized, on VALIDATION set)
  5. Reports secondary metrics: total return, max drawdown, win rate, trade count

Usage:
  python prepare.py              # Run full eval (download if needed + backtest)
  python prepare.py --download   # Force re-download data
  python prepare.py --test       # Run on TEST set (final out-of-sample, use sparingly)
"""

import os
import sys
import json
import hashlib
import argparse
import importlib
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────
# CONSTANTS — the agent cannot change these
# ─────────────────────────────────────────────────────────────

WATCHLIST = [
    "NVDA", "AAPL", "AMZN", "V", "MA",
    "AMD", "AVGO", "CRWD", "VGK", "INTU",
]

# Time splits (inclusive)
TRAIN_START = "2018-01-01"
TRAIN_END = "2022-12-31"       # 5 years for strategy development

VAL_START = "2023-01-01"
VAL_END = "2024-06-30"         # 18 months validation (agent scored on this)

TEST_START = "2024-07-01"
TEST_END = "2025-12-31"        # 18 months held-out test (used sparingly)

# Walk-forward out-of-sample windows within validation
# The agent's Sharpe is the AVERAGE across these windows
WALK_FORWARD_WINDOWS = [
    ("2023-01-01", "2023-06-30"),
    ("2023-04-01", "2023-09-30"),
    ("2023-07-01", "2023-12-31"),
    ("2023-10-01", "2024-03-31"),
    ("2024-01-01", "2024-06-30"),
]

# Backtest assumptions
INITIAL_CAPITAL = 100_000.0
COMMISSION_PER_TRADE = 1.0      # $1 per trade (modern broker)
SLIPPAGE_PCT = 0.05             # 0.05% slippage per trade
MAX_CONCURRENT_POSITIONS = 10
RISK_PER_TRADE_PCT = 1.0        # 1% risk per trade
RISK_FREE_RATE = 0.04           # ~4% for Sharpe calculation (T-bill rate)

DATA_DIR = Path.home() / ".cache" / "autoresearch-trading"

# ─────────────────────────────────────────────────────────────
# DATA DOWNLOAD
# ─────────────────────────────────────────────────────────────

def download_data(force=False):
    """Download daily OHLCV data for all watchlist tickers via yfinance."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = DATA_DIR / "manifest.json"

    if not force and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if set(manifest.get("tickers", [])) == set(WATCHLIST):
            print(f"Data already cached at {DATA_DIR}")
            return

    try:
        import yfinance as yf
    except ImportError:
        print("Installing yfinance...")
        os.system(f"{sys.executable} -m pip install yfinance --quiet")
        import yfinance as yf

    print(f"Downloading data for {len(WATCHLIST)} tickers...")
    # Download with buffer before train start for indicator warmup
    warmup_start = (pd.Timestamp(TRAIN_START) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    download_end = (pd.Timestamp.now() + pd.DateOffset(days=1)).strftime("%Y-%m-%d")

    for ticker in WATCHLIST:
        print(f"  {ticker}...", end=" ", flush=True)
        try:
            df = yf.download(ticker, start=warmup_start, end=download_end,
                             progress=False, auto_adjust=True)
            if df.empty:
                print("EMPTY — skipping")
                continue
            # Flatten multi-level columns if present
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.to_parquet(DATA_DIR / f"{ticker}.parquet")
            print(f"OK ({len(df)} rows)")
        except Exception as e:
            print(f"ERROR: {e}")

    # Also download SPY and QQQ for market regime detection
    for bench in ["SPY", "QQQ"]:
        if bench not in WATCHLIST:
            print(f"  {bench} (benchmark)...", end=" ", flush=True)
            try:
                df = yf.download(bench, start=warmup_start, end=download_end,
                                 progress=False, auto_adjust=True)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df.to_parquet(DATA_DIR / f"{bench}.parquet")
                print(f"OK ({len(df)} rows)")
            except Exception as e:
                print(f"ERROR: {e}")

    # Download VIX for volatility regime
    print(f"  ^VIX (volatility)...", end=" ", flush=True)
    try:
        vix = yf.download("^VIX", start=warmup_start, end=download_end,
                           progress=False, auto_adjust=True)
        if isinstance(vix.columns, pd.MultiIndex):
            vix.columns = vix.columns.get_level_values(0)
        vix.to_parquet(DATA_DIR / "VIX.parquet")
        print(f"OK ({len(vix)} rows)")
    except Exception as e:
        print(f"ERROR: {e}")

    manifest = {
        "tickers": WATCHLIST,
        "downloaded_at": datetime.now().isoformat(),
        "warmup_start": warmup_start,
        "download_end": download_end,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nData cached at {DATA_DIR}")


def load_data(ticker):
    """Load cached parquet data for a ticker. Returns DataFrame with OHLCV."""
    path = DATA_DIR / f"{ticker}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No data for {ticker}. Run: python prepare.py --download")
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # Ensure standard column names
    df.columns = [c.strip().title() for c in df.columns]
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in df.columns:
            raise ValueError(f"Missing column {col} in {ticker} data")
    return df


def load_all_data():
    """Load data for all watchlist tickers. Returns dict of {ticker: DataFrame}."""
    data = {}
    for ticker in WATCHLIST:
        try:
            data[ticker] = load_data(ticker)
        except FileNotFoundError:
            print(f"Warning: No data for {ticker}, skipping")
    # Also load benchmarks
    for extra in ["SPY", "QQQ", "VIX"]:
        try:
            data[extra] = load_data(extra)
        except FileNotFoundError:
            pass
    return data


# ─────────────────────────────────────────────────────────────
# BACKTEST ENGINE
# ─────────────────────────────────────────────────────────────

def run_backtest(signals_df, price_data, start_date, end_date):
    """
    Run a realistic backtest given strategy signals.

    Args:
        signals_df: DataFrame with columns:
            - date (datetime): signal date
            - ticker (str): stock ticker
            - action (str): 'BUY' or 'SELL'
            - price (float): intended entry/exit price
            - stop_loss (float): stop loss price (for BUY signals)
            - take_profit (float): optional profit target (for BUY signals)
            - position_size (int): number of shares (for BUY signals)
        price_data: dict of {ticker: DataFrame} with OHLCV
        start_date: backtest start (str or Timestamp)
        end_date: backtest end (str or Timestamp)

    Returns:
        dict with backtest results
    """
    start_date = pd.Timestamp(start_date)
    end_date = pd.Timestamp(end_date)

    capital = INITIAL_CAPITAL
    positions = {}  # ticker -> {shares, entry_price, entry_date, stop_loss, take_profit}
    trades = []     # completed trades
    equity_curve = []

    # Get all trading days in range
    all_dates = set()
    for ticker, df in price_data.items():
        if ticker in ["VIX"]:
            continue
        mask = (df.index >= start_date) & (df.index <= end_date)
        all_dates.update(df.index[mask].tolist())
    trading_days = sorted(all_dates)

    if not trading_days:
        return _empty_results()

    # Index signals by date for fast lookup
    if signals_df is not None and len(signals_df) > 0:
        signals_df = signals_df.copy()
        signals_df["date"] = pd.to_datetime(signals_df["date"])
        signals_by_date = signals_df.groupby("date")
    else:
        signals_by_date = None

    for day in trading_days:
        day_ts = pd.Timestamp(day)

        # 1. Check stops and take-profits on open
        tickers_to_close = []
        for ticker, pos in list(positions.items()):
            if ticker not in price_data:
                continue
            df = price_data[ticker]
            if day_ts not in df.index:
                continue
            row = df.loc[day_ts]
            low = row["Low"]
            high = row["High"]
            open_price = row["Open"]

            # Stop loss hit (check low of day)
            if pos["stop_loss"] is not None and low <= pos["stop_loss"]:
                exit_price = min(open_price, pos["stop_loss"])  # gap down scenario
                exit_price *= (1 - SLIPPAGE_PCT / 100)
                pnl = (exit_price - pos["entry_price"]) * pos["shares"] - 2 * COMMISSION_PER_TRADE
                capital += pos["entry_price"] * pos["shares"] + pnl
                trades.append({
                    "ticker": ticker,
                    "entry_date": pos["entry_date"],
                    "exit_date": day_ts,
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "shares": pos["shares"],
                    "pnl": pnl,
                    "exit_reason": "stop_loss",
                    "hold_days": (day_ts - pos["entry_date"]).days,
                })
                tickers_to_close.append(ticker)
                continue

            # Take profit hit (check high of day)
            if pos.get("take_profit") is not None and high >= pos["take_profit"]:
                exit_price = max(open_price, pos["take_profit"])
                exit_price *= (1 - SLIPPAGE_PCT / 100)
                pnl = (exit_price - pos["entry_price"]) * pos["shares"] - 2 * COMMISSION_PER_TRADE
                capital += pos["entry_price"] * pos["shares"] + pnl
                trades.append({
                    "ticker": ticker,
                    "entry_date": pos["entry_date"],
                    "exit_date": day_ts,
                    "entry_price": pos["entry_price"],
                    "exit_price": exit_price,
                    "shares": pos["shares"],
                    "pnl": pnl,
                    "exit_reason": "take_profit",
                    "hold_days": (day_ts - pos["entry_date"]).days,
                })
                tickers_to_close.append(ticker)
                continue

        for t in tickers_to_close:
            del positions[t]

        # 2. Process new signals for this day
        if signals_by_date is not None:
            try:
                day_signals = signals_by_date.get_group(day_ts)
            except KeyError:
                day_signals = pd.DataFrame()

            for _, sig in day_signals.iterrows():
                ticker = sig["ticker"]
                action = sig["action"].upper()

                if action == "BUY" and ticker not in positions:
                    if len(positions) >= MAX_CONCURRENT_POSITIONS:
                        continue
                    if ticker not in price_data or day_ts not in price_data[ticker].index:
                        continue

                    entry_price = sig["price"] * (1 + SLIPPAGE_PCT / 100)
                    shares = int(sig.get("position_size", 0))
                    if shares <= 0:
                        # Calculate from risk
                        stop = sig.get("stop_loss", entry_price * 0.95)
                        risk_per_share = max(entry_price - stop, 0.01)
                        risk_amount = capital * (RISK_PER_TRADE_PCT / 100)
                        shares = int(risk_amount / risk_per_share)

                    cost = entry_price * shares + COMMISSION_PER_TRADE
                    if cost > capital:
                        shares = int((capital - COMMISSION_PER_TRADE) / entry_price)
                    if shares <= 0:
                        continue

                    capital -= entry_price * shares + COMMISSION_PER_TRADE
                    positions[ticker] = {
                        "shares": shares,
                        "entry_price": entry_price,
                        "entry_date": day_ts,
                        "stop_loss": sig.get("stop_loss"),
                        "take_profit": sig.get("take_profit"),
                    }

                elif action == "SELL" and ticker in positions:
                    pos = positions[ticker]
                    if ticker not in price_data or day_ts not in price_data[ticker].index:
                        continue
                    exit_price = sig["price"] * (1 - SLIPPAGE_PCT / 100)
                    pnl = (exit_price - pos["entry_price"]) * pos["shares"] - 2 * COMMISSION_PER_TRADE
                    capital += pos["entry_price"] * pos["shares"] + pnl
                    trades.append({
                        "ticker": ticker,
                        "entry_date": pos["entry_date"],
                        "exit_date": day_ts,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "shares": pos["shares"],
                        "pnl": pnl,
                        "exit_reason": "signal",
                        "hold_days": (day_ts - pos["entry_date"]).days,
                    })
                    del positions[ticker]

                elif action == "UPDATE_STOP" and ticker in positions:
                    new_stop = sig.get("stop_loss")
                    if new_stop is not None and new_stop > positions[ticker]["stop_loss"]:
                        positions[ticker]["stop_loss"] = new_stop

        # 3. Calculate equity
        equity = capital
        for ticker, pos in positions.items():
            if ticker in price_data and day_ts in price_data[ticker].index:
                equity += price_data[ticker].loc[day_ts, "Close"] * pos["shares"]
            else:
                equity += pos["entry_price"] * pos["shares"]
        equity_curve.append({"date": day_ts, "equity": equity})

    # Force-close remaining positions at end
    for ticker, pos in list(positions.items()):
        last_day = trading_days[-1]
        if ticker in price_data and pd.Timestamp(last_day) in price_data[ticker].index:
            exit_price = price_data[ticker].loc[pd.Timestamp(last_day), "Close"]
        else:
            exit_price = pos["entry_price"]
        exit_price *= (1 - SLIPPAGE_PCT / 100)
        pnl = (exit_price - pos["entry_price"]) * pos["shares"] - 2 * COMMISSION_PER_TRADE
        trades.append({
            "ticker": ticker,
            "entry_date": pos["entry_date"],
            "exit_date": pd.Timestamp(last_day),
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "shares": pos["shares"],
            "pnl": pnl,
            "exit_reason": "end_of_period",
            "hold_days": (pd.Timestamp(last_day) - pos["entry_date"]).days,
        })

    return _compute_metrics(trades, equity_curve)


def _empty_results():
    return {
        "sharpe_ratio": -999.0,
        "total_return_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "num_trades": 0,
        "win_rate_pct": 0.0,
        "avg_hold_days": 0.0,
        "profit_factor": 0.0,
        "avg_pnl_per_trade": 0.0,
    }


def _compute_metrics(trades, equity_curve):
    """Compute performance metrics from trade list and equity curve."""
    if not equity_curve:
        return _empty_results()

    eq_df = pd.DataFrame(equity_curve).set_index("date")
    eq_df = eq_df.sort_index()

    # Daily returns
    daily_returns = eq_df["equity"].pct_change().dropna()

    # Sharpe ratio (annualized)
    if len(daily_returns) < 10 or daily_returns.std() == 0:
        sharpe = -999.0
    else:
        excess_returns = daily_returns - RISK_FREE_RATE / 252
        sharpe = float(np.sqrt(252) * excess_returns.mean() / daily_returns.std())

    # Total return
    total_return = (eq_df["equity"].iloc[-1] / INITIAL_CAPITAL - 1) * 100

    # Max drawdown
    peak = eq_df["equity"].expanding().max()
    drawdown = (eq_df["equity"] - peak) / peak
    max_drawdown = float(drawdown.min() * 100)

    # Trade statistics
    num_trades = len(trades)
    if num_trades == 0:
        return {
            "sharpe_ratio": sharpe,
            "total_return_pct": float(total_return),
            "max_drawdown_pct": max_drawdown,
            "num_trades": 0,
            "win_rate_pct": 0.0,
            "avg_hold_days": 0.0,
            "profit_factor": 0.0,
            "avg_pnl_per_trade": 0.0,
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    win_rate = len(wins) / num_trades * 100 if num_trades > 0 else 0
    avg_hold = np.mean([t["hold_days"] for t in trades])
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.001
    profit_factor = gross_profit / gross_loss

    return {
        "sharpe_ratio": round(sharpe, 4),
        "total_return_pct": round(float(total_return), 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "num_trades": num_trades,
        "win_rate_pct": round(win_rate, 1),
        "avg_hold_days": round(float(avg_hold), 1),
        "profit_factor": round(profit_factor, 2),
        "avg_pnl_per_trade": round(float(np.mean(pnls)), 2),
    }


# ─────────────────────────────────────────────────────────────
# WALK-FORWARD EVALUATION
# ─────────────────────────────────────────────────────────────

def evaluate(use_test=False):
    """
    Main evaluation function. Imports strategy.py, generates signals,
    runs walk-forward backtest, returns average Sharpe across windows.
    """
    # Import strategy module dynamically
    strategy_path = Path(__file__).parent / "strategy.py"
    if not strategy_path.exists():
        print("ERROR: strategy.py not found!")
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("strategy", str(strategy_path))
    strategy = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(strategy)
    except Exception as e:
        print(f"ERROR loading strategy.py: {e}")
        sys.exit(1)

    if not hasattr(strategy, "generate_signals"):
        print("ERROR: strategy.py must define generate_signals(data, start_date, end_date)")
        sys.exit(1)

    # Load data
    all_data = load_all_data()
    if not all_data:
        print("ERROR: No data loaded. Run: python prepare.py --download")
        sys.exit(1)

    if use_test:
        # Single run on test set
        print("=" * 60)
        print("RUNNING ON TEST SET (out-of-sample)")
        print("=" * 60)
        signals = strategy.generate_signals(all_data, TEST_START, TEST_END)
        results = run_backtest(signals, all_data, TEST_START, TEST_END)
        _print_results(results, "TEST")
        return results

    # Walk-forward validation
    window_results = []
    print("=" * 60)
    print("WALK-FORWARD VALIDATION")
    print("=" * 60)

    for i, (ws, we) in enumerate(WALK_FORWARD_WINDOWS):
        print(f"\n--- Window {i+1}: {ws} to {we} ---")
        try:
            signals = strategy.generate_signals(all_data, ws, we)
            results = run_backtest(signals, all_data, ws, we)
            window_results.append(results)
            print(f"  Sharpe: {results['sharpe_ratio']:.4f}  |  "
                  f"Return: {results['total_return_pct']:.1f}%  |  "
                  f"Trades: {results['num_trades']}  |  "
                  f"MaxDD: {results['max_drawdown_pct']:.1f}%")
        except Exception as e:
            print(f"  ERROR: {e}")
            window_results.append(_empty_results())

    if not window_results:
        print("\nERROR: No valid window results")
        return _empty_results()

    # Average metrics across windows
    avg_results = {}
    for key in window_results[0].keys():
        values = [w[key] for w in window_results]
        avg_results[key] = round(float(np.mean(values)), 4)

    # Penalty for inconsistency: subtract std of Sharpe across windows
    sharpes = [w["sharpe_ratio"] for w in window_results if w["sharpe_ratio"] > -999]
    if len(sharpes) >= 2:
        sharpe_std = float(np.std(sharpes))
        consistency_penalty = sharpe_std * 0.5
        avg_results["sharpe_raw"] = avg_results["sharpe_ratio"]
        avg_results["sharpe_std"] = round(sharpe_std, 4)
        avg_results["consistency_penalty"] = round(consistency_penalty, 4)
        avg_results["sharpe_ratio"] = round(avg_results["sharpe_ratio"] - consistency_penalty, 4)

    # Minimum trade count guard: penalize strategies with too few trades
    if avg_results["num_trades"] < 3:
        avg_results["sharpe_ratio"] = -999.0
        print("\nPENALTY: Too few trades (< 3 avg per window). Sharpe set to -999.")

    print("\n" + "=" * 60)
    _print_results(avg_results, "VALIDATION (walk-forward avg)")
    print("=" * 60)

    # Print the key metric for the agent to grep
    print(f"\nval_sharpe: {avg_results['sharpe_ratio']:.4f}")
    print(f"val_return: {avg_results['total_return_pct']:.2f}")
    print(f"val_maxdd: {avg_results['max_drawdown_pct']:.2f}")
    print(f"val_trades: {avg_results['num_trades']}")
    print(f"val_winrate: {avg_results['win_rate_pct']:.1f}")

    return avg_results


def _print_results(results, label):
    """Pretty-print results."""
    print(f"\n  [{label}]")
    print(f"  Sharpe Ratio:      {results['sharpe_ratio']:.4f}")
    if "sharpe_raw" in results:
        print(f"    (raw: {results['sharpe_raw']:.4f}, "
              f"std: {results['sharpe_std']:.4f}, "
              f"penalty: -{results['consistency_penalty']:.4f})")
    print(f"  Total Return:      {results['total_return_pct']:.2f}%")
    print(f"  Max Drawdown:      {results['max_drawdown_pct']:.2f}%")
    print(f"  Num Trades:        {results['num_trades']}")
    print(f"  Win Rate:          {results['win_rate_pct']:.1f}%")
    print(f"  Avg Hold Days:     {results['avg_hold_days']:.1f}")
    print(f"  Profit Factor:     {results['profit_factor']:.2f}")
    print(f"  Avg PnL/Trade:     ${results['avg_pnl_per_trade']:.2f}")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autoresearch Trading - Evaluation Harness")
    parser.add_argument("--download", action="store_true", help="Force re-download data")
    parser.add_argument("--test", action="store_true", help="Run on held-out test set")
    args = parser.parse_args()

    if args.download:
        download_data(force=True)
    else:
        download_data(force=False)

    evaluate(use_test=args.test)
