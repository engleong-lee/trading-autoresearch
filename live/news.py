"""
news.py — Supplementary news sentiment scoring for BUY signals.

News is SUPPLEMENTARY: it can only VETO a BUY when sentiment is strongly negative
with high impact. It never forces a buy and never affects SELL/UPDATE_STOP.
"""

import logging

from live.config import NEWS_VETO_SCORE, NEWS_MAG_WEIGHTS
from live.db import get_recent_news_for_ticker

log = logging.getLogger("trader.news")

_SENT_SIGN = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}


def score_ticker_news(conn, ticker, as_of_date):
    """
    Return (score, has_high_impact_bearish, top_headline, n_rows).
    score is a confidence/magnitude-weighted average sentiment in [-1, +1].
    """
    rows = get_recent_news_for_ticker(conn, ticker, as_of_date)
    if not rows:
        return 0.0, False, None, 0

    num = 0.0
    den = 0.0
    high_impact_bearish = False
    top_headline = None
    best_weight = -1.0

    for r in rows:
        sign = _SENT_SIGN.get((r.get("sentiment") or "").lower(), 0.0)
        try:
            conf = float(r.get("confidence") or 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        mag = NEWS_MAG_WEIGHTS.get((r.get("impact_magnitude") or "medium").lower(), 1.0)
        weight = conf * mag
        num += sign * weight
        den += weight

        if sign < 0 and (r.get("impact_magnitude") or "").lower() == "high":
            high_impact_bearish = True

        # Track the most impactful headline for the report
        if weight > best_weight and r.get("summary"):
            best_weight = weight
            top_headline = r["summary"]

    score = 0.0 if den == 0 else max(-1.0, min(1.0, num / den))
    return round(score, 2), high_impact_bearish, top_headline, len(rows)


def news_veto(score, high_impact_bearish):
    """Veto a BUY only when sentiment is strongly negative AND high-impact bearish."""
    return score <= NEWS_VETO_SCORE and high_impact_bearish


def sentiment_emoji(score):
    if score > 0.05:
        return "🐂"
    if score < -0.05:
        return "🐻"
    return "➖"
