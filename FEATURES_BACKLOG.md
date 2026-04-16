# Feature Backlog

Features not in the original strategy doc, identified from structural analysis of current strategy gaps.

**Priority:** Reduce val-to-OOS decay (overfitting) over raw val Sharpe gains.

## Backlog

| # | Feature | Type | Effort | Result | Status |
|---|---------|------|--------|--------|--------|
| 1 | Correlation-aware position limiting | Risk mgmt | Low | **Helped.** MAX_PER_SECTOR=2 reduced decay, +0.10 OOS. Sector limit drove val consistency across windows. | R6/R7 ✓ |
| 2 | SPY regime gate (risk-off skip) | Risk mgmt | Low | **Neutral.** Gate never fired in 2023-2024 bull windows. Insurance for future drawdowns, zero cost to keep. | R6/R7 ✓ |
| 3 | Re-entry cooldown after stop-out | Risk mgmt | Low | **Neutral/Hurt.** COOLDOWN=2 hurt OOS (-0.22). Disabled (=0). Infrastructure kept for future tuning. | R6/R7 ✗ |
| 4 | Day-of-week seasonality filter | Entry filter | Low | **Harmful.** Skipping any single day removes ~20% trades, -2 to -3 OOS Sharpe. Strategy needs all 5 trading days. | R7 ✗ |
| 5 | Bollinger Band bounce entry | Entry pattern | Medium | **Harmful.** Added noise, -0.14 OOS Sharpe. BB mean-reversion signal doesn't complement S/R pullback edge. | R7 ✗ |
| 6 | S/R quality scoring (touch count) | Entry quality | Medium | **Harmful.** Requiring 2+ touches eliminated 70% of trades, -5 OOS Sharpe. Current 7-bar swing detection is already selective enough. | R7 ✗ |
| 7 | Breakout entry (close above resistance) | Entry pattern | Medium | **Neutral/Harmful.** Slightly negative OOS. Prev-day S/R recomputation is slow, breakout signals noisy. | R7 ✗ |

## Grouping Strategy

**Group A (R6/R7) — Robustness / anti-overfit (items 1-3):**
Sector concentration limit was the only clear winner. Regime gate is free insurance. Cooldown disabled.

**Group B (R7) — New edge sources (items 4-7):**
All four features tested and rejected. R2's simple S/R pullback is the core edge — adding alternative entry signals or filters only adds noise. The strategy's strength is its simplicity.

## Key R7 Methodology Insight

Blind autoresearch (random search optimizing val Sharpe) compounds overfitting — R4-R6 added +3 val but lost OOS. R7 tested each change individually against OOS before accepting. This manual OOS-verified approach produced the only genuine OOS improvement across all rounds.

## Decay Trend

| Round | Val | OOS | Decay | Notes |
|-------|-----|-----|-------|-------|
| R2 | 15.12 | 12.53 | 17% | Baseline |
| R4 | 18.01 | 12.22 | 32% | +sector rotation, partial exits, momentum |
| R5 | 18.52 | 11.90 | 36% | +momentum ranking, ATR vol, gap analysis |
| R6 | 17.78 | 12.47 | 30% | Anti-overfit: disabled boosts, capped sizing |
| **R7** | **17.05** | **14.27** | **16%** | Rebased from R2, OOS-verified changes only |

R7 is the first round to genuinely improve OOS Sharpe above R2.
