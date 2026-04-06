# Autoresearch: Swing/Trend Trading Strategy Optimization

Adapted from [karpathy/autoresearch](https://github.com/karpathy/autoresearch) for US equity swing/trend trading.

## Architecture

Three files, same pattern as Karpathy's original:

| File | Who edits | Purpose |
|------|-----------|---------|
| `prepare.py` | **Nobody** (immutable) | Data download, backtest engine, walk-forward evaluation, Sharpe computation |
| `strategy.py` | **AI agent** | Trading logic, indicators, entry/exit rules, parameters |
| `program.md` | **Human** | Agent instructions, research directions, constraints |

## Watchlist

NVDA, AAPL, AMZN, V, MA, AMD, AVGO, CRWD, VGK, INTU

## Metric

**Walk-forward validated Sharpe Ratio** (annualized, averaged across 5 overlapping validation windows in 2023-2024, with consistency penalty).

## Time Splits

- **Training data:** 2018-01-01 to 2022-12-31 (for indicator warmup and strategy dev context)
- **Validation:** 2023-01-01 to 2024-06-30 (5 walk-forward windows, agent scored here)
- **Test:** 2024-07-01 to 2025-12-31 (held-out, use sparingly)

## Quick Start

```bash
# 1. Install dependencies
pip install numpy pandas yfinance pyarrow

# 2. Download data (one-time, ~2 min)
python prepare.py --download

# 3. Run baseline evaluation
python prepare.py

# 4. Point your AI agent (Devin/Claude Code) at program.md and let it go:
#    "Read program.md and kick off the autoresearch experiment loop."
```

## Running with Devin

1. Create a new Devin session
2. Upload or clone this repo into the session
3. Run `pip install numpy pandas yfinance pyarrow` and `python prepare.py --download`
4. Prompt Devin:
   ```
   Read program.md carefully. This is an autoresearch loop for trading strategy optimization.
   Run the setup steps, then begin the autonomous experiment loop.
   Do not stop or ask me anything. Keep running experiments.
   ```
5. Let it run overnight. Check `results.tsv` and git log in the morning.

## Design Choices

- **Walk-forward validation** prevents overfitting to a single period
- **Consistency penalty** (0.5 × std of Sharpes across windows) rewards strategies that work across different market conditions
- **Minimum trade count** (3 per window) prevents degenerate strategies
- **Realistic backtest** includes commissions ($1/trade), slippage (0.05%), and position limits
- **No look-ahead bias** enforced by the signal generation contract
- **Long-only** for simplicity (shorting can be added later)

## After the Run

Run on the held-out test set to verify:
```bash
python prepare.py --test
```

Review git log to see the improvement trail:
```bash
git log --oneline autoresearch/<tag>
```
