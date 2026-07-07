# Claude Cowork Prompt — R7 Daily Trading Automation

> Paste everything below into Claude Cowork as the task prompt, and attach the
> whole repo (it already contains the finished automation under `live/`).

---

## ROLE

You operate an automated **paper** trading pipeline for the R7 swing/trend
strategy. The scripts are already written and tested — your job is to install
deps, set env vars/secrets, run the jobs on schedule, and keep them healthy. Do
NOT rewrite the strategy or the pipeline; only run/fix/configure it.

## WHAT'S ALREADY BUILT (in the attached repo)

- `strategy.py` — the R7 strategy (do not modify). Entry point
  `generate_signals(data, start_date, end_date)`.
- `live/automation.py` — **daily job**: load watchlist from Postgres → fetch data
  (yfinance) → generate R7 signals → news veto → place Alpaca orders (1% equity
  risk/ticker, max 5 positions) → upsert daily report to Postgres → broadcast to
  Telegram.
- `live/weekly_summary.py` — **Sunday job**: aggregate the week's rows → write to
  Postgres → broadcast to Telegram.
- `live/db.py`, `live/news.py`, `live/notify.py`, `live/config.py`,
  `live/.env.example` — supporting modules and config.
- `live/README.md` — full operator docs.

## SETUP (run once)

1. `pip install -r requirements.txt`
2. Set secrets as env vars in Cowork's secret store (or create `live/.env` from
   `live/.env.example` — it's auto-loaded by `config.py`). Required keys:
   ```
   ALPACA_API_KEY, ALPACA_SECRET_KEY        # paper keys
   ALPACA_BASE_URL = https://paper-api.alpaca.markets
   PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASSWORD, PG_SCHEMA
   TELEGRAM_BOT_TOKEN                        # @BotFather -> befreedombot -> API Token
   ```
   (The actual values are provided separately — do not paste real secrets here.)
3. Verify: `python -m live.automation --status` (should print paper account
   equity/positions) and `python -m live.automation --dry-run` (should print
   today's signals + report with NO orders/DB/Telegram writes).

## SCHEDULES (create these in Cowork; times in MYT = UTC+8)

- **Daily**, Mon–Fri, **10:00 PM MYT** (≈30 min after US open during US DST;
  shift to 11:00 PM MYT during US standard time Nov–Mar):
  `python -m live.automation`
  - Safe on holidays: the market-closed guard just logs `market_closed`.
- **Weekly**, **Sunday** (any time):
  `python -m live.weekly_summary`

## WHAT EACH DAILY RUN DOES (for your awareness)

1. Skips (logs `market_closed`) if Alpaca `get_clock()` says market is closed.
2. Reads active tickers from `pmvwft68q3g8g19.ticker_watchlist`.
3. Fetches ≥400d OHLCV for watchlist + SPY/QQQ/VIX, runs R7 for today's signals.
4. News veto (supplementary): for each BUY computes a news score from
   `news_signals`; skips the BUY only if score ≤ -0.6 AND a recent high-impact
   bearish item exists. News never forces a buy.
5. Executes on Alpaca paper: SELLs first, then BUYs sized at **1% equity risk per
   ticker, max 5 positions**, then logs UPDATE_STOP.
6. Upserts a markdown report into `autoresearch_trading_result` (one row per date).
7. Broadcasts the report to all Telegram subscribers.

## TELEGRAM — ACTION REQUIRED FROM THE HUMAN

- Provide `TELEGRAM_BOT_TOKEN` (from @BotFather → befreedombot → API Token).
- Intended recipients: **@engleonglee** (Eng Leong Lee) and **@Chee_Leong_Chin**
  (Chee Leong Chin). A bot cannot message a user by @username or phone — only by
  numeric chat_id, and only after the user presses **Start**. So both must open
  befreedombot and tap Start once; the job then auto-captures their chat_ids via
  `getUpdates` into `telegram_subscribers` (storing the @username so you can confirm
  it's these two). Optionally seed known ids via `TELEGRAM_SEED_CHAT_IDS="123,456"`.

## OPERATIONAL RULES

- Paper only (`paper=True`); the keys above are paper keys. Never switch to live
  without new live keys and explicit approval.
- Idempotent per day (report row is upserted, not duplicated).
- On failure the daily job writes a `status='error'` row and Telegram-alerts.
  If a run errors: read `live/trading.log`, fix config/transient issues, re-run
  `python -m live.automation`. Do NOT edit `strategy.py` or `prepare.py`.
- Migration note: this replaces the previous automation writing to the same
  tables — ensure the old job is disabled so both don't run.
