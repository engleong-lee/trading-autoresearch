"""
trader.py — Daily cron-based paper trading bot using R2 strategy + Alpaca API.

This script:
  1. Fetches latest OHLCV data from yfinance (free, no API key needed)
  2. Fetches VIX for market regime detection
  3. Runs the R2 strategy logic to generate today's signals
  4. Checks existing Alpaca positions and manages them (trailing stops, exits)
  5. Places new orders via Alpaca bracket orders (entry + stop-loss + take-profit)
  6. Logs everything to trading.log

Usage:
  python live/trader.py              # Run once (for cron)
  python live/trader.py --dry-run    # Show signals without placing orders
  python live/trader.py --status     # Show current positions and account

Schedule with cron (30 min after market open, Mon-Fri):
  30 10 * * 1-5 cd ~/research/trading-autoresearch && /usr/bin/python3 live/trader.py >> live/cron.log 2>&1
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Add parent dir to path so we can import strategy.py
sys.path.insert(0, str(Path(__file__).parent.parent))

from live.config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL,
    WATCHLIST, HISTORY_DAYS, LOG_FILE,
)

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────

log_path = Path(__file__).parent.parent / LOG_FILE
log_path.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_path),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("trader")

# ─────────────────────────────────────────────────────────────
# ALPACA CLIENT
# ─────────────────────────────────────────────────────────────

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest,
        StopLossRequest, TakeProfitRequest,
        GetOrdersRequest,
    )
    from alpaca.trading.enums import (
        OrderSide, TimeInForce, OrderType,
        OrderClass, QueryOrderStatus,
    )
except ImportError:
    log.error("alpaca-py not installed. Run: pip install alpaca-py")
    sys.exit(1)


def get_alpaca_client():
    """Initialize Alpaca trading client."""
    if ALPACA_API_KEY == "YOUR_PAPER_API_KEY_HERE":
        log.error("Please set your Alpaca API keys in live/config.py")
        sys.exit(1)
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

# ─────────────────────────────────────────────────────────────
# DATA FETCHING
# ─────────────────────────────────────────────────────────────

def fetch_market_data():
    """Fetch latest OHLCV data for all tickers + benchmarks via yfinance."""
    import yfinance as yf

    all_tickers = WATCHLIST + ["SPY", "QQQ", "^VIX"]
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")

    data = {}
    for ticker in all_tickers:
        try:
            df = yf.download(ticker, start=start_date, end=end_date,
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.strip().title() for c in df.columns]
            # Store VIX without the ^ prefix
            key = "VIX" if ticker == "^VIX" else ticker
            data[key] = df
            log.info(f"  Fetched {key}: {len(df)} bars, latest {df.index[-1].date()}")
        except Exception as e:
            log.warning(f"  Failed to fetch {ticker}: {e}")

    return data

# ─────────────────────────────────────────────────────────────
# GENERATE TODAY'S SIGNALS
# ─────────────────────────────────────────────────────────────

def get_todays_signals(data):
    """
    Import strategy.py and generate signals for today.
    We run the strategy for the last 5 trading days and only
    take signals from the most recent day.
    """
    import importlib.util

    strategy_path = Path(__file__).parent.parent / "strategy.py"
    spec = importlib.util.spec_from_file_location("strategy", str(strategy_path))
    strategy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(strategy)

    # Generate signals for the last 5 days to allow indicator warmup
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

    signals_df = strategy.generate_signals(data, start_date, end_date)

    if signals_df.empty:
        log.info("No signals generated for recent period")
        return pd.DataFrame()

    # Filter to only the most recent day's signals
    signals_df["date"] = pd.to_datetime(signals_df["date"])
    latest_date = signals_df["date"].max()
    today_signals = signals_df[signals_df["date"] == latest_date].copy()

    log.info(f"Signals for {latest_date.date()}: {len(today_signals)} signals")
    for _, sig in today_signals.iterrows():
        log.info(f"  {sig['action']} {sig['ticker']} @ ${sig['price']:.2f} "
                 f"SL=${sig.get('stop_loss', 'N/A')} "
                 f"TP=${sig.get('take_profit', 'N/A')} "
                 f"Qty={sig.get('position_size', 'N/A')}")

    return today_signals

# ─────────────────────────────────────────────────────────────
# POSITION MANAGEMENT
# ─────────────────────────────────────────────────────────────

def get_current_positions(client):
    """Get all open positions from Alpaca."""
    positions = client.get_all_positions()
    pos_dict = {}
    for p in positions:
        pos_dict[p.symbol] = {
            "qty": int(p.qty),
            "entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_pnl": float(p.unrealized_pl),
            "unrealized_pnl_pct": float(p.unrealized_plpc) * 100,
            "market_value": float(p.market_value),
        }
    return pos_dict


def get_open_orders(client):
    """Get all open/pending orders."""
    request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
    orders = client.get_orders(filter=request)
    return orders

# ─────────────────────────────────────────────────────────────
# ORDER EXECUTION
# ─────────────────────────────────────────────────────────────

def place_bracket_order(client, ticker, qty, entry_price, stop_loss, take_profit, dry_run=False):
    """
    Place a bracket order: limit buy + stop-loss + take-profit.
    Alpaca bracket orders automatically manage the exit legs.
    """
    log.info(f"{'[DRY RUN] ' if dry_run else ''}Placing bracket order: "
             f"BUY {qty} {ticker} @ limit ${entry_price:.2f}, "
             f"SL=${stop_loss:.2f}, TP=${take_profit:.2f}")

    if dry_run:
        return None

    try:
        order_data = LimitOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
            limit_price=round(entry_price, 2),
            order_class=OrderClass.BRACKET,
            stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
        )
        order = client.submit_order(order_data)
        log.info(f"  Order placed: {order.id} status={order.status}")
        return order
    except Exception as e:
        log.error(f"  Order failed: {e}")
        return None


def place_market_sell(client, ticker, qty, dry_run=False):
    """Place a market sell order to exit a position."""
    log.info(f"{'[DRY RUN] ' if dry_run else ''}Selling {qty} {ticker} at market")

    if dry_run:
        return None

    try:
        order_data = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(order_data)
        log.info(f"  Sell order placed: {order.id} status={order.status}")
        return order
    except Exception as e:
        log.error(f"  Sell order failed: {e}")
        return None

# ─────────────────────────────────────────────────────────────
# MAIN LOGIC
# ─────────────────────────────────────────────────────────────

def show_status(client):
    """Display current account status and positions."""
    account = client.get_account()
    positions = get_current_positions(client)
    orders = get_open_orders(client)

    log.info("=" * 60)
    log.info("ACCOUNT STATUS")
    log.info("=" * 60)
    log.info(f"  Equity:         ${float(account.equity):,.2f}")
    log.info(f"  Cash:           ${float(account.cash):,.2f}")
    log.info(f"  Buying Power:   ${float(account.buying_power):,.2f}")
    log.info(f"  Day Trades:     {account.daytrade_count}/3")

    if positions:
        log.info(f"\nOPEN POSITIONS ({len(positions)}):")
        for ticker, pos in positions.items():
            log.info(f"  {ticker}: {pos['qty']} shares @ ${pos['entry_price']:.2f} "
                     f"| now ${pos['current_price']:.2f} "
                     f"| PnL ${pos['unrealized_pnl']:.2f} ({pos['unrealized_pnl_pct']:.1f}%)")
    else:
        log.info("\nNo open positions.")

    if orders:
        log.info(f"\nOPEN ORDERS ({len(orders)}):")
        for o in orders:
            log.info(f"  {o.side} {o.qty} {o.symbol} @ ${o.limit_price or o.stop_price or 'MKT'} "
                     f"| type={o.order_type} status={o.status}")
    else:
        log.info("\nNo open orders.")
    log.info("=" * 60)


def run_trading_session(dry_run=False):
    """Main daily trading session."""
    log.info("=" * 60)
    log.info(f"TRADING SESSION — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE PAPER'}")
    log.info("=" * 60)

    # 1. Init Alpaca client
    client = get_alpaca_client()
    account = client.get_account()
    equity = float(account.equity)
    log.info(f"Account equity: ${equity:,.2f}")

    # Check if market is open
    clock = client.get_clock()
    if not clock.is_open:
        log.info("Market is CLOSED. Skipping trading session.")
        log.info(f"  Next open: {clock.next_open}")
        return

    # 2. Fetch market data
    log.info("\nFetching market data...")
    data = fetch_market_data()

    # 3. Get current positions
    positions = get_current_positions(client)
    log.info(f"\nCurrent positions: {len(positions)}")
    for ticker, pos in positions.items():
        log.info(f"  {ticker}: {pos['qty']} shares, PnL {pos['unrealized_pnl_pct']:.1f}%")

    # 4. Generate today's signals
    log.info("\nGenerating signals...")
    signals = get_todays_signals(data)

    if signals.empty:
        log.info("No signals today. Session complete.")
        return

    # 5. Process SELL signals first (close positions)
    sell_signals = signals[signals["action"] == "SELL"]
    for _, sig in sell_signals.iterrows():
        ticker = sig["ticker"]
        if ticker in positions:
            qty = positions[ticker]["qty"]
            place_market_sell(client, ticker, qty, dry_run=dry_run)
            log.info(f"  EXIT {ticker}: {qty} shares at market")
        else:
            log.info(f"  SELL signal for {ticker} but no position held — skipping")

    # 6. Process BUY signals (open new positions)
    buy_signals = signals[signals["action"] == "BUY"]
    for _, sig in buy_signals.iterrows():
        ticker = sig["ticker"]

        # Skip if already holding
        if ticker in positions:
            log.info(f"  BUY signal for {ticker} but already holding — skipping")
            continue

        # Skip if we'd exceed max positions
        if len(positions) >= 10:
            log.info(f"  BUY signal for {ticker} but at max positions (10) — skipping")
            break

        entry_price = float(sig["price"])
        stop_loss = float(sig["stop_loss"]) if pd.notna(sig["stop_loss"]) else None
        take_profit = float(sig["take_profit"]) if pd.notna(sig["take_profit"]) else None
        qty = int(sig.get("position_size", 0))

        if qty <= 0:
            log.info(f"  BUY signal for {ticker} but qty=0 — skipping")
            continue

        # Use bracket order if both SL and TP exist, otherwise limit order
        if stop_loss and take_profit:
            place_bracket_order(client, ticker, qty, entry_price,
                                stop_loss, take_profit, dry_run=dry_run)
        elif stop_loss:
            # Trend trade (no take-profit) — use limit entry + stop-loss only
            log.info(f"{'[DRY RUN] ' if dry_run else ''}Placing limit order: "
                     f"BUY {qty} {ticker} @ ${entry_price:.2f}, SL=${stop_loss:.2f}")
            if not dry_run:
                try:
                    order_data = LimitOrderRequest(
                        symbol=ticker,
                        qty=qty,
                        side=OrderSide.BUY,
                        type=OrderType.LIMIT,
                        time_in_force=TimeInForce.DAY,
                        limit_price=round(entry_price, 2),
                        order_class=OrderClass.OTO,
                        stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)),
                    )
                    order = client.submit_order(order_data)
                    log.info(f"  Order placed: {order.id}")
                except Exception as e:
                    log.error(f"  Order failed: {e}")
        else:
            log.info(f"  BUY signal for {ticker} but no stop-loss — skipping (unsafe)")

    # 7. Process UPDATE_STOP signals
    update_signals = signals[signals["action"] == "UPDATE_STOP"]
    for _, sig in update_signals.iterrows():
        ticker = sig["ticker"]
        new_stop = float(sig["stop_loss"]) if pd.notna(sig["stop_loss"]) else None
        if new_stop and ticker in positions:
            log.info(f"  UPDATE_STOP {ticker} → ${new_stop:.2f}")
            # Alpaca doesn't support modifying stop-loss on bracket orders easily
            # For paper trading, we log it and the next day's run will handle exits
            log.info(f"  (Stop update noted — will be enforced on next run via strategy logic)")

    log.info("\n" + "=" * 60)
    log.info("SESSION COMPLETE")
    log.info("=" * 60)

# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autoresearch Trading Bot — Paper Trading")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show signals without placing orders")
    parser.add_argument("--status", action="store_true",
                        help="Show current positions and account status")
    args = parser.parse_args()

    if args.status:
        client = get_alpaca_client()
        show_status(client)
    else:
        run_trading_session(dry_run=args.dry_run)
