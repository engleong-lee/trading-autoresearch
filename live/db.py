"""
db.py — Postgres helpers for the R7 trading automation (NocoDB-backed DB).

All tables live in schema config.PG_SCHEMA. Identifiers are double-quoted because
some columns are capitalized ("Datetime", "Summary", "Ticker", ...).
"""

import json
import logging
from datetime import datetime

import psycopg2
import psycopg2.extras

from live.config import (
    PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD, PG_SCHEMA,
    NEWS_LOOKBACK_DAYS,
)

log = logging.getLogger("trader.db")


def connect():
    """Open a psycopg2 connection. gssencmode disabled to avoid GSSAPI stalls."""
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASSWORD,
        connect_timeout=15, gssencmode="disable",
    )


def _q(table):
    return f'"{PG_SCHEMA}"."{table}"'


# ─────────────────────────────────────────────────────────────
# WATCHLIST
# ─────────────────────────────────────────────────────────────

def get_active_watchlist(conn):
    """Return list of active tickers from ticker_watchlist ("Active" = true)."""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT "Ticker" FROM {_q("ticker_watchlist")} '
            f'WHERE "Active" = true AND "Ticker" IS NOT NULL ORDER BY id'
        )
        return [r[0].strip() for r in cur.fetchall() if r[0]]


# ─────────────────────────────────────────────────────────────
# NEWS SIGNALS
# ─────────────────────────────────────────────────────────────

def get_recent_news_for_ticker(conn, ticker, as_of_date, lookback_days=NEWS_LOOKBACK_DAYS):
    """
    Return recent news_signals rows that reference `ticker` in entities.tickers.
    Each row: dict(signal_date, sentiment, confidence, impact_magnitude, summary).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT signal_date, sentiment, confidence, impact_magnitude,
                   impact_timeframe, event_type, summary, entities
            FROM {_q("news_signals")}
            WHERE signal_date >= (%s::date - %s)
              AND signal_date <= %s::date
              AND entities::text ILIKE %s
            ORDER BY signal_date DESC
            """,
            (as_of_date, lookback_days, as_of_date, f'%"{ticker}"%'),
        )
        return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────
# DAILY RESULT (upsert one row per US trading date)
# ─────────────────────────────────────────────────────────────

def upsert_daily_result(conn, trade_date, report, status="completed"):
    """Insert or update the autoresearch_trading_result row for trade_date."""
    now = datetime.now()
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT id FROM {_q("autoresearch_trading_result")} WHERE date = %s ORDER BY id DESC LIMIT 1',
            (trade_date,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                f'UPDATE {_q("autoresearch_trading_result")} '
                f'SET report=%s, status=%s, timestamp=%s, updated_at=%s WHERE id=%s',
                (report, status, now, now, row[0]),
            )
            rid = row[0]
        else:
            cur.execute(
                f'INSERT INTO {_q("autoresearch_trading_result")} '
                f'(created_at, updated_at, date, timestamp, report, status) '
                f'VALUES (%s,%s,%s,%s,%s,%s) RETURNING id',
                (now, now, trade_date, now, report, status),
            )
            rid = cur.fetchone()[0]
    conn.commit()
    log.info(f"Persisted daily result id={rid} date={trade_date} status={status}")
    return rid


def get_results_between(conn, start_date, end_date):
    """Fetch daily result rows in [start_date, end_date] inclusive (for weekly summary)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f'SELECT date, timestamp, status, report FROM {_q("autoresearch_trading_result")} '
            f'WHERE date >= %s AND date <= %s ORDER BY date',
            (start_date, end_date),
        )
        return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────
# WEEKLY SUMMARY
# ─────────────────────────────────────────────────────────────

def insert_weekly_summary(conn, summary_text):
    now = datetime.now()
    with conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO {_q("autoresearch_trading_weekly_summary")} '
            f'(created_at, updated_at, "Datetime", "Summary") VALUES (%s,%s,%s,%s) RETURNING id',
            (now, now, now, summary_text),
        )
        rid = cur.fetchone()[0]
    conn.commit()
    log.info(f"Persisted weekly summary id={rid}")
    return rid


# ─────────────────────────────────────────────────────────────
# TELEGRAM SUBSCRIBERS
# ─────────────────────────────────────────────────────────────

def ensure_subscriber_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE IF NOT EXISTS {_q("telegram_subscribers")} '
            f'(chat_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, '
            f'active BOOLEAN DEFAULT true, added_at TIMESTAMP DEFAULT now())'
        )
    conn.commit()


def upsert_subscriber(conn, chat_id, username=None, first_name=None):
    with conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO {_q("telegram_subscribers")} (chat_id, username, first_name) '
            f'VALUES (%s,%s,%s) ON CONFLICT (chat_id) DO UPDATE SET '
            f'username=EXCLUDED.username, first_name=EXCLUDED.first_name, active=true',
            (chat_id, username, first_name),
        )
    conn.commit()


def get_active_subscribers(conn):
    with conn.cursor() as cur:
        cur.execute(f'SELECT chat_id FROM {_q("telegram_subscribers")} WHERE active = true')
        return [r[0] for r in cur.fetchall()]


def deactivate_subscriber(conn, chat_id):
    with conn.cursor() as cur:
        cur.execute(
            f'UPDATE {_q("telegram_subscribers")} SET active=false WHERE chat_id=%s',
            (chat_id,),
        )
    conn.commit()
