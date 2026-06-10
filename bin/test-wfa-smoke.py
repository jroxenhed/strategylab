#!/usr/bin/env python3
"""
test-wfa-smoke.py — Scripted C28 Walk-Forward Analysis smoke test.

Validates two WFA configs against the /api/backtest/walk_forward endpoint:
  Config A (overfit): RSI(2) extreme thresholds grid, expects wfe < 0.5 and spike tags.
  Config B (robust): 50/200 SMA crossover, expects wfe > 0.5 and stable_plateau tags.

Usage:
    python3 bin/test-wfa-smoke.py [--base-url http://127.0.0.1:8000]

Exit codes:
    0 — all assertions passed
    1 — one or more assertions failed
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from typing import Any

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="WFA smoke test")
parser.add_argument(
    "--base-url",
    default="http://127.0.0.1:8000",
    help="Base URL of the running backend (default: http://127.0.0.1:8000)",
)
args = parser.parse_args()
BASE_URL = args.base_url.rstrip("/")

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

_TIMEOUT_S = 600  # WFA can be slow on first fetch; daily configs typically <60s


def post_wfa(payload: dict) -> dict:
    """POST /api/backtest/walk_forward and return the parsed JSON response."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/backtest/walk_forward",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

class Assertion:
    def __init__(self, label: str, passed: bool, detail: str = ""):
        self.label = label
        self.passed = passed
        self.detail = detail

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        suffix = f"  ({self.detail})" if self.detail else ""
        return f"  [{status}] {self.label}{suffix}"


def check(label: str, passed: bool, detail: str = "") -> Assertion:
    return Assertion(label, passed, detail)


# ---------------------------------------------------------------------------
# Config A — Overfit (RSI(2) with 3D grid on minimal IS windows)
#
# Design goals:
#   - RSI(2) with buy_threshold × sell_threshold × stop_loss_pct grid
#     giving 5×5×4 = 100 combos per IS window.
#   - IS window = 30 bars (~6 weeks) — extremely short. With RSI(2) and many
#     combos, IS Sharpe for the winner is extreme (cherry-picked from 100 combos).
#   - OOS = 15 bars (~3 weeks) — also short; the cherry-picked combo almost never
#     repeats its luck in the immediately following 3 weeks.
#   - Expected: mean(IS_sharpe) far exceeds mean(OOS_sharpe) → wfe < 0.5
# ---------------------------------------------------------------------------

CONFIG_A = {
    "base": {
        "ticker": "AAPL",
        "start": "2020-01-01",
        "end": "2024-12-31",
        "interval": "1d",
        "source": "yahoo",
        "buy_rules": [
            {
                "indicator": "rsi",
                "condition": "below",
                "value": 10,
                "params": {"period": 2},
            }
        ],
        "sell_rules": [
            {
                "indicator": "rsi",
                "condition": "above",
                "value": 90,
                "params": {"period": 2},
            }
        ],
        "buy_logic": "AND",
        "sell_logic": "AND",
        "initial_capital": 10000,
        "stop_loss_pct": 2.0,
    },
    # 5 buy values × 5 sell values × 4 stop_loss values = 100 combos per IS window.
    # On a 30-bar IS, IS winner is cherrypicked from 100 noise outcomes.
    # stop_loss adds a non-linear regime split that rarely repeats in OOS.
    "params": [
        {
            "path": "buy_rule_0_value",
            "values": [5, 10, 15, 20, 25],
        },
        {
            "path": "sell_rule_0_value",
            "values": [75, 80, 85, 90, 95],
        },
        {
            "path": "stop_loss_pct",
            "values": [0.5, 1.5, 3.0, 5.0],
        },
    ],
    "is_bars": 30,    # extremely short IS (~6 weeks)
    "oos_bars": 15,   # very short OOS (~3 weeks)
    "gap_bars": 0,
    "step_bars": 15,
    "expand_train": False,
    "metric": "sharpe_ratio",
    "min_trades_is": 1,
}

# ---------------------------------------------------------------------------
# Config B — Robust (RSI mean-reversion with nearby-threshold grid on AAPL daily)
#
# Design goals:
#   - RSI(14) with a 5-value buy-threshold grid centred on 30 (25–35 range).
#     All values are nearby → neighbours share the same edge → stable_plateau.
#   - OOS windows long enough (100 bars ~5mo) to get trades even if RSI rarely
#     crosses the threshold; IS (350 bars ~1.5yr) gives more crossings.
#   - With period fixed at 14 and thresholds all near 30, the IS winner is robust:
#     its neighbours score similarly → stable_plateau tag expected.
#   - Expected: wfe > 0.5 and ≥1 window tagged stable_plateau
# ---------------------------------------------------------------------------

CONFIG_B = {
    "base": {
        "ticker": "AAPL",
        "start": "2019-01-01",   # extend range for more windows + trades
        "end": "2024-12-31",
        "interval": "1d",
        "source": "yahoo",
        "buy_rules": [
            {
                "indicator": "rsi",
                "condition": "below",
                "value": 30,
                "params": {"period": 14},
            }
        ],
        "sell_rules": [
            {
                "indicator": "rsi",
                "condition": "above",
                "value": 70,
                "params": {"period": 14},
            }
        ],
        "buy_logic": "AND",
        "sell_logic": "AND",
        "initial_capital": 10000,
    },
    # 5 nearby buy threshold values — tight range so IS winner's neighbours perform similarly.
    # This is the key to stable_plateau: winner (e.g. 28) and neighbours (25, 30) both work.
    "params": [
        {
            "path": "buy_rule_0_value",
            "values": [22, 25, 28, 30, 33],
        }
    ],
    "is_bars": 350,   # ~1.5yr IS — enough RSI(14) crossings for min_trades_is
    "oos_bars": 100,  # ~5mo OOS — enough crossings for OOS Sharpe
    "gap_bars": 0,
    "step_bars": 100,
    "expand_train": False,
    "metric": "sharpe_ratio",
    "min_trades_is": 1,
}


# ---------------------------------------------------------------------------
# Run and assert
# ---------------------------------------------------------------------------

def run_config(label: str, payload: dict) -> tuple[dict | None, list[Assertion]]:
    """Run one WFA config, return (response_dict, assertions)."""
    assertions: list[Assertion] = []
    print(f"\n--- {label}: posting to {BASE_URL}/api/backtest/walk_forward ---")
    t0 = time.time()
    try:
        resp = post_wfa(payload)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"  HTTP {exc.code}: {body[:500]}")
        assertions.append(check(f"{label}: HTTP request succeeds", False, f"HTTP {exc.code}"))
        return None, assertions
    except Exception as exc:
        print(f"  Request failed: {exc}")
        assertions.append(check(f"{label}: HTTP request succeeds", False, str(exc)))
        return None, assertions

    elapsed = time.time() - t0
    print(f"  Response received in {elapsed:.1f}s")
    assertions.append(check(f"{label}: HTTP request succeeds", True, f"{elapsed:.1f}s"))
    return resp, assertions


def assert_config_a(resp: dict) -> list[Assertion]:
    results: list[Assertion] = []
    label = "Config A (overfit)"

    windows = resp.get("windows", [])
    wfe = resp.get("wfe")
    low_windows_warn = resp.get("low_windows_warn", False)
    stitched = resp.get("stitched_equity", [])
    tags = [w.get("stability_tag") for w in windows]
    timed_out = resp.get("timed_out", False)

    # Print diagnostics
    print(f"\n  {label} diagnostics:")
    print(f"    windows: {len(windows)}")
    print(f"    wfe: {wfe}")
    print(f"    low_windows_warn: {low_windows_warn}")
    print(f"    timed_out: {timed_out}")
    print(f"    stability_tags: {tags}")
    print(f"    stitched_equity points: {len(stitched)}")
    if windows:
        print(f"    IS/OOS Sharpe per window:")
        for w in windows:
            is_s = w.get("is_sharpe", "?")
            oos_m = w.get("oos_metrics", {})
            oos_s = oos_m.get("sharpe_ratio") if isinstance(oos_m, dict) else getattr(oos_m, "sharpe_ratio", "?")
            tag = w.get("stability_tag", "?")
            print(f"      win {w.get('window_index','?')}: IS_sharpe={is_s:.3f}  OOS_sharpe={oos_s}  tag={tag}")

    # Assertions
    results.append(check(
        f"{label}: windows > 0",
        len(windows) > 0,
        f"{len(windows)} windows",
    ))
    results.append(check(
        f"{label}: stitched_equity non-empty",
        len(stitched) > 0,
        f"{len(stitched)} points",
    ))
    results.append(check(
        f"{label}: wfe < 0.5 (overfitting signature)",
        wfe is not None and wfe < 0.5,
        f"wfe={wfe}",
    ))
    results.append(check(
        f"{label}: at least 1 window tagged 'spike'",
        "spike" in tags,
        f"tags={tags}",
    ))
    results.append(check(
        f"{label}: low_windows_warn when configured <=5 windows",
        # Config A with 120-bar IS and 30-bar OOS on ~1258 daily bars yields ~17 windows
        # so low_windows_warn should be False; validate the response is consistent
        # (low_windows_warn iff 2 <= len(windows) < 6)
        low_windows_warn == (2 <= len(windows) < 6),
        f"low_windows_warn={low_windows_warn}, windows={len(windows)}",
    ))
    # Per-window IS/OOS divergence: at least one window should have IS >> OOS
    divergent_windows = []
    for w in windows:
        is_s = w.get("is_sharpe", 0.0) or 0.0
        oos_m = w.get("oos_metrics", {})
        if isinstance(oos_m, dict):
            oos_s = oos_m.get("sharpe_ratio") or 0.0
        else:
            oos_s = getattr(oos_m, "sharpe_ratio", 0.0) or 0.0
        if is_s > 0.3 and oos_s < is_s * 0.5:
            divergent_windows.append(w.get("window_index"))
    results.append(check(
        f"{label}: per-window IS/OOS Sharpe divergence visible (>=1 window IS>>OOS)",
        len(divergent_windows) > 0,
        f"divergent windows={divergent_windows}",
    ))

    return results


def assert_config_b(resp: dict) -> list[Assertion]:
    results: list[Assertion] = []
    label = "Config B (robust)"

    windows = resp.get("windows", [])
    wfe = resp.get("wfe")
    stitched = resp.get("stitched_equity", [])
    tags = [w.get("stability_tag") for w in windows]
    timed_out = resp.get("timed_out", False)

    # Print diagnostics
    print(f"\n  {label} diagnostics:")
    print(f"    windows: {len(windows)}")
    print(f"    wfe: {wfe}")
    print(f"    timed_out: {timed_out}")
    print(f"    stability_tags: {tags}")
    print(f"    stitched_equity points: {len(stitched)}")
    if windows:
        print(f"    IS/OOS Sharpe per window:")
        for w in windows:
            is_s = w.get("is_sharpe", "?")
            oos_m = w.get("oos_metrics", {})
            oos_s = oos_m.get("sharpe_ratio") if isinstance(oos_m, dict) else getattr(oos_m, "sharpe_ratio", "?")
            tag = w.get("stability_tag", "?")
            print(f"      win {w.get('window_index','?')}: IS_sharpe={is_s}  OOS_sharpe={oos_s}  tag={tag}")

    # Assertions
    results.append(check(
        f"{label}: windows > 0",
        len(windows) > 0,
        f"{len(windows)} windows",
    ))
    results.append(check(
        f"{label}: stitched_equity non-empty",
        len(stitched) > 0,
        f"{len(stitched)} points",
    ))
    results.append(check(
        f"{label}: wfe > 0.5 (robust strategy)",
        wfe is not None and wfe > 0.5,
        f"wfe={wfe}",
    ))
    results.append(check(
        f"{label}: at least 1 window tagged 'stable_plateau'",
        "stable_plateau" in tags,
        f"tags={tags}",
    ))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    all_assertions: list[Assertion] = []

    # --- Config A ---
    resp_a, http_a = run_config("Config A (overfit)", CONFIG_A)
    all_assertions.extend(http_a)
    if resp_a is not None:
        all_assertions.extend(assert_config_a(resp_a))

    # --- Config B ---
    resp_b, http_b = run_config("Config B (robust)", CONFIG_B)
    all_assertions.extend(http_b)
    if resp_b is not None:
        all_assertions.extend(assert_config_b(resp_b))

    # --- Summary table ---
    print("\n" + "=" * 70)
    print("WFA SMOKE TEST — RESULTS")
    print("=" * 70)
    for a in all_assertions:
        print(a)
    print("=" * 70)

    passed = sum(1 for a in all_assertions if a.passed)
    failed = sum(1 for a in all_assertions if not a.passed)
    total = len(all_assertions)
    print(f"TOTAL: {passed}/{total} passed, {failed} failed")

    if failed == 0:
        print("RESULT: PASS")
        return 0
    else:
        print("RESULT: FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
