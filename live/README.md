# Live Paper Trading with Alpaca

This folder contains a standalone daily trading bot that executes the R2 autoresearch strategy via the Alpaca paper trading API. No n8n required.

## How It Works

1. Fetches latest daily OHLCV data from Yahoo Finance (free)
2. Imports `strategy.py` from the parent repo and generates today's signals
3. Checks existing Alpaca positions and manages exits (stop-loss, take-profit, EMA exit, max hold)
4. Places new bracket orders (limit entry + stop-loss + take-profit) via Alpaca API
5. Logs everything to `trading.log`

## Prerequisites

```bash
pip install alpaca-py yfinance numpy pandas
```

## Setup

### Step 1: Create Alpaca paper trading API keys

1. Go to https://app.alpaca.markets
2. Log in to your account
3. Switch to **Paper Trading** in the left sidebar
4. Go to **API Keys** and generate a new key pair
5. Copy both the **API Key ID** and **Secret Key**

> You can create multiple paper API keys under the same Alpaca login.
> Each key pair operates independently with its own $100k paper balance.

### Step 2: Configure your API keys

Edit `live/config.py` and replace the placeholder values:

```python
ALPACA_API_KEY = "PKXXXXXXXXXXXXXXXX"
ALPACA_SECRET_KEY = "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
```

> **Do not commit this file** — it's already in `.gitignore`.

### Step 3: Verify the setup

```bash
cd ~/research/trading-autoresearch

# Check account connection
python3 live/trader.py --status
```

You should see your paper account equity (~$100,000), buying power, and any existing positions.

## Usage

### Dry run (no orders placed, just shows signals)

```bash
python3 live/trader.py --dry-run
```

This fetches live data, runs the strategy, and prints what orders *would* be placed. Use this first to verify signals look reasonable.

### Live paper trading (places real paper orders)

```bash
python3 live/trader.py
```

This will:
- Skip if market is closed
- Fetch data for all 10 watchlist tickers + SPY/QQQ/VIX
- Generate signals using the R2 strategy
- Sell any positions that have exit signals (EMA exit, max hold, earnings)
- Place bracket orders for new buy signals (limit entry + stop-loss + take-profit)
- Log everything to `live/trading.log`

### Check account status

```bash
python3 live/trader.py --status
```

## Schedule with Cron (Automated Daily Runs)

The script should run once daily, ~30 minutes after US market open to allow the opening candle to settle.

**US market hours in Malaysia time (MYT = UTC+8):**
- Market open: 9:30 PM MYT (during US daylight saving, Mar–Nov)
- Market open: 10:30 PM MYT (during US standard time, Nov–Mar)
- Recommended run time: **10:00 PM MYT** (daylight saving) or **11:00 PM MYT** (standard)

### Set up the cron job

```bash
crontab -e
```

Add this line (for US daylight saving period, Mar–Nov):

```cron
0 22 * * 1-5 cd /Users/engleong.lee/research/trading-autoresearch && /usr/bin/python3 live/trader.py >> live/cron.log 2>&1
```

For US standard time (Nov–Mar), change `22` to `23`:

```cron
0 23 * * 1-5 cd /Users/engleong.lee/research/trading-autoresearch && /usr/bin/python3 live/trader.py >> live/cron.log 2>&1
```

### Verify cron is running

Check cron logs the next morning:

```bash
cat live/cron.log
```

Or check if any orders were placed:

```bash
python3 live/trader.py --status
```

### Important: Mac sleep prevention

Cron won't run if your Mac is asleep. Options:
- **caffeinate**: Run `caffeinate -s &` before sleeping to prevent sleep
- **pmset**: Schedule wake: `sudo pmset repeat wake MTWRF 21:55:00`
- **Energy settings**: System Settings → Energy → Prevent automatic sleeping when the display is off

## Files

| File | Purpose | Git tracked? |
|------|---------|-------------|
| `trader.py` | Main trading bot | Yes |
| `config.py` | API keys (template) | No (in .gitignore) |
| `README.md` | This file | Yes |
| `trading.log` | Execution log | No (in .gitignore) |
| `cron.log` | Cron output log | No (in .gitignore) |

## Strategy Details (R2)

The bot runs the R2 autoresearch strategy which was optimized across 50 experiments:

- **Watchlist:** NVDA, AAPL, AMZN, V, MA, AMD, AVGO, CRWD, VGK, INTU
- **Hold period:** ~2 days average
- **Entry:** Pullback to support with 1.2+ R/R ratio
- **Exit:** Bracket order (stop-loss + take-profit), EMA-18 exit, 2-day max hold
- **Risk:** 0.5% per trade (0.65% when VIX < 15)
- **Max positions:** 10 concurrent
- **Filters:** ADX > 15, skip downtrends, VIX < 30, earnings avoidance
- **Candlestick confirmation** required when VIX > 25

### Backtest performance (R2)

| Metric | Validation | Out-of-sample test |
|--------|-----------|-------------------|
| Sharpe | 15.12 | 12.49 |
| Return | 174.9% | 431% |
| Max DD | -0.90% | -1.05% |
| Win rate | 90.6% | 87.0% |
| Trades | 247.8 | 708 |

> **Disclaimer:** Past backtest performance does not guarantee future results.
> This is paper trading only. Do not use with real money without thorough
> forward testing and risk assessment.
