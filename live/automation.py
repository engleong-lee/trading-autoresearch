"""
automation.py — R7 daily trading automation (Claude Cowork job).

Runs once per US-market day (schedule ~10:00 PM MYT, Mon–Fri):
  1. Connect to Alpaca (paper); skip if market closed.
  2. Load active watchlist from Postgres (ticker_watchlist).
  3. Fetch OHLCV (yfinance) + benchmarks; run R7 strategy for today's signals.
  4. Cross-check BUY signals against news_signals (supplementary veto only).
  5. Execute on Alpaca: SELL first, then BUY (1% equity risk/ticker, max 5 positions),
     then log UPDATE_STOP.
  6. Build a markdown report, upsert it to autoresearch_trading_result.
  7. Broadcast the report to Telegram subscribers.

Usage:
  python -m live.automation            # live paper run
  python -m live.automation --dry-run  # compute signals + report, place NO orders
  python -m live.automation --status   # show Alpaca account/positions
"""

import sys
import logging
import argparse
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from live.config import (
    WATCHLIST, BENCHMARKS, HISTORY_DAYS, LOG_FILE,
    RISK_PER_TRADE, MAX_POSITIONS,
)
from live import db, news, notify
from live.trader import (
    get_alpaca_client, get_current_positions, get_open_orders,
    place_bracket_order, place_market_sell, show_status, get_todays_signals,
)

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────
log_path = Path(__file__).parent.parent / LOG_FILE
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("trader.automation")


# ─────────────────────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────────────────────

def fetch_market_data(tickers):
    """Fetch OHLCV for tickers + benchmarks via yfinance (title-cased columns)."""
    import yfinance as yf

    all_tickers = list(dict.fromkeys(tickers + BENCHMARKS))
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
            key = "VIX" if ticker == "^VIX" else ticker
            data[key] = df
            log.info("  Fetched %s: %d bars, latest %s", key, len(df),
                     df.index[-1].date() if len(df) else "n/a")
        except Exception as e:
            log.warning("  Failed to fetch %s: %s", ticker, e)
    return data


# ─────────────────────────────────────────────────────────────
# POSITION SIZING  (override strategy sizing: 1% equity risk, max 5 positions)
# ─────────────────────────────────────────────────────────────

def size_position(equity, buying_power, entry_price, stop_loss):
    """Return share qty risking RISK_PER_TRADE of equity, capped by buying power."""
    per_share_risk = entry_price - stop_loss
    if per_share_risk <= 0 or entry_price <= 0:
        return 0
    risk_amount = equity * RISK_PER_TRADE
    qty = int(risk_amount // per_share_risk)
    if qty <= 0:
        return 0
    # cap by available buying power
    max_by_bp = int(buying_power // entry_price)
    return max(0, min(qty, max_by_bp))


# ─────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────

def build_report(trade_date, watchlist, decisions, account, positions, orders_summary):
    lines = []
    lines.append("# 📊 Autoresearch Trading Daily Report")
    lines.append(f"**Date:** {trade_date} {datetime.now().strftime('%H:%M:%S')}")
    lines.append("**Strategy:** R7 (Walk-forward Sharpe optimized)")
    lines.append(f"**Watchlist:** {', '.join(watchlist)}")
    lines.append("")
    lines.append("## 🎯 Strategy Signals vs News Sentiment")
    if decisions:
        lines.append("| Ticker | Signal | News Score | Decision | Reasoning |")
        lines.append("|--------|--------|------------|----------|-----------|")
        for d in decisions:
            lines.append(
                f"| {d['ticker']} | {d['signal']} | {d['emoji']} {d['score']:+.2f} "
                f"| **{d['decision']}** | {d['reasoning']} |"
            )
    else:
        lines.append("_No actionable strategy signals today._")
    lines.append("")
    lines.append("## 💼 Execution")
    if orders_summary:
        for o in orders_summary:
            lines.append(f"- {o}")
    else:
        lines.append("- No orders placed.")
    lines.append("")
    lines.append("## 📈 Account")
    if account:
        lines.append(f"- Equity: ${account['equity']:,.2f} | Cash: ${account['cash']:,.2f} "
                     f"| Buying Power: ${account['buying_power']:,.2f} "
                     f"| Day trades: {account['daytrade_count']}/3")
    if positions:
        lines.append(f"- Open positions ({len(positions)}):")
        for t, p in positions.items():
            lines.append(f"  - {t}: {p['qty']} @ ${p['entry_price']:.2f} "
                         f"→ ${p['current_price']:.2f} "
                         f"(PnL ${p['unrealized_pnl']:.2f}, {p['unrealized_pnl_pct']:.1f}%)")
    else:
        lines.append("- No open positions.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run(dry_run=False):
    log.info("=" * 60)
    log.info("R7 DAILY AUTOMATION — %s | mode=%s",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "DRY RUN" if dry_run else "LIVE PAPER")

    conn = db.connect()

    # Always refresh Telegram subscriber list from getUpdates
    try:
        notify.sync_subscribers(conn)
    except Exception as e:
        log.warning("Subscriber sync failed: %s", e)

    client = get_alpaca_client()
    acct = client.get_account()
    equity = float(acct.equity)
    buying_power = float(acct.buying_power)
    account = {
        "equity": equity, "cash": float(acct.cash),
        "buying_power": buying_power, "daytrade_count": acct.daytrade_count,
    }

    # Market open guard
    clock = client.get_clock()
    trade_date = datetime.now().strftime("%Y-%m-%d")
    if not clock.is_open:
        log.info("Market CLOSED. Next open: %s", clock.next_open)
        if dry_run:
            log.info("[DRY RUN] continuing to preview signals; no orders/DB/Telegram writes.")
        else:
            report = (f"# 📊 R7 Daily Report — {trade_date}\n\n"
                      f"Market is **closed**. No trading. Next open: {clock.next_open}")
            db.upsert_daily_result(conn, trade_date, report, status="market_closed")
            notify.broadcast(conn, report)
            conn.close()
            return

    # Watchlist: DB active ∩ strategy-known tickers
    try:
        db_watch = db.get_active_watchlist(conn)
    except Exception as e:
        log.warning("Watchlist read failed (%s); using config fallback", e)
        db_watch = WATCHLIST
    watchlist = [t for t in db_watch if t in WATCHLIST]
    skipped = [t for t in db_watch if t not in WATCHLIST]
    if skipped:
        log.warning("DB tickers not supported by strategy (skipped): %s", skipped)
    log.info("Trading watchlist: %s", watchlist)

    # Data + signals
    data = fetch_market_data(watchlist)
    signals = get_todays_signals(data)

    positions = get_current_positions(client)
    log.info("Current positions: %d", len(positions))

    # Symbols that already have exposure OR a live open order (avoid duplicate buys)
    open_order_symbols = set()
    try:
        open_order_symbols = {o.symbol for o in get_open_orders(client)}
    except Exception as e:
        log.warning("Could not fetch open orders: %s", e)

    decisions = []
    orders_summary = []

    if not signals.empty:
        sells = signals[signals["action"] == "SELL"]
        buys = signals[signals["action"] == "BUY"]
        updates = signals[signals["action"] == "UPDATE_STOP"]

        # 1) SELL first
        for _, sig in sells.iterrows():
            t = sig["ticker"]
            if t in positions:
                qty = positions[t]["qty"]
                place_market_sell(client, t, qty, dry_run=dry_run)
                orders_summary.append(f"SELL {qty} {t} @ market (exit)")
                decisions.append(dict(ticker=t, signal="SELL", emoji="➖", score=0.0,
                                      decision="EXECUTE", reasoning="Strategy exit"))
            else:
                orders_summary.append(f"SELL {t} skipped — no position held")

        # 2) BUY with news veto + risk sizing + max positions
        held = set(positions.keys())
        slots_left = MAX_POSITIONS - len(held)
        for _, sig in buys.iterrows():
            t = sig["ticker"]
            entry = float(sig["price"])
            stop = float(sig["stop_loss"]) if pd.notna(sig["stop_loss"]) else None
            tp = float(sig["take_profit"]) if pd.notna(sig["take_profit"]) else None

            score, high_bear, headline, n = news.score_ticker_news(conn, t, trade_date)
            emoji = news.sentiment_emoji(score)
            reason = f"Strategy: BUY • News: {emoji} ({score:+.2f})"
            if headline:
                reason += f" [news: {headline[:120]}]"

            if t in held or t in open_order_symbols:
                dec = "SKIP_ALREADY_HELD"
            elif stop is None:
                dec = "SKIP_NO_STOP"
            elif slots_left <= 0:
                dec = "SKIP_MAX_POSITIONS"
            elif news.news_veto(score, high_bear):
                dec = "SKIP_BEARISH_NEWS"
            else:
                qty = size_position(equity, buying_power, entry, stop)
                if qty <= 0:
                    dec = "SKIP_QTY_ZERO"
                else:
                    if tp:
                        place_bracket_order(client, t, qty, entry, stop, tp, dry_run=dry_run)
                        orders_summary.append(
                            f"BUY {qty} {t} @ ${entry:.2f} SL ${stop:.2f} TP ${tp:.2f} (bracket)")
                    else:
                        _place_limit_stop(client, t, qty, entry, stop, dry_run)
                        orders_summary.append(
                            f"BUY {qty} {t} @ ${entry:.2f} SL ${stop:.2f} (trend, no TP)")
                    slots_left -= 1
                    buying_power -= qty * entry
                    dec = "EXECUTE"

            decisions.append(dict(ticker=t, signal="BUY", emoji=emoji, score=score,
                                  decision=dec, reasoning=reason))

        # 3) UPDATE_STOP (logged; enforced next run)
        for _, sig in updates.iterrows():
            t = sig["ticker"]
            ns = float(sig["stop_loss"]) if pd.notna(sig["stop_loss"]) else None
            if ns and t in positions:
                orders_summary.append(f"UPDATE_STOP {t} → ${ns:.2f} (noted)")

    # Refresh positions after execution for the report
    if not dry_run:
        try:
            positions = get_current_positions(client)
        except Exception:
            pass

    report = build_report(trade_date, watchlist, decisions, account, positions, orders_summary)
    status = "dry_run" if dry_run else "completed"
    if not dry_run:
        db.upsert_daily_result(conn, trade_date, report, status=status)
        notify.broadcast(conn, report)
    else:
        log.info("\n%s", report)

    # Refresh the order-lifecycle history table (for trade debugging + weekly stats).
    # Fully guarded: this is reporting only and must never affect the trading path.
    if not dry_run:
        try:
            from live import trade_history
            trade_history.sync_orders(conn, client)
        except Exception as e:
            log.warning("Trade-history sync failed (non-fatal): %s", e)

    conn.close()
    log.info("SESSION COMPLETE")


def _place_limit_stop(client, ticker, qty, entry, stop, dry_run):
    """OTO limit-buy + stop-loss (trend trades with no take-profit)."""
    from alpaca.trading.requests import LimitOrderRequest, StopLossRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderType, OrderClass
    log.info("%sBUY(OTO) %d %s @ $%.2f SL $%.2f",
             "[DRY] " if dry_run else "", qty, ticker, entry, stop)
    if dry_run:
        return
    try:
        req = LimitOrderRequest(
            symbol=ticker, qty=qty, side=OrderSide.BUY, type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY, limit_price=round(entry, 2),
            order_class=OrderClass.OTO,
            stop_loss=StopLossRequest(stop_price=round(stop, 2)),
        )
        order = client.submit_order(req)
        log.info("  Order placed: %s", order.id)
    except Exception as e:
        log.error("  OTO order failed: %s", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="R7 Daily Trading Automation")
    parser.add_argument("--dry-run", action="store_true", help="No orders; print report")
    parser.add_argument("--status", action="store_true", help="Show Alpaca status")
    args = parser.parse_args()

    if args.status:
        show_status(get_alpaca_client())
        sys.exit(0)

    try:
        run(dry_run=args.dry_run)
    except Exception as e:
        log.error("FATAL: %s\n%s", e, traceback.format_exc())
        # Best-effort error logging + alert
        try:
            conn = db.connect()
            td = datetime.now().strftime("%Y-%m-%d")
            msg = f"# ❌ R7 Daily Report — {td}\n\nRun FAILED:\n```\n{e}\n```"
            db.upsert_daily_result(conn, td, msg, status="error")
            notify.broadcast(conn, msg)
            conn.close()
        except Exception as e2:
            log.error("Error handler also failed: %s", e2)
        sys.exit(1)
