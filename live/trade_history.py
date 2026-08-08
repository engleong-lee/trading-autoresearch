"""
trade_history.py — Full order-lifecycle capture + round-trip reconstruction.

WHY: the daily report shows equity/positions but not what happened to past orders
(filled? expired? stop-loss hit? take-profit hit?). This module reads the complete
order history from Alpaca (which retains it) and:

  1. sync_orders()      — flatten every order (parent entry + bracket legs) into
                          autoresearch_trade_history, one row per order, idempotent.
  2. reconstruct_trips()— FIFO-match filled buys to filled sells to derive closed
                          round-trips with P&L, holding period, and EXIT REASON
                          (TAKE-PROFIT / STOP-LOSS / STRATEGY SELL).
  3. stats()            — win-rate, avg win/loss, expectancy, total realized P&L.

This is a READ/REPORTING layer. It never places or cancels orders and does not
touch strategy.py or prepare.py.

Usage:
  python -m live.trade_history --sync            # upsert order lifecycle to Postgres
  python -m live.trade_history --sync --days 90  # limit history window (default: all)
  python -m live.trade_history --report          # print round-trips + stats to console
  python -m live.trade_history --debug SYMBOL    # print full lifecycle for one symbol
"""

import sys
import json
import logging
import argparse
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from live.config import LOG_FILE
from live import db
from live.trader import get_alpaca_client

log_path = Path(__file__).parent.parent / LOG_FILE
log_path.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    handlers=[logging.FileHandler(log_path), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("trader.history")


# ─────────────────────────────────────────────────────────────
# FETCH + FLATTEN
# ─────────────────────────────────────────────────────────────

def _s(v):
    """Enum/UUID/datetime -> plain string (or None)."""
    if v is None:
        return None
    if hasattr(v, "value"):        # alpaca enums
        return v.value
    return str(v)


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _dt(v):
    return v if v is not None else None


def _leg_role(order, parent_id):
    """Classify an order within a bracket: entry / take_profit / stop_loss / exit."""
    otype = _s(order.order_type) or _s(getattr(order, "type", None))
    side = _s(order.side)
    if parent_id is None:
        return "entry" if side == "buy" else "exit"
    # child legs are sells
    if otype == "limit":
        return "take_profit"
    if otype in ("stop", "stop_limit", "trailing_stop"):
        return "stop_loss"
    return "exit"


def _flatten(order, account, parent_id=None):
    """Yield a lifecycle-row dict for `order` and recurse into its legs."""
    oid = _s(order.id)
    role = _leg_role(order, parent_id)
    # raw JSON for ultimate debugging
    try:
        raw = json.dumps(order, default=str)
    except Exception:
        raw = None
    yield {
        "account": account,
        "order_id": oid,
        "client_order_id": _s(order.client_order_id),
        "parent_order_id": parent_id,
        "leg_role": role,
        "symbol": _s(order.symbol),
        "side": _s(order.side),
        "order_type": _s(order.order_type) or _s(getattr(order, "type", None)),
        "order_class": _s(order.order_class),
        "time_in_force": _s(order.time_in_force),
        "qty": _num(order.qty),
        "filled_qty": _num(order.filled_qty),
        "limit_price": _num(order.limit_price),
        "stop_price": _num(order.stop_price),
        "filled_avg_price": _num(order.filled_avg_price),
        "status": _s(order.status),
        "submitted_at": _dt(order.submitted_at),
        "filled_at": _dt(order.filled_at),
        "canceled_at": _dt(order.canceled_at),
        "expired_at": _dt(order.expired_at),
        "replaced_at": _dt(order.replaced_at),
        "raw": raw,
    }
    for leg in (order.legs or []):
        yield from _flatten(leg, account, parent_id=oid)


def fetch_orders(client, days=None):
    """Fetch ALL orders (nested legs) from Alpaca, newest-inclusive, paging by date.

    Alpaca caps each call at 500 orders; we page backwards using the oldest
    submitted_at seen until we exhaust history (or the `days` window)."""
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    floor = None
    if days:
        floor = datetime.now(timezone.utc) - timedelta(days=days)

    all_orders = {}
    until = None
    while True:
        req = GetOrdersRequest(
            status=QueryOrderStatus.ALL, nested=True, limit=500,
            direction="desc", until=until,
        )
        batch = client.get_orders(filter=req)
        if not batch:
            break
        for o in batch:
            all_orders[str(o.id)] = o
        oldest = min(o.submitted_at for o in batch)
        if floor and oldest <= floor:
            break
        if len(batch) < 500:
            break
        until = oldest  # page further back
    orders = list(all_orders.values())
    if floor:
        orders = [o for o in orders if o.submitted_at >= floor]
    return orders


# ─────────────────────────────────────────────────────────────
# SYNC
# ─────────────────────────────────────────────────────────────

def sync_orders(conn, client, days=None):
    """Upsert the full order lifecycle into autoresearch_trade_history."""
    account = _s(client.get_account().account_number)
    db.ensure_trade_history_table(conn)
    orders = fetch_orders(client, days=days)
    n = 0
    for o in orders:
        for row in _flatten(o, account):
            db.upsert_order_row(conn, row)
            n += 1
    log.info("Synced %d order-lifecycle rows (%d top-level orders) for account %s",
             n, len(orders), account)
    return n


# ─────────────────────────────────────────────────────────────
# ROUND-TRIP RECONSTRUCTION
# ─────────────────────────────────────────────────────────────

def reconstruct_trips(filled_rows):
    """FIFO-match filled buys to filled sells per symbol -> closed round-trips.

    Each trip: symbol, qty, entry_price, entry_at, exit_price, exit_at, pnl, pct,
    hold_days, exit_reason (TAKE-PROFIT / STOP-LOSS / STRATEGY SELL).
    """
    lots = defaultdict(deque)
    trips = []
    for r in filled_rows:
        sym = r["symbol"]
        qty = float(r["filled_qty"] or 0)      # Postgres NUMERIC -> Decimal -> float
        price = float(r["filled_avg_price"] or 0)
        if qty <= 0:
            continue
        if r["side"] == "buy":
            lots[sym].append([qty, price, r["filled_at"]])
        else:  # sell closes open buy lots
            reason = _exit_reason(r)
            remain = qty
            while remain > 1e-9 and lots[sym]:
                lot = lots[sym][0]
                take = min(lot[0], remain)
                pnl = (price - lot[1]) * take
                hold = None
                if lot[2] and r["filled_at"]:
                    hold = (r["filled_at"] - lot[2]).total_seconds() / 86400.0
                trips.append(dict(
                    symbol=sym, qty=take, entry_price=lot[1], entry_at=lot[2],
                    exit_price=price, exit_at=r["filled_at"],
                    pnl=pnl, pct=(price / lot[1] - 1) * 100 if lot[1] else 0,
                    hold_days=hold, exit_reason=reason,
                ))
                lot[0] -= take
                remain -= take
                if lot[0] < 1e-9:
                    lots[sym].popleft()
    open_lots = {s: [l for l in dq] for s, dq in lots.items() if dq}
    return trips, open_lots


def _exit_reason(sell_row):
    role = sell_row.get("leg_role")
    if role == "take_profit":
        return "TAKE-PROFIT"
    if role == "stop_loss":
        return "STOP-LOSS"
    otype = sell_row.get("order_type")
    if otype == "market":
        return "STRATEGY SELL"
    return "SELL"


def stats(trips):
    if not trips:
        return {}
    wins = [t for t in trips if t["pnl"] > 0]
    losses = [t for t in trips if t["pnl"] <= 0]
    total = sum(t["pnl"] for t in trips)
    n = len(trips)
    win_rate = len(wins) / n * 100
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    # expectancy per trade
    p_w = len(wins) / n
    expectancy = p_w * avg_win + (1 - p_w) * avg_loss
    by_reason = defaultdict(lambda: [0, 0.0])
    for t in trips:
        by_reason[t["exit_reason"]][0] += 1
        by_reason[t["exit_reason"]][1] += t["pnl"]
    return dict(n=n, total=total, win_rate=win_rate, avg_win=avg_win,
                avg_loss=avg_loss, expectancy=expectancy,
                by_reason={k: {"n": v[0], "pnl": v[1]} for k, v in by_reason.items()})


# ─────────────────────────────────────────────────────────────
# REPORT (markdown, reused by weekly summary)
# ─────────────────────────────────────────────────────────────

def render_markdown(trips, st, title="Closed Trades"):
    lines = [f"## 📕 {title}"]
    if not trips:
        lines.append("- No closed round-trips in this period.")
        return "\n".join(lines)
    lines.append(f"| Symbol | Qty | Entry | Exit | P&L | % | Hold | Exit |")
    lines.append(f"|--------|----:|------:|-----:|----:|--:|-----:|------|")
    for t in trips:
        hold = f"{t['hold_days']:.0f}d" if t["hold_days"] is not None else "—"
        lines.append(
            f"| {t['symbol']} | {t['qty']:.0f} | ${t['entry_price']:.2f} | "
            f"${t['exit_price']:.2f} | ${t['pnl']:,.2f} | {t['pct']:+.1f}% | "
            f"{hold} | {t['exit_reason']} |"
        )
    lines.append("")
    lines.append(
        f"**Realized P&L:** ${st['total']:,.2f} • **Trades:** {st['n']} • "
        f"**Win-rate:** {st['win_rate']:.0f}% • **Avg win:** ${st['avg_win']:,.2f} • "
        f"**Avg loss:** ${st['avg_loss']:,.2f} • **Expectancy:** ${st['expectancy']:,.2f}/trade"
    )
    if st.get("by_reason"):
        parts = [f"{k}: {v['n']} (${v['pnl']:,.0f})" for k, v in st["by_reason"].items()]
        lines.append("**By exit reason:** " + " • ".join(parts))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def _print_debug(conn, symbol):
    import psycopg2.extras
    from live.db import _q
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f'SELECT submitted_at, side, leg_role, order_type, qty, filled_qty, '
            f'limit_price, stop_price, filled_avg_price, status, filled_at, expired_at '
            f'FROM {_q("autoresearch_trade_history")} WHERE symbol=%s '
            f'ORDER BY submitted_at',
            (symbol.upper(),),
        )
        rows = cur.fetchall()
    print(f"\nFull lifecycle for {symbol.upper()} ({len(rows)} orders):")
    for r in rows:
        print(f"  {str(r['submitted_at'])[:19]} | {r['side']:4} {r['leg_role']:11} "
              f"{r['order_type']:6} qty={r['qty']} filled={r['filled_qty']} "
              f"lim={r['limit_price']} stop={r['stop_price']} "
              f"avg={r['filled_avg_price']} | {r['status']} "
              f"filled_at={str(r['filled_at'])[:19]} expired_at={str(r['expired_at'])[:19]}")


def main():
    ap = argparse.ArgumentParser(description="Trade history / order-lifecycle tool")
    ap.add_argument("--sync", action="store_true", help="Upsert order lifecycle to Postgres")
    ap.add_argument("--days", type=int, default=None, help="History window in days (default: all)")
    ap.add_argument("--report", action="store_true", help="Print round-trips + stats")
    ap.add_argument("--debug", metavar="SYMBOL", help="Print full lifecycle for one symbol")
    args = ap.parse_args()

    conn = db.connect()
    if args.sync:
        sync_orders(conn, get_alpaca_client(), days=args.days)
    if args.report:
        db.ensure_trade_history_table(conn)
        rows = db.get_all_filled_orders(conn)
        trips, open_lots = reconstruct_trips(rows)
        st = stats(trips)
        print(render_markdown(trips, st, title="Closed Trades (all history)"))
        if open_lots:
            print("\nStill open:")
            for s, lots in open_lots.items():
                for l in lots:
                    print(f"  {s} {l[0]:.0f} @ ${l[1]:.2f} since {str(l[2])[:19]}")
    if args.debug:
        _print_debug(conn, args.debug)
    conn.close()


if __name__ == "__main__":
    main()
