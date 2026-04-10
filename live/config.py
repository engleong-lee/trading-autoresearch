"""
config.py — Configuration for the live paper trading bot.

Fill in your Alpaca paper trading API keys.
DO NOT commit this file with real keys — it's in .gitignore.
"""

# Alpaca Paper Trading API
ALPACA_API_KEY = ""
ALPACA_SECRET_KEY = ""
ALPACA_BASE_URL = "https://paper-api.alpaca.markets"

# Watchlist (must match strategy.py)
WATCHLIST = ["NVDA", "AAPL", "AMZN", "V", "MA", "AMD", "AVGO", "CRWD", "VGK", "INTU"]

# How many days of history to fetch for indicator calculation
HISTORY_DAYS = 400  # Need ~200+ for EMA 200 warmup

# Logging
LOG_FILE = "live/trading.log"
