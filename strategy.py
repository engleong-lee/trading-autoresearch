"""
strategy.py — THE ONLY FILE THE AGENT MODIFIES.

Next-day momentum/breakout strategy.
Signals are generated after the close on day t and executed at the open of day t+1.

INTERFACE CONTRACT (do not break this):
  - Must define: generate_signals(data, start_date, end_date) -> pd.DataFrame
  - The returned DataFrame must have columns:
      date, ticker, action, price, stop_loss, take_profit, position_size
  - action must be one of: 'BUY', 'SELL', 'UPDATE_STOP'
  - data is a dict of {ticker: DataFrame} with OHLCV columns + benchmarks
  - All indicators must be computed HERE, not imported from prepare.py

WATCHLIST: NVDA, AAPL, AMZN, V, MA, AMD, AVGO, CRWD, VGK, INTU
"""

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────
# PARAMETERS
# ─────────────────────────────────────────────────────────────

EMA_FAST = 10
EMA_MID = 21
EMA_SLOW = 200

ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.0

DONCHIAN_LOOKBACK = 20

SWING_MIN_RR = 2.0
SWING_MAX_HOLD_DAYS = 14
SWING_TRAIL_PROFIT_PCT = 0.05
SWING_TRAIL_ATR_MULT = 2.0

RISK_PCT = 0.01
MAX_POSITION_SHARES = 600

VOL_RATIO_MIN = 1.0
RSI_OVERBOUGHT = 70
ADX_MIN = 20

VIX_HIGH_THRESHOLD = 30
VIX_LOW_THRESHOLD = 15

REGIME_GATE_ENABLED = True

INITIAL_CAPITAL = 100_000.0

MAX_PER_SECTOR = 3
MAX_NEW_POSITIONS_PER_DAY = 3
STOP_COOLDOWN_DAYS = 0

SECTOR_MAP = {
    "NVDA": "XLC",
    "AAPL": "XLC",
    "AMD": "XLC",
    "AVGO": "XLC",
    "CRWD": "XLC",
    "V": "XLI",
    "MA": "XLI",
    "AMZN": "XLY",
    "VGK": None,
    "INTU": "XLC",
}

WATCHLIST = ["NVDA", "AAPL", "AMZN", "V", "MA", "AMD", "AVGO", "CRWD", "VGK", "INTU"]


# ─────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────

def compute_indicators(df):
    df = df.copy()

    # EMAs
    df["ema_fast"] = df["Close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_mid"] = df["Close"].ewm(span=EMA_MID, adjust=False).mean()
    df["ema_slow"] = df["Close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # ATR
    prev_close = df["Close"].shift(1)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = true_range.rolling(window=ATR_PERIOD).mean()

    # Volume
    df["vol_ma"] = df["Volume"].rolling(window=20).mean()
    df["vol_ratio"] = df["Volume"] / df["vol_ma"].replace(0, np.nan)

    # RSI
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ADX (simplified)
    plus_dm = df["High"].diff()
    minus_dm = -df["Low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr = df["atr"].replace(0, np.nan)
    plus_di = 100 * (plus_dm.rolling(window=14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=14).mean() / atr)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
    df["adx"] = dx.rolling(window=14).mean()

    # Donchian channel (previous N days)
    df["donchian_high"] = df["High"].shift(1).rolling(window=DONCHIAN_LOOKBACK).max()
    df["donchian_low"] = df["Low"].shift(1).rolling(window=DONCHIAN_LOOKBACK).min()

    # Next day for execution
    df["next_open"] = df["Open"].shift(-1)
    df["next_date"] = df.index.to_series().shift(-1)

    return df


def detect_trend(df):
    if len(df) < EMA_SLOW + 10:
        return "neutral"
    cur = df.iloc[-1]
    prev = df.iloc[-2]
    if (
        cur["Close"] > cur["ema_slow"]
        and cur["ema_fast"] > cur["ema_mid"] > cur["ema_slow"]
        and cur["ema_fast"] > prev["ema_fast"]
        and cur["ema_mid"] > prev["ema_mid"]
    ):
        return "uptrend"
    if (
        cur["Close"] < cur["ema_slow"]
        and cur["ema_fast"] < cur["ema_mid"] < cur["ema_slow"]
        and cur["ema_fast"] < prev["ema_fast"]
        and cur["ema_mid"] < prev["ema_mid"]
    ):
        return "downtrend"
    return "neutral"


def detect_market_regime(data, date):
    regime = "neutral"
    if "SPY" in data:
        spy = compute_indicators(data["SPY"].loc[:date])
        if len(spy) > EMA_SLOW:
            trend = detect_trend(spy)
            if trend == "uptrend":
                regime = "risk_on"
            elif trend == "downtrend":
                regime = "risk_off"
    return regime


def get_vix_level(data, date):
    if "VIX" not in data:
        return 20.0
    vix_df = data["VIX"]
    if date in vix_df.index:
        return float(vix_df.loc[date, "Close"])
    prior = vix_df.index[vix_df.index <= date]
    if len(prior) > 0:
        return float(vix_df.loc[prior[-1], "Close"])
    return 20.0


# ─────────────────────────────────────────────────────────────
# SIGNAL GENERATION
# ─────────────────────────────────────────────────────────────

def generate_signals(data, start_date, end_date):
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    signals = []

    ticker_data = {}
    for ticker in WATCHLIST:
        if ticker not in data:
            continue
        df = data[ticker].copy()
        df = compute_indicators(df)
        ticker_data[ticker] = df

    if not ticker_data:
        return pd.DataFrame(columns=["date", "ticker", "action", "price", "stop_loss", "take_profit", "position_size"])

    trading_days = pd.date_range(start, end, freq="B")

    for day in trading_days:
        day_ts = pd.Timestamp(day)
        vix = get_vix_level(data, day_ts)
        market_regime = detect_market_regime(data, day_ts)

        if REGIME_GATE_ENABLED and market_regime == "risk_off":
            continue
        if vix > VIX_HIGH_THRESHOLD:
            continue

        daily_new = 0
        sector_count = {}

        for ticker in WATCHLIST:
            if ticker not in ticker_data:
                continue
            df = ticker_data[ticker]
            if day_ts not in df.index:
                continue

            cur = df.loc[day_ts]
            if (
                pd.isna(cur["next_open"])
                or pd.isna(cur["donchian_high"])
                or pd.isna(cur["atr"])
            ):
                continue

            # Momentum breakout filters
            if cur["Close"] <= cur["donchian_high"]:
                continue
            if cur["Close"] <= cur["ema_fast"]:
                continue
            if cur["ema_fast"] <= cur["ema_mid"]:
                continue
            if cur["vol_ratio"] < VOL_RATIO_MIN:
                continue
            if cur["rsi"] > RSI_OVERBOUGHT:
                continue
            if cur["adx"] < ADX_MIN:
                continue

            entry_price = float(cur["next_open"])
            # Stop at breakout low or ATR-based stop, whichever is closer
            stop_loss = max(
                cur["donchian_low"] * 0.99,
                entry_price - ATR_STOP_MULTIPLIER * cur["atr"],
            )
            if not np.isfinite(stop_loss) or stop_loss >= entry_price:
                continue

            risk = entry_price - stop_loss
            reward = risk * SWING_MIN_RR
            take_profit = entry_price + reward

            next_date = cur["next_date"]
            if pd.isna(next_date):
                continue

            # Daily entry limits
            if daily_new >= MAX_NEW_POSITIONS_PER_DAY:
                continue
            sector = SECTOR_MAP.get(ticker)
            if sector is not None and sector_count.get(sector, 0) >= MAX_PER_SECTOR:
                continue

            shares = int(INITIAL_CAPITAL * RISK_PCT / risk)
            if shares <= 0:
                continue
            shares = min(shares, MAX_POSITION_SHARES)

            signals.append({
                "date": next_date,
                "ticker": ticker,
                "action": "BUY",
                "price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "position_size": shares,
            })

            daily_new += 1
            if sector is not None:
                sector_count[sector] = sector_count.get(sector, 0) + 1

    return pd.DataFrame(signals)
