# Feature Backlog — R6+

Features not in the original strategy doc, identified from structural analysis of current strategy gaps.

**Priority:** Reduce val-to-OOS decay (overfitting) over raw val Sharpe gains.

## Backlog

| # | Feature | Type | Effort | Expected Impact | Status |
|---|---------|------|--------|----------------|--------|
| 1 | Correlation-aware position limiting | Risk mgmt | Low | High — reduces DD, improves OOS | ○ |
| 2 | SPY regime gate (risk-off skip) | Risk mgmt | Low | High — protects during corrections | ○ |
| 3 | Re-entry cooldown after stop-out | Risk mgmt | Low | Medium — breaks losing streaks | ○ |
| 4 | Day-of-week seasonality filter | Entry filter | Low | Medium — cheap edge for 1-day holds | ○ |
| 5 | Bollinger Band bounce entry | Entry pattern | Medium | Medium — new uncorrelated signal source | ○ |
| 6 | S/R quality scoring (touch count) | Entry quality | Medium | Medium — better level selection | ○ |
| 7 | Breakout entry (close above resistance) | Entry pattern | Medium | Medium — diversifies signal source | ○ |

## Grouping Strategy

**Group A (R6) — Robustness / anti-overfit (items 1-3):**
All three are risk management features that limit exposure. They should reduce drawdown variance across windows, directly attacking the val-to-OOS decay problem. Low effort, can implement + optimize together.

**Group B (R7) — New edge sources (items 4-7):**
These add new entry signals or filters. Higher risk of adding noise. Should only attempt after Group A stabilizes OOS.

## Current Decay Trend

| Round | Val | OOS | Decay |
|-------|-----|-----|-------|
| R2 | 15.12 | 12.49 | 17% |
| R4 | 18.01 | 12.22 | 32% |
| R5 | 18.52 | 11.90 | 36% |

Target for R6: reduce decay below 25% while keeping val Sharpe ≥ 17.
