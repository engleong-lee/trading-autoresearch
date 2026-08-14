#!/usr/bin/env python3
"""
Autoresearch optimization loop.
Randomly varies strategy parameters, evaluates, keeps improvements.
"""

import subprocess, json, re, random, shutil, time, sys
from pathlib import Path

STRATEGY_FILE = Path(__file__).parent / "strategy.py"
BEST_BACKUP = Path(__file__).parent / "strategy_best.py"
NUM_EXPERIMENTS = int(sys.argv[1]) if len(sys.argv) > 1 else 50

# Parameter search space: (param_name, [values])
SEARCH_SPACE = {
    "SR_LOOKBACK": [40, 50, 60, 75, 90],
    "SWING_TRAIL_ATR_MULT": [0.5, 0.6, 0.8, 1.0, 1.2],
    "ATR_STOP_MULTIPLIER": [0.5, 0.6, 0.7, 0.8, 1.0],
    "PULLBACK_THRESHOLD_PCT": [0.01, 0.02, 0.03, 0.04],
    "EMA_FAST": [10, 12, 15],
    "EMA_MID": [20, 21, 25],
    "TP_OFFSET_PCT": [0.002, 0.003, 0.004, 0.005, 0.006],
    "ENTRY_OFFSET_PCT": [0.001, 0.002, 0.003],
    "VOL_RATIO_MIN": [0.2, 0.3, 0.5],
    "RISK_PCT": [0.005, 0.007, 0.01, 0.012],
    "MAX_POSITION_SHARES": [400, 600, 800, 1000],
    "SWING_MAX_HOLD_DAYS": [5, 7, 10, 14, 21],
    "SWING_MIN_RR": [1.5, 2.0, 2.5, 3.0],
    "SWING_TRAIL_PROFIT_PCT": [0.02, 0.03, 0.04, 0.05],
    "TREND_MIN_RR": [1.5, 2.0, 3.0, 4.0],
    "MAX_PER_SECTOR": [2, 3, 4, 5],
    "STOP_COOLDOWN_DAYS": [0, 1, 2, 3],
}

# How many params to vary per experiment (1-4)
PARAMS_PER_EXP = (1, 4)


def read_strategy():
    return STRATEGY_FILE.read_text()


def write_strategy(content):
    STRATEGY_FILE.write_text(content)


def get_param_value(content, param):
    """Extract current value of a parameter from strategy.py."""
    pattern = rf'^{param}\s*=\s*(.+?)(?:\s*#.*)?$'
    m = re.search(pattern, content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return None


def set_param_value(content, param, value):
    """Set a parameter value in strategy.py, preserving comments."""
    pattern = rf'^({param}\s*=\s*)(.+?)(\s*#.*)$'
    m = re.search(pattern, content, re.MULTILINE)
    if m:
        return re.sub(pattern, rf'\g<1>{value}\3', content, count=1, flags=re.MULTILINE)
    # No comment
    pattern2 = rf'^({param}\s*=\s*)(.+)$'
    return re.sub(pattern2, rf'\g<1>{value}', content, count=1, flags=re.MULTILINE)


def evaluate():
    """Run prepare.py (val) and test_forward.py (2026 OOS); return (val, forward, score)."""
    res1 = subprocess.run(
        [sys.executable, "prepare.py"],
        capture_output=True, text=True, timeout=300,
        cwd=str(STRATEGY_FILE.parent),
    )
    output1 = res1.stdout + res1.stderr
    m1 = re.search(r'val_sharpe:\s*([-\d.]+)', output1)
    val = float(m1.group(1)) if m1 else -999.0

    res2 = subprocess.run(
        [sys.executable, "test_forward.py"],
        capture_output=True, text=True, timeout=120,
        cwd=str(STRATEGY_FILE.parent),
    )
    output2 = res2.stdout + res2.stderr
    m2 = re.search(r'sharpe_ratio:\s*([-\d.]+)', output2)
    fwd = float(m2.group(1)) if m2 else -999.0

    if val == -999.0 or fwd == -999.0:
        print(f"  EVAL FAILED: val={val} forward={fwd}")
        return -999.0, -999.0, -999.0

    # Optimize for the worst case between validation and forward OOS
    score = min(val, fwd)
    return val, fwd, score


def main():
    # Backup current best
    best_content = read_strategy()
    shutil.copy2(STRATEGY_FILE, BEST_BACKUP)

    best_val, best_fwd, best_score = evaluate()
    print(f"\n{'='*60}")
    print(f"BASELINE score={best_score:.4f} (val={best_val:.4f}, forward={best_fwd:.4f})")
    print(f"{'='*60}\n")

    results_log = []
    improvements = 0

    for exp in range(1, NUM_EXPERIMENTS + 1):
        # Pick random subset of params to vary
        n_params = random.randint(*PARAMS_PER_EXP)
        params_to_vary = random.sample(list(SEARCH_SPACE.keys()), min(n_params, len(SEARCH_SPACE)))

        # Generate variation
        content = read_strategy()
        changes = {}
        for param in params_to_vary:
            old_val = get_param_value(content, param)
            new_val = random.choice(SEARCH_SPACE[param])
            content = set_param_value(content, param, new_val)
            changes[param] = (old_val, new_val)

        # Apply and evaluate
        write_strategy(content)
        t0 = time.time()
        val, fwd, score = evaluate()
        elapsed = time.time() - t0

        improved = score > best_score
        status = "IMPROVED" if improved else "no improvement"

        change_str = ", ".join(f"{k}: {v[0]}→{v[1]}" for k, v in changes.items())
        print(f"[{exp}/{NUM_EXPERIMENTS}] score={score:.4f} (val={val:.4f}, fwd={fwd:.4f}) [{status}] [{elapsed:.0f}s] | {change_str}")

        results_log.append({
            "exp": exp,
            "val": val,
            "forward": fwd,
            "score": score,
            "improved": improved,
            "changes": {k: str(v[1]) for k, v in changes.items()},
            "elapsed": round(elapsed, 1),
        })

        if improved:
            best_score = score
            best_val = val
            best_fwd = fwd
            best_content = content
            shutil.copy2(STRATEGY_FILE, BEST_BACKUP)
            improvements += 1
            print(f"  >>> NEW BEST: score={best_score:.4f} val={best_val:.4f} forward={best_fwd:.4f}")
        else:
            # Revert to best
            write_strategy(best_content)

    # Final summary
    print(f"\n{'='*60}")
    print(f"AUTORESEARCH COMPLETE")
    print(f"  Experiments:  {NUM_EXPERIMENTS}")
    print(f"  Improvements: {improvements}")
    print(f"  Best score:   {best_score:.4f}")
    print(f"  Best val:     {best_val:.4f}")
    print(f"  Best forward: {best_fwd:.4f}")
    print(f"{'='*60}")

    # Save results log
    log_path = STRATEGY_FILE.parent / "autoresearch_log.json"
    with open(log_path, "w") as f:
        json.dump(results_log, f, indent=2)
    print(f"Log saved to {log_path}")


if __name__ == "__main__":
    main()
