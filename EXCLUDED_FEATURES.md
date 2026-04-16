# Excluded Features — Future Enhancement Roadmap

This document catalogues features from the original `swing_and_trend_strategy.md` that were **excluded** from the autoresearch baseline. Features that have since been implemented are marked with their round.

---

## IMPLEMENTED FEATURES

### ~~3. Candlestick Pattern Confirmation~~ — IMPLEMENTED (R1)

Implemented in `strategy.py`:
- `detect_hammer()`, `detect_engulfing_bullish()`, `detect_strong_bullish()`
- `has_bullish_confirmation()` — checks latest candle for any bullish pattern
- VIX-gated: requires candle confirmation when `VIX > CANDLE_CONFIRM_VIX` (25)
- Tunable params: `CANDLE_BODY_RATIO`, `CANDLE_WICK_RATIO`, `CANDLE_CONFIRM_VIX`

### ~~4. Earnings Date Avoidance~~ — IMPLEMENTED (R1)

Implemented in `strategy.py`:
- `EARNINGS_WINDOWS` — approximate quarterly earnings dates per ticker
- `days_to_earnings()` — compute days until next earnings
- Entry filter: skip if within `EARNINGS_AVOID_ENTRY_DAYS` (2) of earnings
- Exit rule: force-exit swing trades `EARNINGS_EXIT_DAYS` (1) before earnings

### ~~5. Sector Rotation Quadrant Analysis~~ — IMPLEMENTED (R4)

Implemented in `strategy.py` + `prepare.py`:
- Sector ETFs (XLK, XLF, XLY) downloaded in `prepare.py`
- `SECTOR_MAP` — ticker-to-sector-ETF mapping
- `get_sector_quadrant()` — RS = sector/SPY, classify Leading/Improving/Weakening/Lagging
- Position size boost for leading sectors (`SECTOR_LEADING_BOOST`, optimized to 1.3x)
- Tunable params: `SECTOR_RS_LOOKBACK` (30), `SECTOR_RS_THRESHOLD`, `SECTOR_LEADING_BOOST`

### ~~7. Partial Profit Taking~~ — IMPLEMENTED (R4)

Implemented in `strategy.py` + `prepare.py`:
- Backtest engine supports partial exits (SELL with `position_size` < total shares)
- Swing trades: sell `PARTIAL_EXIT_PCT` (70%) at intermediate target
- Intermediate target = entry + `PARTIAL_TARGET_PCT` (60%) × (TP - entry)
- After partial exit, stop moves to breakeven (entry price)
- Tunable params: `PARTIAL_EXIT_PCT`, `PARTIAL_TARGET_PCT`

### ~~8. Position Sizing Grid (Macro-Adjusted)~~ — IMPLEMENTED (R4/R5)

Implemented as compound multiplier in `strategy.py`:
- VIX adjustment: 1.3x when VIX < 15, 1.0x otherwise
- Sector strength: `SECTOR_LEADING_BOOST` (1.3x) for leading sectors
- Momentum ranking: `MOMENTUM_TOP_BOOST` (1.3x) for top-third momentum tickers
- ATR volatility regime: `ATR_LOW_SIZE_MULT` (1.1x) in low-vol, `ATR_HIGH_SIZE_MULT` (1.0x, disabled) in high-vol
- Final size = base_risk / risk_per_share × sector × momentum × atr_vol

### ~~6. Shorting / Short-Side Strategies~~ — PARTIALLY IMPLEMENTED (R4)

Backtest engine in `prepare.py` supports SHORT/COVER:
- SHORT action with reversed P&L: (entry - exit) × shares
- COVER action (full or partial)
- 50% margin requirement, 0.5% annualized borrow cost
- Stop-loss for shorts triggers on HIGH >= stop (reversed)
- **Not yet used:** No short signal generation in `strategy.py` — engine is ready but no strategy-side short entry logic exists

---

## STILL EXCLUDED

### 1. News & Macro Analysis Integration

The original strategy ingests four news/macro feeds (via n8n workflow nodes):
- Macro Economic Context, Macro Indicators/News, Industry News, Ticker News
- Rules: trade policy impact, currency effects, inflation/rate environment, Fed policy, news impact scoring, macro emergency exits

**Why still excluded:** Requires historical news sentiment dataset (RavenPack, Quandl, or LLM-scored archive). No free historical source available for backtesting. VIX is used as a partial macro proxy.

**To add:** Pre-compute daily sentiment scores per ticker from a news API, store as parquet, use as entry filter or size multiplier.

---

### 2. Chart Image Reading & Visual Analysis

The original strategy reads chart images (from chart-img.com) for OHLC, EMA lines, S/R levels, candle patterns.

**Why still excluded:** Not applicable to autoresearch — all chart information is computed numerically from OHLCV data. This is specific to the live n8n/LLM workflow.

---

### 9. Margin Management Rules

From the original strategy Section VIII:
- Initial/maintenance margin tracking, 50% excess liquidity rule
- Max 70% margin usage, reduced to 50% in high VIX
- CFD considerations, NLV monitoring

**Why still excluded:** Backtest uses simplified cash-based model. Margin management is a live trading concern with minimal impact on strategy optimization. The SHORT/COVER engine does track 50% margin requirement.

---

### 10. Swing vs Trend Strategy Selection Logic

**Partially implemented.** Current logic:
- Uptrend + R/R >= `TREND_MIN_RR` (6.0, effectively disabled) → trend trade
- R/R >= `SWING_MIN_RR` (1.0) → swing trade

**Still missing from original:**
- VIX-based preference (VIX > 20 → prefer swing over trend)
- "Never trend trade sideways" enforcement (currently just checks uptrend)
- Three-condition trend reversal detection (trendline break + higher low + higher high)
- Separate performance tracking per strategy type

**Low priority:** With `SWING_MAX_HOLD_DAYS=1` and `TREND_MIN_RR=6.0`, the strategy is effectively all-swing. Trend logic exists but rarely triggers.

---

## Summary

| # | Feature | Status | Round |
|---|---------|--------|-------|
| 1 | News & Macro | Excluded | — |
| 2 | Chart Image Reading | Excluded (N/A) | — |
| 3 | Candlestick Confirmation | **Implemented** | R1 |
| 4 | Earnings Avoidance | **Implemented** | R1 |
| 5 | Sector Rotation | **Implemented** | R4 |
| 6 | Shorting | **Engine only** | R4 |
| 7 | Partial Profit Taking | **Implemented** | R4 |
| 8 | Position Sizing Grid | **Implemented** | R4/R5 |
| 9 | Margin Management | Excluded | — |
| 10 | Swing vs Trend Selection | Partial | R1 baseline |
