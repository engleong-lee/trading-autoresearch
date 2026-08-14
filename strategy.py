"""
strategy.py — THE ONLY FILE THE AGENT MODIFIES.

This file implements a swing/trend trading strategy for US equities.
The agent experiments by changing indicators, thresholds, entry/exit logic,
position sizing, stop-loss rules, and any other trading logic here.

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
# PARAMETERS — the agent can tune these
# ─────────────────────────────────────────────────────────────

# EMA periods (from the original strategy: 20, 50, 200)
EMA_FAST = 12
EMA_MID = 21
EMA_SLOW = 200

# ATR for stop-loss calculation
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 1.0   # Stop = support - ATR * multiplier

# Swing trading parameters
SWING_MIN_RR = 1.0           # Minimum reward/risk ratio for swing entries
SWING_MAX_HOLD_DAYS = 1     # Max hold days for swing trades
SWING_TRAIL_PROFIT_PCT = 0.03  # Activate trailing stop after this profit %
SWING_TRAIL_ATR_MULT = 0.8    # Trailing stop distance in ATR units

# Trend trading parameters
TREND_MIN_RR = 6.0           # Minimum reward/risk for trend initial entry
TREND_TRAIL_ATR_MULT = 1.5   # Trail stop at N * ATR below recent support

# Entry offset from support (0.3% above support)
ENTRY_OFFSET_PCT = 0.002

# Take-profit offset from resistance (0.3% below resistance)
TP_OFFSET_PCT = 0.005

# Risk per trade
RISK_PCT = 0.005  # 0.5% of capital
MAX_POSITION_SHARES = 600    # Cap shares per trade

# Market regime filter
VIX_HIGH_THRESHOLD = 30      # Above this = cautious (smaller positions)
VIX_LOW_THRESHOLD = 15       # Below this = aggressive (normal/larger positions)

# Capital
INITIAL_CAPITAL = 100_000.0

# Support/Resistance lookback
SR_LOOKBACK = 60             # Days to look back for S/R levels
SR_MIN_TOUCHES = 2           # Minimum touches to qualify as S/R

# Pattern detection
PULLBACK_THRESHOLD_PCT = 0.07  # Price within 7% of support = potential entry

# Candlestick confirmation
CANDLE_CONFIRM_VIX = 25      # Require candle confirmation when VIX above this
CANDLE_BODY_RATIO = 0.35     # Max body/range ratio for hammer/shooting star
CANDLE_WICK_RATIO = 2.0      # Min long_wick/body ratio for hammer/shooting star

# Earnings avoidance
EARNINGS_AVOID_ENTRY_DAYS = 2   # Don't enter within N days of earnings
EARNINGS_EXIT_DAYS = 1          # Exit swing trades N days before earnings

# Confirmation filters
RSI_OVERBOUGHT = 999          # Disabled — RSI filter not helpful with short holds
VOL_RATIO_MIN = 0.3            # Minimum volume ratio for entry
MACD_HIST_ATR_MULT = -1.0      # Skip if MACD hist < this * ATR

# SPY regime gate
REGIME_GATE_ENABLED = True     # Skip entries when SPY in downtrend

# Sector concentration limit
SECTOR_MAP = {
    "NVDA": "XLK", "AAPL": "XLK", "AMD": "XLK", "AVGO": "XLK",
    "CRWD": "XLK", "INTU": "XLK",
    "V": "XLF", "MA": "XLF",
    "AMZN": "XLY",
    "VGK": None,
}
MAX_PER_SECTOR = 2            # Max concurrent positions in same sector

# Stop-loss cooldown
STOP_COOLDOWN_DAYS = 0        # Skip re-entry for N days after stop-out (0=disabled)


# ─────────────────────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────────────────────

def compute_indicators(df):
    """Compute all technical indicators on a single ticker's OHLCV DataFrame."""
    df = df.copy()

    # EMAs
    df["ema_fast"] = df["Close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_mid"] = df["Close"].ewm(span=EMA_MID, adjust=False).mean()
    df["ema_slow"] = df["Close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # ATR
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift(1)).abs()
    low_close = (df["Low"] - df["Close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = true_range.rolling(ATR_PERIOD).mean()

    # RSI (14-period)
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # Bollinger Bands (for mean-reversion detection)
    df["bb_mid"] = df["Close"].rolling(20).mean()
    df["bb_std"] = df["Close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]

    # Higher highs / higher lows detection
    df["swing_high"] = df["High"].rolling(5, center=True).max() == df["High"]
    df["swing_low"] = df["Low"].rolling(5, center=True).min() == df["Low"]

    # Volume moving average
    df["vol_ma"] = df["Volume"].rolling(20).mean()
    df["vol_ratio"] = df["Volume"] / df["vol_ma"].replace(0, np.nan)

    # ADX (14-period) for trend strength
    plus_dm = df["High"].diff()
    minus_dm = -df["Low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = true_range  # reuse from ATR calc above
    atr14 = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr14.replace(0, np.nan))
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr14.replace(0, np.nan))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    df["adx"] = dx.rolling(14).mean()

    return df


def find_support_resistance(df, lookback=None):
    """
    Find support and resistance levels from recent price action.
    Returns (support_levels, resistance_levels) as lists of floats.
    """
    if lookback is None:
        lookback = SR_LOOKBACK

    recent = df.tail(lookback)
    if len(recent) < 10:
        return [], []

    current_price = recent["Close"].iloc[-1]

    # Find swing lows (potential support) — 7-bar window
    supports = []
    for i in range(3, len(recent) - 3):
        if (recent["Low"].iloc[i] <= recent["Low"].iloc[i-1] and
            recent["Low"].iloc[i] <= recent["Low"].iloc[i-2] and
            recent["Low"].iloc[i] <= recent["Low"].iloc[i-3] and
            recent["Low"].iloc[i] <= recent["Low"].iloc[i+1] and
            recent["Low"].iloc[i] <= recent["Low"].iloc[i+2] and
            recent["Low"].iloc[i] <= recent["Low"].iloc[i+3]):
            supports.append(recent["Low"].iloc[i])

    # Find swing highs (potential resistance) — 7-bar window
    resistances = []
    for i in range(3, len(recent) - 3):
        if (recent["High"].iloc[i] >= recent["High"].iloc[i-1] and
            recent["High"].iloc[i] >= recent["High"].iloc[i-2] and
            recent["High"].iloc[i] >= recent["High"].iloc[i-3] and
            recent["High"].iloc[i] >= recent["High"].iloc[i+1] and
            recent["High"].iloc[i] >= recent["High"].iloc[i+2] and
            recent["High"].iloc[i] >= recent["High"].iloc[i+3]):
            resistances.append(recent["High"].iloc[i])

    # Cluster nearby levels (within 1% of each other)
    supports = _cluster_levels(supports, pct=0.01)
    resistances = _cluster_levels(resistances, pct=0.01)

    # Filter: support below current price, resistance above
    supports = [s for s in supports if s < current_price]
    resistances = [r for r in resistances if r > current_price]

    # Sort by proximity to current price
    supports.sort(reverse=True)   # Nearest support first
    resistances.sort()            # Nearest resistance first

    return supports, resistances


def _cluster_levels(levels, pct=0.01, min_touches=1):
    """Cluster nearby price levels, return cluster averages filtered by touch count."""
    if not levels:
        return []
    levels = sorted(levels)
    clusters = [[levels[0]]]
    for level in levels[1:]:
        if (level - clusters[-1][-1]) / clusters[-1][-1] < pct:
            clusters[-1].append(level)
        else:
            clusters.append([level])
    return [np.mean(c) for c in clusters if len(c) >= min_touches]


# ─────────────────────────────────────────────────────────────
# CANDLESTICK PATTERNS
# ─────────────────────────────────────────────────────────────

def detect_hammer(row):
    """Detect hammer pattern (bullish at support). Row is a Series with OHLC."""
    body = abs(row["Close"] - row["Open"])
    lower_wick = min(row["Open"], row["Close"]) - row["Low"]
    upper_wick = row["High"] - max(row["Open"], row["Close"])
    total_range = row["High"] - row["Low"]
    if total_range == 0 or body == 0:
        return False
    return (lower_wick > CANDLE_WICK_RATIO * body and
            upper_wick < body * 0.5 and
            body / total_range < CANDLE_BODY_RATIO)


def detect_engulfing_bullish(prev, curr):
    """Detect bullish engulfing. prev/curr are Series with OHLC."""
    return (prev["Close"] < prev["Open"] and      # prev is bearish
            curr["Close"] > curr["Open"] and        # current is bullish
            curr["Close"] > prev["Open"] and        # current close > prev open
            curr["Open"] < prev["Close"])            # current open < prev close


def detect_strong_bullish(row):
    """Detect strong bullish candle — large body, minimal tails."""
    body = row["Close"] - row["Open"]
    total_range = row["High"] - row["Low"]
    if total_range == 0:
        return False
    return body > 0 and body / total_range > 0.65


def has_bullish_confirmation(hist):
    """Check if the most recent candle shows bullish confirmation."""
    if len(hist) < 2:
        return False
    curr = hist.iloc[-1]
    prev = hist.iloc[-2]
    return (detect_hammer(curr) or
            detect_engulfing_bullish(prev, curr) or
            detect_strong_bullish(curr))


# ─────────────────────────────────────────────────────────────
# EARNINGS DATE AVOIDANCE
# ─────────────────────────────────────────────────────────────

# Approximate earnings months: most companies report in Jan, Apr, Jul, Oct
# Earnings typically fall in weeks 3-4 of these months
EARNINGS_WINDOWS = {
    "NVDA": [(2, 21, 28), (5, 22, 29), (8, 21, 28), (11, 20, 27)],
    "AAPL": [(1, 25, 31), (5, 1, 7), (8, 1, 7), (10, 25, 31)],
    "AMZN": [(2, 1, 7), (4, 25, 30), (8, 1, 7), (10, 25, 31)],
    "V":    [(1, 25, 31), (4, 22, 28), (7, 22, 28), (10, 22, 28)],
    "MA":   [(1, 25, 31), (4, 25, 30), (7, 25, 31), (10, 25, 31)],
    "AMD":  [(1, 28, 31), (4, 28, 30), (7, 28, 31), (10, 28, 31)],
    "AVGO": [(3, 7, 14), (6, 12, 19), (9, 5, 12), (12, 7, 14)],
    "CRWD": [(3, 5, 12), (6, 4, 11), (8, 27, 31), (11, 26, 30)],
    "VGK":  [],  # ETF, no earnings
    "INTU":[(2, 20, 27), (5, 20, 27), (8, 22, 29), (11, 19, 26)],
}


def days_to_earnings(ticker, current_date):
    """
    Approximate days until next earnings for a ticker.
    Returns int days, or None if unknown/ETF.
    """
    windows = EARNINGS_WINDOWS.get(ticker, [])
    if not windows:
        return None
    current_date = pd.Timestamp(current_date)
    min_days = None
    for month, day_start, day_end in windows:
        # Check current year and next year
        for year in [current_date.year, current_date.year + 1]:
            try:
                earn_start = pd.Timestamp(year=year, month=month, day=day_start)
                diff = (earn_start - current_date).days
                if diff >= -2:  # include 2 days after (earnings just happened)
                    if min_days is None or abs(diff) < abs(min_days):
                        min_days = diff
            except ValueError:
                continue
    return min_days


# ─────────────────────────────────────────────────────────────
# TREND DETECTION
# ─────────────────────────────────────────────────────────────

def detect_trend(df):
    """
    Detect market trend for a ticker.
    Returns: 'uptrend', 'downtrend', or 'sideways'
    """
    if len(df) < EMA_SLOW + 10:
        return "sideways"

    latest = df.iloc[-1]

    ema_f = latest["ema_fast"]
    ema_m = latest["ema_mid"]
    ema_s = latest["ema_slow"]

    # Strong uptrend: EMA 20 > 50 > 200, all sloping up
    ema_fast_slope = df["ema_fast"].iloc[-1] - df["ema_fast"].iloc[-5]
    ema_mid_slope = df["ema_mid"].iloc[-1] - df["ema_mid"].iloc[-5]

    if ema_f > ema_m > ema_s and ema_fast_slope > 0 and ema_mid_slope > 0:
        return "uptrend"

    # Downtrend: EMA 20 < 50 < 200
    if ema_f < ema_m < ema_s and ema_fast_slope < 0:
        return "downtrend"

    return "sideways"


def detect_market_regime(data, date):
    """
    Detect overall market regime using SPY/QQQ and VIX up to `date`.
    Returns: 'risk_on', 'risk_off', or 'neutral'
    """
    regime = "neutral"

    if "SPY" in data:
        spy = compute_indicators(data["SPY"].loc[:date])
        if len(spy) > EMA_SLOW:
            spy_trend = detect_trend(spy)
            if spy_trend == "uptrend":
                regime = "risk_on"
            elif spy_trend == "downtrend":
                regime = "risk_off"

    return regime


def get_vix_level(data, date):
    """Get VIX closing level for a given date."""
    if "VIX" not in data:
        return 20.0  # Default neutral
    vix_df = data["VIX"]
    if date in vix_df.index:
        return float(vix_df.loc[date, "Close"])
    # Find nearest prior date
    prior = vix_df.index[vix_df.index <= date]
    if len(prior) > 0:
        return float(vix_df.loc[prior[-1], "Close"])
    return 20.0


# ─────────────────────────────────────────────────────────────
# SIGNAL GENERATION
# ─────────────────────────────────────────────────────────────

WATCHLIST = ["NVDA", "AAPL", "AMZN", "V", "MA", "AMD", "AVGO", "CRWD", "VGK", "INTU"]


def generate_signals(data, start_date, end_date):
    """
    Generate trading signals for the given date range.

    Args:
        data: dict of {ticker: DataFrame} with OHLCV data
        start_date: str, start of evaluation period
        end_date: str, end of evaluation period

    Returns:
        pd.DataFrame with columns: date, ticker, action, price,
                                    stop_loss, take_profit, position_size
    """
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    signals = []

    # Precompute indicators for all tickers
    ticker_data = {}
    for ticker in WATCHLIST:
        if ticker not in data:
            continue
        df = data[ticker].copy()
        df = compute_indicators(df)
        ticker_data[ticker] = df

    # Get all trading days in range
    all_dates = set()
    for ticker, df in ticker_data.items():
        mask = (df.index >= start) & (df.index <= end)
        all_dates.update(df.index[mask].tolist())
    trading_days = sorted(all_dates)

    # Track open positions for signal generation
    open_positions = {}  # ticker -> {entry_date, entry_price, stop_loss, strategy_type}
    stop_cooldowns = {}  # ticker -> date when cooldown expires
    capital = INITIAL_CAPITAL

    for day in trading_days:
        day_ts = pd.Timestamp(day)
        vix = get_vix_level(data, day_ts)
        market_regime = detect_market_regime(data, day_ts)

        for ticker in WATCHLIST:
            if ticker not in ticker_data:
                continue

            df = ticker_data[ticker]
            if day_ts not in df.index:
                continue

            # Get data up to current day (no look-ahead)
            hist = df.loc[:day_ts]
            if len(hist) < EMA_SLOW + 10:
                continue

            current = hist.iloc[-1]
            price = current["Close"]
            atr = current["atr"]

            if pd.isna(atr) or atr <= 0:
                continue

            # ── MANAGE EXISTING POSITIONS ──
            if ticker in open_positions:
                pos = open_positions[ticker]
                days_held = (day_ts - pos["entry_date"]).days

                # Earnings avoidance: exit swing trades before earnings
                dte = days_to_earnings(ticker, day_ts)
                if dte is not None and 0 < dte <= EARNINGS_EXIT_DAYS:
                    if pos["strategy_type"] == "swing":
                        signals.append({
                            "date": day_ts, "ticker": ticker, "action": "SELL",
                            "price": price, "stop_loss": None,
                            "take_profit": None, "position_size": 0,
                        })
                        del open_positions[ticker]
                        continue

                # Swing trade: exit if close below mid EMA
                if pos["strategy_type"] == "swing" and days_held >= 1:
                    ema_val = current.get("ema_mid", price)
                    if pd.notna(ema_val) and price < ema_val:
                        signals.append({
                            "date": day_ts, "ticker": ticker, "action": "SELL",
                            "price": price, "stop_loss": None,
                            "take_profit": None, "position_size": 0,
                        })
                        if price < pos["entry_price"]:
                            stop_cooldowns[ticker] = day_ts + pd.Timedelta(days=STOP_COOLDOWN_DAYS)
                        del open_positions[ticker]
                        continue

                # Swing trade: trailing stop when profitable
                if pos["strategy_type"] == "swing":
                    if price > pos["entry_price"] * (1 + SWING_TRAIL_PROFIT_PCT):
                        new_stop = max(pos["stop_loss"], price - SWING_TRAIL_ATR_MULT * atr)
                        if new_stop > pos["stop_loss"]:
                            pos["stop_loss"] = new_stop
                            signals.append({
                                "date": day_ts, "ticker": ticker, "action": "UPDATE_STOP",
                                "price": price, "stop_loss": new_stop,
                                "take_profit": None, "position_size": 0,
                            })

                # Swing trade: check max hold
                if pos["strategy_type"] == "swing" and days_held >= SWING_MAX_HOLD_DAYS:
                    signals.append({
                        "date": day_ts, "ticker": ticker, "action": "SELL",
                        "price": price, "stop_loss": None,
                        "take_profit": None, "position_size": 0,
                    })
                    del open_positions[ticker]
                    continue

                # Trend trade: trail stop loss
                if pos["strategy_type"] == "trend":
                    supports, _ = find_support_resistance(hist)
                    if supports:
                        new_stop = supports[0] - atr * TREND_TRAIL_ATR_MULT
                        if new_stop > pos["stop_loss"]:
                            signals.append({
                                "date": day_ts, "ticker": ticker,
                                "action": "UPDATE_STOP",
                                "price": price, "stop_loss": new_stop,
                                "take_profit": None, "position_size": 0,
                            })
                            open_positions[ticker]["stop_loss"] = new_stop

                    # Trend exit: close below EMA 50
                    if price < current["ema_mid"]:
                        signals.append({
                            "date": day_ts, "ticker": ticker, "action": "SELL",
                            "price": price, "stop_loss": None,
                            "take_profit": None, "position_size": 0,
                        })
                        del open_positions[ticker]

                continue  # Don't generate new entry for ticker already held

            # ── FILTER: skip if already at max positions ──
            if len(open_positions) >= 10:
                continue

            # ── FILTER: SPY regime gate ──
            if REGIME_GATE_ENABLED and market_regime == "risk_off":
                continue

            # ── FILTER: sector concentration limit ──
            ticker_sector = SECTOR_MAP.get(ticker)
            if ticker_sector is not None:
                sector_count = sum(
                    1 for t in open_positions
                    if SECTOR_MAP.get(t) == ticker_sector
                )
                if sector_count >= MAX_PER_SECTOR:
                    continue

            # ── FILTER: stop-loss cooldown ──
            if ticker in stop_cooldowns and day_ts < stop_cooldowns[ticker]:
                continue

            # ── FILTER: skip downtrends ──
            trend = detect_trend(hist)
            if trend == "downtrend":
                continue

            # ── FILTER: skip very weak trend (ADX < 15) ──
            adx_val = current.get("adx", 25)
            if pd.notna(adx_val) and adx_val < 15:
                continue

            # ── FILTER: skip if VIX too high ──
            if vix > VIX_HIGH_THRESHOLD:
                continue

            # ── FILTER: skip if too close to earnings ──
            dte = days_to_earnings(ticker, day_ts)
            if dte is not None and 0 < dte <= EARNINGS_AVOID_ENTRY_DAYS:
                continue

            # ── FIND SUPPORT/RESISTANCE ──
            supports, resistances = find_support_resistance(hist)
            if not supports or not resistances:
                continue

            entry_found = False

            # ── ENTRY PATH 1: Price near support (pullback entry) ──
            nearest_support = supports[0]
            nearest_resistance = resistances[0]

            distance_to_support = (price - nearest_support) / nearest_support

            if 0 < distance_to_support < PULLBACK_THRESHOLD_PCT:
                entry_price = nearest_support * (1 + ENTRY_OFFSET_PCT)
                stop_loss = nearest_support - atr * ATR_STOP_MULTIPLIER
                take_profit = nearest_resistance * (1 - TP_OFFSET_PCT)

                risk = entry_price - stop_loss
                reward = take_profit - entry_price

                if risk > 0:
                    rr_ratio = reward / risk

                    if trend == "uptrend" and rr_ratio >= TREND_MIN_RR:
                        strategy_type = "trend"
                        take_profit_signal = None
                        entry_found = True
                    elif rr_ratio >= SWING_MIN_RR:
                        strategy_type = "swing"
                        take_profit_signal = take_profit
                        entry_found = True

            if not entry_found:
                continue

            # Position sizing: risk-adjust based on VIX
            effective_risk = RISK_PCT * 1.3 if vix < VIX_LOW_THRESHOLD else RISK_PCT
            risk_amount = capital * effective_risk
            position_size = int(risk_amount / risk)
            if position_size <= 0:
                continue
            position_size = min(position_size, MAX_POSITION_SHARES)

            # Confirmation filters
            # RSI not overbought
            if current["rsi"] > RSI_OVERBOUGHT:
                continue

            # MACD histogram not deeply negative
            if current["macd_hist"] < MACD_HIST_ATR_MULT * atr:
                continue

            # Volume confirmation (above average)
            if current["vol_ratio"] < VOL_RATIO_MIN:
                continue

            # Candlestick confirmation required in high-VIX regime
            if vix > CANDLE_CONFIRM_VIX:
                if not has_bullish_confirmation(hist):
                    continue

            signals.append({
                "date": day_ts,
                "ticker": ticker,
                "action": "BUY",
                "price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit_signal,
                "position_size": position_size,
            })

            open_positions[ticker] = {
                "entry_date": day_ts,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "strategy_type": strategy_type,
            }

    return pd.DataFrame(signals) if signals else pd.DataFrame(
        columns=["date", "ticker", "action", "price", "stop_loss", "take_profit", "position_size"]
    )
