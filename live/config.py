"""
config.py — Configuration for the live R7 trading automation.

Secrets are read from environment variables. For local/attached runs, put them in
`live/.env` (gitignored) — it's auto-loaded here via python-dotenv. In Claude Cowork,
set them as env vars/secrets. NEVER hard-code real secrets in this committed file.
Paper trading only. See live/.env.example for the full list.
"""

import os
from pathlib import Path

# Auto-load live/.env (and any .env in cwd) if python-dotenv is installed.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
    load_dotenv()
except Exception:
    pass

# ─────────────────────────────────────────────────────────────
# ALPACA (paper trading)
# ─────────────────────────────────────────────────────────────
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_PAPER = True  # paper only — never flip without new live keys

# ─────────────────────────────────────────────────────────────
# POSTGRES (NocoDB-backed)
# ─────────────────────────────────────────────────────────────
PG_HOST = os.getenv("PG_HOST", "")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DB = os.getenv("PG_DB", "root_db")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")
PG_SCHEMA = os.getenv("PG_SCHEMA", "pmvwft68q3g8g19")

# ─────────────────────────────────────────────────────────────
# TELEGRAM  (bot: befreedombot)
# ─────────────────────────────────────────────────────────────
# Get the token from @BotFather -> /mybots -> befreedombot -> API Token.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Optional comma-separated chat_ids to seed the subscriber list, e.g. "12345,67890"
TELEGRAM_SEED_CHAT_IDS = os.getenv("TELEGRAM_SEED_CHAT_IDS", "")

# ─────────────────────────────────────────────────────────────
# RISK / POSITION SIZING
# ─────────────────────────────────────────────────────────────
# Live sizing overrides the strategy's internal sizing:
#   - Risk (max potential loss) per ticker = 1% of account equity
#   - Portfolio cap = 5% total risk => at most 5 concurrent positions
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))   # 1% of equity per trade
MAX_PORTFOLIO_RISK = float(os.getenv("MAX_PORTFOLIO_RISK", "0.05"))  # 5% total
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))          # 5% / 1% = 5 tickers

# ─────────────────────────────────────────────────────────────
# NEWS VETO (supplementary — never forces a buy, only vetoes)
# ─────────────────────────────────────────────────────────────
NEWS_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "3"))
NEWS_VETO_SCORE = float(os.getenv("NEWS_VETO_SCORE", "-0.6"))  # skip BUY if score <= this
NEWS_MAG_WEIGHTS = {"low": 0.5, "medium": 1.0, "high": 1.5}

# ─────────────────────────────────────────────────────────────
# DATA / GENERAL
# ─────────────────────────────────────────────────────────────
# Fallback watchlist if the DB is unreachable (must match strategy.py known tickers)
WATCHLIST = ["NVDA", "AAPL", "AMZN", "V", "MA", "AMD", "AVGO", "CRWD", "VGK", "INTU"]
BENCHMARKS = ["SPY", "QQQ", "^VIX"]
HISTORY_DAYS = 400  # need ~200+ for EMA200 warmup

# Logging
LOG_FILE = "live/trading.log"
