# Live Paper Trading with Alpaca

This folder contains a standalone daily trading bot that executes the R2 autoresearch strategy via the Alpaca paper trading API. No n8n required.

---

## R7 Automation (Postgres + News + Telegram) — for Claude Cowork

`automation.py` and `weekly_summary.py` are the production automation built on top
of `trader.py`. They run the **R7** strategy, log results to Postgres, cross-check
news sentiment, and notify via Telegram. Designed to be scheduled by Claude Cowork.

### Modules
| File | Purpose |
|------|---------|
| `automation.py` | Daily job: signals → news veto → Alpaca orders → DB report → Telegram |
| `weekly_summary.py` | Sunday job: aggregate the week's rows → DB + Telegram |
| `db.py` | Postgres helpers (schema `pmvwft68q3g8g19`) |
| `news.py` | Supplementary news scoring + bearish veto |
| `notify.py` | Telegram broadcast + auto-capture subscribers via `getUpdates` |
| `config.py` | Env-driven config (Alpaca / Postgres / Telegram / risk) |
| `.env.example` | Copy to `.env` and fill in secrets |

### Configuration
All secrets read from env (or `live/.env`), falling back to defaults in `config.py`.
The one value you MUST supply is `TELEGRAM_BOT_TOKEN` (from @BotFather → befreedombot
→ API Token). See `.env.example`.

### Risk sizing (overrides strategy sizing)
- **1% of account equity risk per ticker** (`RISK_PER_TRADE=0.01`)
- **Max 5 concurrent positions** = 5% total portfolio risk (`MAX_POSITIONS=5`)
- Qty = `floor(0.01 * equity / (entry - stop_loss))`, capped by buying power.

### News handling (supplementary only)
For each BUY, a per-ticker news score in [-1, +1] is computed from `news_signals`
(confidence × magnitude weighted). A BUY is vetoed (`SKIP_BEARISH_NEWS`) **only** if
score ≤ -0.6 **and** there is a recent high-impact bearish item. News never forces a
buy and never affects SELL/UPDATE_STOP.

### Database tables (schema `pmvwft68q3g8g19` in DB `root_db`)
- `ticker_watchlist` (read) — active tickers.
- `news_signals` (read) — sentiment, matched via `entities.tickers`.
- `autoresearch_trading_result` (write) — one upserted row per US trading date.
- `autoresearch_trading_weekly_summary` (write) — one row per Sunday.
- `telegram_subscribers` (auto-created) — chat_ids captured from `getUpdates`.

### Telegram: adding recipients
A bot can only message users who have pressed **Start** on it. On every run the job
calls `getUpdates` and upserts any new chat_ids into `telegram_subscribers`, then
broadcasts to all active subscribers. So: ask each recipient to open the bot and tap
Start once. Optionally seed via `TELEGRAM_SEED_CHAT_IDS`.

### Run manually
```bash
python -m live.automation --dry-run   # preview signals + report, no orders/DB/Telegram
python -m live.automation             # live paper run (orders + DB + Telegram)
python -m live.automation --status    # Alpaca account/positions
python -m live.weekly_summary         # build + persist + send weekly summary
```

### Claude Cowork schedules (times in MYT = UTC+8)
- **Daily** (Mon–Fri, ~30 min after US open): `10:00 PM MYT` (US DST) — `python -m live.automation`
  - The `get_clock()` guard makes weekend/holiday runs safe (logged as `market_closed`).
  - During US standard time (Nov–Mar) shift to `11:00 PM MYT`.
- **Weekly** (Sunday): any time — `python -m live.weekly_summary`

---

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
