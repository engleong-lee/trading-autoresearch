# Excluded Features — Future Enhancement Roadmap

This document catalogues every feature from the original `swing_and_trend_strategy.md` that was **excluded** from the autoresearch baseline `strategy.py`. Each section explains what was excluded, why, and provides implementation guidance for adding it back.

---

## 1. News & Macro Analysis Integration

### What Was Excluded

The original strategy ingests four distinct news/macro feeds (via n8n workflow nodes) and uses them to adjust every trading decision:

- **Macro Economic Context** (`Get Macro Analysis`) — broad market regime assessment
- **Macro Indicators and News** (`Get Macro News`) — specific macro data (CPI, Fed, yield curve)
- **Industry News** (`Get Industry News`) — sector-level developments
- **Ticker News** (`Get Ticker News`) — company-specific news events

### Original Rules That Depend on News

1. **Trade Policy Impact:** Factor in tariff effects on specific companies/sectors. The strategy explicitly checks tariff exposure as `[None/Low/Medium/High]` and considers exiting or reducing positions for directly impacted companies.

2. **Currency Effects:** USD strength impact on multinational revenues. The strategy assesses currency impact as `[Positive/Negative/Neutral]` per ticker.

3. **Inflation/Rate Environment:** Adjusts strategy style — favor value/commodities in high inflation, favor growth in low inflation.

4. **Liquidity Conditions:** Uses money market fund flows as a market liquidity indicator.

5. **Fed Policy Implications:** Assessed as `[Favorable/Unfavorable/Neutral]` — affects whether to enter new positions or tighten existing ones.

6. **News Impact Assessment (Section VII of original):**
   - Company-specific news relevance scoring
   - Impact on technical setup: `[Supports/Contradicts/Neutral]`
   - Action required: `[None/Adjust/Exit]`
   - Industry/sector news momentum impact
   - Net effect on trade decision with confidence adjustment

7. **Macro-Based Emergency Exits:** Exit positions if:
   - Sudden tariff announcement affecting the company
   - Fed policy surprise
   - Geopolitical event

### Why Excluded

News data requires real-time or near-real-time API calls (news APIs, economic calendars). The autoresearch loop runs backtests on historical data, and integrating historical news sentiment would require a historical news sentiment dataset (e.g., from RavenPack, Quandl, or a custom LLM-scored news archive). This adds significant complexity and cost.

### How to Add It Back

**Option A — Sentiment Score as a Feature (Recommended for autoresearch):**
1. Pre-compute a daily sentiment score per ticker using a historical news API (e.g., Alpha Vantage News Sentiment, Finnhub News, or Polygon.io news)
2. Store as a parquet file: `{ticker}_sentiment.parquet` with columns `[date, sentiment_score, news_count]`
3. Load in `strategy.py` alongside OHLCV data
4. Use as a filter: only enter if `sentiment_score > threshold` or scale position size by sentiment
5. The agent can then optimize the sentiment threshold and weighting

**Option B — VIX as Macro Proxy (Partially Implemented):**
The baseline already uses VIX as a simple market regime filter. This could be expanded:
- Download sector ETFs (XLK, XLF, XLY, etc.) and compute relative strength
- Use yield curve data (10Y-2Y spread) from FRED API
- Use DXY (dollar index) as currency strength proxy

**Option C — Live Trading Integration (Not for autoresearch):**
For live trading (not autoresearch), keep the original n8n workflow nodes that feed news into the analysis prompt. This is complementary to autoresearch — autoresearch optimizes the technical rules, and the news overlay is applied on top during live execution.

---

## 2. Chart Image Reading & Visual Analysis

### What Was Excluded

The original strategy is designed around an LLM reading actual chart images (from chart-img.com). The entire `CHART READING REQUIREMENTS` section (lines 44-89) is a detailed protocol for:

1. **Price Information Extraction:** Reading OHLC from the top-left of chart, price change percentages
2. **Moving Average Visual Reading:** Matching colored lines to EMA 20/50/200 values from the legend
3. **Support/Resistance from Drawn Lines:** Reading horizontal lines that are pre-drawn on the chart
4. **ATR Visual Reading:** Finding the ATR 14 value from the bottom indicator panel
5. **Time Scale Interpretation:** Reading date range and identifying the most recent candle
6. **Candle Pattern Visual Recognition:** Identifying candle colors, wick lengths, body sizes visually

The original strategy has a hard rule: `"USE ONLY PRICES VISIBLE ON THE PROVIDED CHART"` and `"If you cannot clearly determine a price level from the chart, you must state 'Cannot determine from chart'"`.

### Why Excluded

Autoresearch works with numerical data (OHLCV in DataFrames), not chart images. The visual chart reading was designed for an LLM-based analysis workflow (your n8n pipeline) where a multimodal model reads a chart screenshot. In the autoresearch context, we compute the same information programmatically from the raw data — EMAs, ATR, support/resistance levels are all calculated numerically in `strategy.py`.

### How to Add It Back

This feature is **not applicable to autoresearch** — it's specific to the live analysis workflow. However, if you want chart-based confirmation in a hybrid system:

1. Generate chart images programmatically (using `mplfinance` or `plotly`) from the backtest data
2. Send them to a multimodal LLM (Claude/GPT-4V) for pattern confirmation
3. Use the LLM's assessment as an additional signal filter

This would be expensive and slow for autoresearch (hundreds of LLM calls per experiment), so it's better suited as a final overlay in live trading.

---

## 3. Candlestick Pattern Confirmation

### What Was Excluded

The original strategy defines candlestick patterns in **Section VII — Advanced Candlestick Patterns** (lines 428-449):

**Single Candle Patterns:**
- **Hammer** (bullish at support): Small body, long lower tail — used as entry confirmation at support
- **Shooting Star** (bearish at resistance): Small body, long upper tail — used as exit/short signal
- **Strong Bullish/Bearish Candle**: Large body, minimal tails — breakout confirmation

**Double Candle Patterns:**
- **Inside Day**: Second candle's range within first candle's range (less potent signal)
- **Outside Day**: Second candle engulfs first candle (stronger signal)
- **Railway Tracks**: Two equal-length opposite candles
- **Dark Cloud Cover/Piercing Pattern**: Partial reversal patterns

**Key Rules from Original:**
- Patterns are only valid at support/resistance levels
- Small candle color doesn't matter; large candle color is crucial
- In **unstable/volatile markets** (large candles, increasing ATR), the strategy requires waiting for a bullish confirmation candle before entry:
  - Hammer at support
  - Strong green candle closing above support
  - Bullish engulfing pattern
  - Only ONE confirmation candle needed
- **VIX overlay:** If VIX > 20, always wait for confirmation; if VIX < 15, can use pending orders directly

### What's Partially Implemented

The baseline `strategy.py` has basic RSI/MACD/volume filters but does NOT detect candlestick patterns. It also does not implement the "wait for confirmation in volatile markets" rule.

### How to Add It Back

Add candlestick detection functions to `strategy.py`:

```python
def detect_hammer(df, idx):
    """Detect hammer pattern at index. Returns True if hammer found."""
    row = df.iloc[idx]
    body = abs(row["Close"] - row["Open"])
    lower_wick = min(row["Open"], row["Close"]) - row["Low"]
    upper_wick = row["High"] - max(row["Open"], row["Close"])
    total_range = row["High"] - row["Low"]
    if total_range == 0:
        return False
    return (lower_wick > 2 * body and
            upper_wick < body * 0.5 and
            body / total_range < 0.35)

def detect_engulfing_bullish(df, idx):
    """Detect bullish engulfing at index."""
    if idx < 1:
        return False
    prev = df.iloc[idx - 1]
    curr = df.iloc[idx]
    return (prev["Close"] < prev["Open"] and     # prev is bearish
            curr["Close"] > curr["Open"] and       # current is bullish
            curr["Close"] > prev["Open"] and       # current close > prev open
            curr["Open"] < prev["Close"])           # current open < prev close

def detect_shooting_star(df, idx):
    """Detect shooting star pattern at index."""
    row = df.iloc[idx]
    body = abs(row["Close"] - row["Open"])
    lower_wick = min(row["Open"], row["Close"]) - row["Low"]
    upper_wick = row["High"] - max(row["Open"], row["Close"])
    total_range = row["High"] - row["Low"]
    if total_range == 0:
        return False
    return (upper_wick > 2 * body and
            lower_wick < body * 0.5 and
            body / total_range < 0.35)
```

Then integrate into the entry logic:
```python
# In generate_signals, before generating a BUY signal:
if vix > 20 or market_is_volatile:
    # Require candlestick confirmation
    if not (detect_hammer(hist, -1) or detect_engulfing_bullish(hist, -1)):
        continue  # Skip entry, wait for confirmation
```

The autoresearch agent could then optimize:
- The body/wick ratios that define each pattern
- Which patterns to require in which conditions
- The VIX threshold for requiring confirmation

---

## 4. Earnings Date Avoidance

### What Was Excluded

The original strategy has a **non-negotiable rule** (mentioned in multiple sections):

- **"NO holding through earnings"** — exit 1-2 days before earnings
- **Swing trades:** Must exit 1-2 days before earnings (hard rule, lines 120, 257)
- **Trend trades:** Can hold through earnings IF ALL positions have 5%+ profit buffer; otherwise exit all (line 343)
- **Earnings date is checked** as part of the Chart Overview output: "Earnings Date: (from provided data) (critical - specify days until earnings)"
- **Time Stop:** "Exit swing trades 2 days before earnings" (line 650)

### Why Excluded

Requires an earnings calendar data source. `yfinance` has `.earnings_dates` but it's unreliable for historical dates. A proper implementation needs a dedicated earnings calendar API.

### How to Add It Back

**Option A — yfinance earnings dates (free, imperfect):**
```python
import yfinance as yf

def get_earnings_dates(ticker):
    """Fetch upcoming/historical earnings dates."""
    stock = yf.Ticker(ticker)
    try:
        dates = stock.earnings_dates
        if dates is not None:
            return dates.index.tolist()
    except:
        pass
    return []
```

**Option B — Pre-downloaded earnings calendar (recommended for autoresearch):**
1. Download historical earnings dates from Alpha Vantage, Polygon.io, or scrape from earnings whispers
2. Store as `earnings_calendar.parquet` with columns `[ticker, earnings_date]`
3. In `strategy.py`, check proximity to earnings before entry:

```python
def days_to_earnings(ticker, current_date, earnings_data):
    """Return days until next earnings, or None if unknown."""
    future = earnings_data[
        (earnings_data["ticker"] == ticker) &
        (earnings_data["earnings_date"] > current_date)
    ]
    if len(future) == 0:
        return None
    return (future["earnings_date"].min() - current_date).days

# In signal generation:
dte = days_to_earnings(ticker, day_ts, earnings_data)
if dte is not None and dte <= 2 and pos["strategy_type"] == "swing":
    # Force exit swing trade
    signals.append({"action": "SELL", ...})
if dte is not None and dte <= 5:
    # Don't enter new swing positions within 5 days of earnings
    continue
```

**Option C — Quarterly approximation (rough but free):**
If no API is available, approximate earnings as occurring in the last two weeks of January, April, July, October for most companies. This is imprecise but better than nothing.

### Impact on `prepare.py`

If adding earnings data, you would:
1. Add a `download_earnings()` function to `prepare.py` (or a separate `download_earnings.py` script)
2. Load the earnings data in `load_all_data()` and pass it to `generate_signals()`
3. This changes the interface — `generate_signals` would need an extra parameter, OR the earnings data could be embedded in the `data` dict under a special key like `data["_earnings"]`

---

## 5. Sector Rotation Quadrant Analysis

### What Was Excluded

The original strategy uses a **sector rotation framework** with four quadrants:

- **Leading** — strong sector, outperforming market → position size multiplier 1.25x
- **Improving** — sector gaining momentum → multiplier 1.0x
- **Weakening** — sector losing momentum → multiplier 0.75x
- **Lagging** — weak sector, underperforming → multiplier 0.5x (or avoid entirely)

This is referenced throughout the strategy:

1. **Entry filter:** "Verify stock's sector is not in 'Lagging' quadrant" (line 202)
2. **Trend confirmation:** "Sector in Leading/Improving quadrant" required for trend trades (line 288)
3. **Pyramiding:** "Only pyramid if broad market indices (SPY/QQQ) also in confirmed uptrends" (line 309)
4. **Exit signal:** "Sector enters 'Lagging' quadrant" triggers Phase 3 full exit (line 340)
5. **Position sizing grid:** Sector strength directly multiplies the base risk percentage (lines 636-646)
6. **Macro stop:** "Exit if sector enters 'Lagging' quadrant" (line 649)
7. **Shorting confirmation:** "Best shorting environment when VIX > 20, sectors rotating to 'Lagging' quadrant" (line 403)

### Sector-to-Ticker Mapping (for your watchlist)

| Ticker | Sector | Sector ETF |
|--------|--------|------------|
| NVDA | Technology/Semiconductors | XLK / SMH |
| AAPL | Technology | XLK |
| AMZN | Consumer Discretionary/Tech | XLY / XLK |
| V | Financials | XLF |
| MA | Financials | XLF |
| AMD | Technology/Semiconductors | XLK / SMH |
| AVGO | Technology/Semiconductors | XLK / SMH |
| CRWD | Technology/Cybersecurity | XLK / CIBR |
| VGK | International (Europe ETF) | N/A (it IS a sector ETF) |
| INTU | Technology | XLK |

### Why Excluded

Sector rotation analysis requires:
- Relative strength calculations of sector ETFs vs SPY
- A rolling window framework to classify into quadrants
- Downloading sector ETF data (XLK, XLF, XLY, etc.)

This is implementable but adds complexity to the baseline. The autoresearch agent could discover similar effects through momentum scoring.

### How to Add It Back

**Step 1 — Download sector ETF data in `prepare.py`:**
```python
SECTOR_ETFS = ["XLK", "XLF", "XLY", "XLC", "XLI", "XLE", "XLU", "XLP", "XLV", "XLRE", "XLB"]

# Add to download_data():
for etf in SECTOR_ETFS:
    df = yf.download(etf, start=warmup_start, end=download_end, progress=False)
    df.to_parquet(DATA_DIR / f"{etf}.parquet")
```

**Step 2 — Compute sector rotation in `strategy.py`:**
```python
TICKER_SECTOR_MAP = {
    "NVDA": "XLK", "AAPL": "XLK", "AMZN": "XLY", "V": "XLF", "MA": "XLF",
    "AMD": "XLK", "AVGO": "XLK", "CRWD": "XLK", "VGK": None, "INTU": "XLK",
}

def compute_sector_quadrant(sector_etf_df, spy_df, lookback=63):
    """
    Classify sector into quadrant based on relative strength vs SPY.
    Uses a 63-day (3-month) rolling window:
    - RS = sector return / SPY return over the window
    - RS momentum = current RS - RS from 21 days ago
    Quadrants:
    - Leading:   RS > 1.0 and RS momentum > 0
    - Improving:  RS < 1.0 and RS momentum > 0
    - Weakening: RS > 1.0 and RS momentum < 0
    - Lagging:   RS < 1.0 and RS momentum < 0
    """
    sector_ret = sector_etf_df["Close"].pct_change(lookback)
    spy_ret = spy_df["Close"].pct_change(lookback)
    rs = sector_ret / spy_ret.replace(0, np.nan)
    rs_momentum = rs - rs.shift(21)
    latest_rs = rs.iloc[-1]
    latest_mom = rs_momentum.iloc[-1]
    if pd.isna(latest_rs) or pd.isna(latest_mom):
        return "neutral", 1.0
    if latest_rs > 1.0 and latest_mom > 0:
        return "leading", 1.25
    elif latest_rs <= 1.0 and latest_mom > 0:
        return "improving", 1.0
    elif latest_rs > 1.0 and latest_mom <= 0:
        return "weakening", 0.75
    else:
        return "lagging", 0.5
```

**Step 3 — Integrate into signal generation:**
```python
# In generate_signals, before BUY signal:
sector_etf = TICKER_SECTOR_MAP.get(ticker)
if sector_etf and sector_etf in data:
    quadrant, size_mult = compute_sector_quadrant(data[sector_etf], data["SPY"])
    if quadrant == "lagging":
        continue  # Skip lagging sectors
    position_size = int(position_size * size_mult)
```

---

## 6. Shorting / Short-Side Strategies

### What Was Excluded

The original strategy defines a full shorting framework in **Section VI** (lines 394-425):

- **Pattern 1A:** Price bounces off resistance multiple times (mirror of Pattern 1)
- **Pattern 2A:** Price breaks below support with strong red candle
- **Pattern 3A:** After breakdown, price rallies to retest broken support as resistance
- **EMA Method for Short Entry:** Price below EMA 50, EMA 20 crosses below EMA 50, short on pullback to EMA resistance
- **Relative High-Low Method:** Lower high + lower low formation → enter Pattern 2A or 3A
- **Rules:** Prefer shorting ETFs over stocks, never hold stock shorts overnight, VIX > 20 is best shorting environment

### Why Excluded

The autoresearch baseline is long-only to reduce complexity. Adding shorts roughly doubles the signal generation logic and introduces additional risks (unlimited loss potential, borrow costs, harder to backtest realistically).

### How to Add It Back

1. Add `action: "SHORT"` and `action: "COVER"` to the signal schema
2. Mirror the long entry logic with inverted conditions:
   - Detect downtrend (EMA 20 < EMA 50 < EMA 200)
   - Find resistance levels (nearest resistance above price)
   - Enter short on pullback to resistance
   - Stop-loss 1 ATR above resistance
   - Take-profit at support below
3. Update the backtest engine in `prepare.py` to handle short positions (track borrow costs, margin, unlimited loss cap)

---

## 7. Partial Profit Taking

### What Was Excluded

The original strategy recommends splitting positions for partial exits (lines 244-258):

- Split position into two equal parts
- Take profit on first half at a conservative target (may have R/R < 2)
- Let second half run to full target
- Both parts share the same entry and stop loss
- Overall combined R/R must still exceed 2:1
- Creates two separate orders with same OCA (One Cancels All) group

For trend trades, a three-phase partial exit is defined:
- **Phase 1 (1/3 to 1/2):** No new higher high, same high, or lower high formed
- **Phase 2 (tighten stops):** Price breaks below EMA 20, volume increases on down days
- **Phase 3 (full exit):** Lower low formed, close below EMA 50

### What's Partially Implemented

The baseline has trend trade exits (close below EMA 50) and swing trade time-based exits, but no partial profit taking.

### How to Add It Back

```python
# In position tracking, add partial_exited flag:
open_positions[ticker] = {
    "entry_date": day_ts,
    "entry_price": entry_price,
    "stop_loss": stop_loss,
    "strategy_type": strategy_type,
    "initial_shares": position_size,
    "remaining_shares": position_size,
    "partial_exited": False,
}

# In position management:
if not pos["partial_exited"]:
    # Check if conservative target hit (50% of distance to resistance)
    partial_target = pos["entry_price"] + (take_profit - pos["entry_price"]) * 0.5
    if price >= partial_target:
        exit_shares = pos["remaining_shares"] // 2
        signals.append({
            "action": "SELL", "position_size": exit_shares, ...
        })
        pos["remaining_shares"] -= exit_shares
        pos["partial_exited"] = True
```

Note: This requires changes to the backtest engine in `prepare.py` to support partial position exits (currently it's all-or-nothing per ticker).

---

## 8. Position Sizing Grid (Macro-Adjusted)

### What Was Excluded

The original strategy uses a compound position sizing adjustment (lines 636-646):

```
Base Risk: 1% of capital

Adjustments (multiplicative):
- VIX Level:       < 15 → 1.0x  |  15-20 → 1.0x  |  20-25 → 0.75x  |  > 25 → 0.5x
- Sector Strength:  Leading → 1.25x  |  Improving → 1.0x  |  Weakening → 0.75x  |  Lagging → 0.5x
- Event Risk:       None → 1.0x  |  Low → 1.0x  |  Medium → 0.75x  |  High → 0.5x

Final Risk = Base × VIX_mult × Sector_mult × Event_mult
```

### What's Partially Implemented

The baseline has a basic VIX filter (skip trades if VIX > 25) but does not scale position sizes by VIX level, sector strength, or event risk.

### How to Add It Back

```python
def compute_position_size_multiplier(vix, sector_quadrant=None, event_risk="none"):
    """Compute compound position sizing multiplier."""
    # VIX adjustment
    if vix < 15:
        vix_mult = 1.0
    elif vix < 20:
        vix_mult = 1.0
    elif vix < 25:
        vix_mult = 0.75
    else:
        vix_mult = 0.5

    # Sector adjustment
    sector_mults = {"leading": 1.25, "improving": 1.0, "weakening": 0.75, "lagging": 0.5}
    sector_mult = sector_mults.get(sector_quadrant, 1.0)

    # Event risk adjustment
    event_mults = {"none": 1.0, "low": 1.0, "medium": 0.75, "high": 0.5}
    event_mult = event_mults.get(event_risk, 1.0)

    return vix_mult * sector_mult * event_mult
```

---

## 9. Margin Management Rules

### What Was Excluded

From **Section VIII** (lines 453-488):

- Initial/maintenance margin tracking
- Keep excess liquidity > 50% to avoid margin calls
- Maximum 70% margin usage for trend trading with multiple positions
- In high volatility (VIX > 25), reduce margin usage to 50% max
- CFD considerations (20-33% margin, daily interest charges)
- Monitor Net Liquidation Value (NLV) daily

### Why Excluded

The backtest engine uses a simplified cash-based model (no margin). Adding margin would require tracking buying power, maintenance requirements, and margin calls — significant complexity for marginal improvement in backtest realism.

### How to Add It Back

Would require rewriting the backtest engine in `prepare.py` to track margin state. For autoresearch purposes, the cash-based model is sufficient since it still correctly tracks P&L and position limits.

---

## 10. Swing vs Trend Strategy Selection Logic

### What's Partially Implemented vs Original

**Original rules (lines 140-145):**
- Choose TREND when: clear bullish trending (rebounds on EMA 20/50), trend reversal just occurred (all 3 conditions), consistent HH/HL
- Choose SWING when: moderately bullish or sideways, EMAs crisscrossing, range-bound
- NEVER do trend trading on sideways charts
- In volatile macro (VIX > 20), prefer swing over trend
- In stable trending markets (VIX < 15), prefer trend trading
- Do NOT switch from swing to trend mid-trade

**What the baseline does:**
- If uptrend AND R/R ≥ 3:1, assign as trend trade (trailing stop, no fixed target)
- Otherwise if R/R ≥ 2:1, assign as swing trade (fixed take-profit)
- VIX > 25 = skip all trades (too aggressive — could be softened)

**What's missing:**
- The VIX-based preference for swing vs trend (VIX > 20 prefer swing)
- The "never trend trade sideways" enforcement (currently just checks for uptrend)
- Tracking strategies separately for performance measurement
- The three-condition trend reversal detection (trendline break + higher low + higher high)

### How to Add the VIX Overlay

```python
# In signal generation, when deciding strategy type:
if trend == "uptrend" and rr_ratio >= TREND_MIN_RR:
    if vix > 20:
        strategy_type = "swing"  # Prefer swing in volatile markets
        take_profit_signal = take_profit
    else:
        strategy_type = "trend"
        take_profit_signal = None  # Use trailing stop
```

---

## Summary: Priority Order for Re-Adding Features

| Priority | Feature | Effort | Impact on Sharpe |
|----------|---------|--------|-----------------|
| 1 | Candlestick confirmation | Low | Medium — reduces false entries in volatile markets |
| 2 | Earnings date avoidance | Medium | High — prevents holding through binary events |
| 3 | Sector rotation quadrants | Medium | Medium — filters out weak sectors, adjusts sizing |
| 4 | Partial profit taking | Medium | Medium — improves risk-adjusted returns |
| 5 | Position sizing grid (macro) | Low | Low-Medium — fine-tuning, not game-changing |
| 6 | News sentiment integration | High | Uncertain — could be high but requires data source |
| 7 | Shorting strategies | High | Medium — adds opportunity in bear markets |
| 8 | Margin management | High | Low — primarily a live trading concern |
| 9 | Chart image reading | N/A | N/A — not applicable to autoresearch backtesting |

Features 1-5 can be added directly to `strategy.py` without changing `prepare.py`. Features 6-7 require changes to both files (new data sources in prepare, new logic in strategy). Feature 8 requires a backtest engine rewrite. Feature 9 is specific to the live n8n workflow.
