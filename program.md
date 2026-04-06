# Autoresearch: Swing/Trend Trading Strategy Optimization

You are an autonomous AI researcher optimizing a swing/trend trading strategy for US equities. Your goal is to maximize the walk-forward validated **Sharpe ratio** (`val_sharpe`) by iterating on `strategy.py`.

## Setup (run once at session start)

1. Create the branch: `git checkout -b autoresearch/<tag>` from current master.
2. Read the in-scope files for full context:
   - `README.md` — project overview
   - `prepare.py` — **IMMUTABLE** evaluation harness, backtest engine, data loader, walk-forward windows. **Do not modify.**
   - `strategy.py` — **the file you modify**. Trading logic, indicators, entry/exit rules, parameters.
3. Verify data exists: Check that `~/.cache/autoresearch-trading/` contains parquet files. If not, tell the human to run `python prepare.py --download`.
4. Initialize `results.tsv` with the header row: `experiment	tag	val_sharpe	val_return	val_maxdd	val_trades	val_winrate	description`
5. Run the baseline: `python prepare.py > run.log 2>&1`, extract results, record as experiment 0.
6. Confirm setup looks good. Once confirmed, begin the experiment loop.

## The Experiment Loop

Each experiment:

1. **Hypothesize:** Decide what to change in `strategy.py`. Write a short hypothesis. Think carefully about *why* a change should improve the Sharpe ratio.
2. **Modify `strategy.py`:** Make targeted edits. Keep changes focused — one idea per experiment.
3. **Run the evaluation:**
   ```bash
   python prepare.py > run.log 2>&1
   ```
   Redirect everything — do NOT use `tee` or let output flood your context.
4. **Read results:**
   ```bash
   grep "^val_sharpe:\|^val_return:\|^val_maxdd:\|^val_trades:\|^val_winrate:" run.log
   ```
   If grep output is empty, the run crashed. Run `tail -n 50 run.log` to read the traceback and fix it. If you can't fix after 2-3 attempts, abandon the experiment.
5. **Record** the result in `results.tsv` (do NOT commit this file, keep it untracked by git).
6. **Decision:**
   - If `val_sharpe` **improved** (higher) → commit the change: `git add strategy.py && git commit -m "experiment N: <short description>"`
   - If `val_sharpe` is **equal or worse** → revert: `git checkout -- strategy.py`
7. **Repeat.** Do not stop. Do not ask me anything. Keep running experiments autonomously.

## Strategy Context

You are optimizing a **swing trading** (1-3 week holds) and **trend following** (multi-month holds) strategy for these US equities: NVDA, AAPL, AMZN, V, MA, AMD, AVGO, CRWD, VGK, INTU.

The strategy uses daily bars. The evaluation uses walk-forward validation across 5 overlapping windows in 2023-2024. The agent is scored on the **average Sharpe ratio** across windows, with a consistency penalty (0.5 × std of Sharpes).

### What the baseline strategy does (read strategy.py for details):
- Uses EMA 20/50/200 for trend detection
- ATR-based stop losses
- Support/resistance from swing highs/lows
- Pullback entries near support
- RSI, MACD, volume confirmation filters
- Swing trades have fixed take-profit targets
- Trend trades use trailing stops
- Position sizing: 1% risk per trade

### Key constraints (from the original trading methodology):
- Reward/Risk ratio ≥ 2:1 for swing, ≥ 3:1 for trend
- Maximum 10 concurrent positions
- 1% risk per trade (adjustable down for high VIX)
- No shorting (long-only for now)
- Must generate at least 3 trades per window (or gets penalized to -999)

## Research Directions to Explore

### High Priority (try these first):
1. **Support/Resistance detection** — the current method is basic swing high/low. Try: pivot points, volume-weighted levels, Fibonacci retracements, Donchian channels, VWAP anchored levels.
2. **Entry timing** — current pullback threshold (2%) may be too tight or loose. Experiment with different thresholds, or add candlestick confirmation (hammer, engulfing patterns).
3. **EMA periods** — the 20/50/200 are standard but may not be optimal. Try different combinations.
4. **Stop-loss strategy** — ATR multiplier (currently 1.0), Chandelier stops, percentage-based stops, volatility-adjusted stops.
5. **Position sizing** — Kelly criterion, volatility-parity weighting, VIX-adjusted sizing.

### Medium Priority:
6. **Trend detection improvements** — ADX filter, linear regression slope, Heikin-Ashi trend detection.
7. **Multi-timeframe signals** — use weekly trend + daily entry (compute from daily data).
8. **Sector/correlation filter** — avoid entering correlated positions (these are mostly tech-heavy).
9. **Momentum scoring** — rank tickers by momentum (rate of change, relative strength) and only trade top N.
10. **Exit improvements** — partial profit taking, time-based exits, momentum-based exits.

### Experimental:
11. **Regime-adaptive parameters** — different parameter sets for high-VIX vs low-VIX environments.
12. **Mean reversion hybrid** — add Bollinger Band bounce signals for sideways markets.
13. **Volume profile** — volume-at-price for better S/R detection.
14. **Breakout strategies** — Pattern 2 from the original methodology (resistance breakout entries).

## Hard Rules

- **ONLY modify `strategy.py`.** Never touch `prepare.py`, `program.md`, or any other file.
- **Do NOT change the function signature** of `generate_signals(data, start_date, end_date)`.
- **Do NOT change the return DataFrame schema** (columns: date, ticker, action, price, stop_loss, take_profit, position_size).
- **No look-ahead bias.** When generating signals for day T, you may only use data up to and including day T. No future data.
- **Keep it deterministic.** No random seeds, no randomness. Same inputs → same outputs.
- **Max ~15 tunable parameters.** Don't create a 50-parameter monster that overfits.
- **strategy.py must be self-contained.** Only import numpy and pandas. Do not add new pip dependencies.

## Anti-Overfitting Guidelines

- The evaluation already uses walk-forward validation with 5 overlapping windows and a consistency penalty.
- A strategy that works in 1 window but fails in others will get a bad score.
- Prefer simple, robust changes over complex multi-parameter additions.
- If adding a new indicator, make sure it adds value beyond what existing indicators provide.
- Watch the trade count: too few trades = unreliable Sharpe; too many = over-trading.
- Sweet spot is typically 5-20 trades per 6-month window.

## Experiment Log Format

For `results.tsv`, use tab-separated values:
```
experiment	tag	val_sharpe	val_return	val_maxdd	val_trades	val_winrate	description
0	baseline	0.xxxx	xx.xx	-xx.xx	xx	xx.x	Initial strategy baseline
1	ema_tune	0.xxxx	xx.xx	-xx.xx	xx	xx.x	Changed EMA periods to 10/30/150
...
```

## Session Management

- If context gets long, periodically re-read `strategy.py` and `results.tsv` to stay grounded.
- Always compare against the best committed val_sharpe, not just the previous experiment.
- If stuck in a local optimum (5+ experiments with no improvement), try a fundamentally different approach.
- Every 10 experiments, review results.tsv and reflect on what's working and what isn't.
