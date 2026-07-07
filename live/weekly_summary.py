"""
weekly_summary.py — Weekly trading summary (Claude Cowork job, Sundays).

Aggregates the past week's autoresearch_trading_result rows (Mon–Fri), summarizes
signals/decisions and executed orders, pulls current Alpaca account state, writes
the summary to autoresearch_trading_weekly_summary, and broadcasts via Telegram.

Usage:
  python -m live.weekly_summary
"""

import re
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from live.config import LOG_FILE
from live import db, notify
from live.trader import get_alpaca_client, get_current_positions

log_path = Path(__file__).parent.parent / LOG_FILE
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("trader.weekly")


def _week_bounds(today=None):
    """Return (monday, friday) dates for the trading week that just finished.

    weekday(): Mon=0 .. Sun=6. Intended to run Sunday: the finished week is the
    immediately preceding Mon–Fri. For any other run day, use the current week's
    Monday (so ad-hoc runs still work).
    """
    today = today or datetime.now().date()
    if today.weekday() == 6:  # Sunday -> previous Monday
        monday = today - timedelta(days=6)
    else:
        monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


def _count_decisions(report_text):
    """Count decision tags like **EXECUTE**, **SKIP_BEARISH_NEWS** in a report."""
    tags = re.findall(r"\*\*(EXECUTE|SKIP_[A-Z_]+)\*\*", report_text or "")
    counts = {}
    for t in tags:
        counts[t] = counts.get(t, 0) + 1
    return counts


def build_weekly_summary(rows, account, positions, monday, friday):
    lines = []
    lines.append(f"# 🗓️ Weekly Trading Summary ({monday} → {friday})")
    lines.append("**Strategy:** R7")
    lines.append("")
    lines.append("## Session Activity")
    if rows:
        for r in rows:
            lines.append(f"- {r['date']}: status={r['status']}")
    else:
        lines.append("- No daily records this week.")
    lines.append("")

    # Aggregate decisions across the week
    agg = {}
    for r in rows:
        for tag, n in _count_decisions(r.get("report")).items():
            agg[tag] = agg.get(tag, 0) + n
    lines.append("## Signals & Decisions (week total)")
    if agg:
        for tag in sorted(agg):
            lines.append(f"- {tag}: {agg[tag]}")
    else:
        lines.append("- No decisions recorded.")
    lines.append("")

    lines.append("## Account (current)")
    if account:
        lines.append(f"- Equity: ${account['equity']:,.2f} | Cash: ${account['cash']:,.2f} "
                     f"| Buying Power: ${account['buying_power']:,.2f}")
    if positions:
        total_pnl = sum(p["unrealized_pnl"] for p in positions.values())
        lines.append(f"- Open positions ({len(positions)}), unrealized PnL ${total_pnl:,.2f}:")
        for t, p in positions.items():
            lines.append(f"  - {t}: {p['qty']} @ ${p['entry_price']:.2f} "
                         f"({p['unrealized_pnl_pct']:.1f}%)")
    else:
        lines.append("- No open positions.")
    return "\n".join(lines)


def run():
    log.info("=" * 60)
    log.info("R7 WEEKLY SUMMARY — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    conn = db.connect()

    monday, friday = _week_bounds()
    rows = db.get_results_between(conn, monday.isoformat(), friday.isoformat())
    log.info("Found %d daily rows for %s..%s", len(rows), monday, friday)

    account, positions = None, {}
    try:
        client = get_alpaca_client()
        acct = client.get_account()
        account = {"equity": float(acct.equity), "cash": float(acct.cash),
                   "buying_power": float(acct.buying_power)}
        positions = get_current_positions(client)
    except Exception as e:
        log.warning("Alpaca fetch failed: %s", e)

    summary = build_weekly_summary(rows, account, positions, monday, friday)
    db.insert_weekly_summary(conn, summary)

    try:
        notify.sync_subscribers(conn)
    except Exception as e:
        log.warning("Subscriber sync failed: %s", e)
    notify.broadcast(conn, summary)

    conn.close()
    log.info("WEEKLY SUMMARY COMPLETE")


if __name__ == "__main__":
    run()
