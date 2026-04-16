# B1 — Post-Loss Behavior (Skip-After-Stop + Dynamic Sizing Trigger)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add "Skip N trades after stop" (new, independent counter) and give both it and existing Dynamic Sizing a user-selectable trigger (hard SL / trailing SL / both). Settings carry through strategy presets into bots.

**Architecture:** One helper `_is_post_loss_trigger(exit_reason, trigger)` shared by `routes/backtest.py` and `bot_runner.py`. New `SkipAfterStopConfig` on `StrategyRequest` and `BotConfig`. Existing `DynamicSizingConfig` gains a `trigger` field (default `"sl"` to preserve current backtest behavior and **fix the latent inconsistency** where `bot_runner` counts trailing-stop exits but `backtest` does not). `BotState` gains a `skip_remaining` counter. Frontend adds a settings block in `StrategyBuilder` and one trigger selector on Dynamic Sizing; `SavedStrategy` and `AddBotBar` plumb the new field.

**Tech Stack:** Python / FastAPI / Pydantic; React + TypeScript.

---

## File Structure

- **Modify** `backend/models.py` — add `SkipAfterStopConfig`, add `trigger` to `DynamicSizingConfig`, add `skip_after_stop` to `StrategyRequest`.
- **Create** `backend/post_loss.py` — tiny helper module: `is_post_loss_trigger(exit_reason, trigger) -> bool`.
- **Modify** `backend/routes/backtest.py` — use helper for both counters; add `skip_remaining` logic that blocks entries.
- **Modify** `backend/bot_manager.py` — add `skip_after_stop` to `BotConfig`; add `skip_remaining` to `BotState` (and its to_dict).
- **Modify** `backend/bot_runner.py` — mirror backtest logic; replace hard-coded `("stop_loss","trailing_stop")` check with helper.
- **Create** `backend/tests/test_post_loss.py` — unit tests for helper + backtest integration.
- **Modify** `frontend/src/shared/types/index.ts` — add `SkipAfterStopConfig`, extend `DynamicSizingConfig.trigger`, extend `StrategyRequest`, `BotConfig`, `SavedStrategy`.
- **Modify** `frontend/src/features/strategy/StrategyBuilder.tsx` — state + UI block + preset snapshot/load + persistence + request payload + trigger selector inside Dynamic Sizing.
- **Modify** `frontend/src/features/trading/AddBotBar.tsx` — pass `skip_after_stop` into `BotConfig` payload.

---

## Task 1: Backend models

**Files:**
- Modify: `backend/models.py`

- [ ] **Step 1: Add `SkipAfterStopConfig` and extend `DynamicSizingConfig`**

Replace the existing `DynamicSizingConfig` block and add the new model above `StrategyRequest`:

```python
class DynamicSizingConfig(BaseModel):
    enabled: bool = False
    consec_sls: int = 2             # number of consecutive qualifying stops before reducing size
    reduced_pct: float = 25.0       # position size % to use when triggered
    trigger: str = "sl"             # "sl" | "tsl" | "both" — which exit reason(s) increment the counter


class SkipAfterStopConfig(BaseModel):
    enabled: bool = False
    count: int = 1                  # number of entries to skip after a qualifying stop
    trigger: str = "sl"             # "sl" | "tsl" | "both"
```

- [ ] **Step 2: Add `skip_after_stop` field to `StrategyRequest`**

In `StrategyRequest`, next to `dynamic_sizing`:

```python
    dynamic_sizing: Optional[DynamicSizingConfig] = None
    skip_after_stop: Optional[SkipAfterStopConfig] = None
```

- [ ] **Step 3: Commit**

```bash
git add backend/models.py
git commit -m "feat(B1): add SkipAfterStopConfig and dynamic-sizing trigger field"
```

---

## Task 2: Shared trigger helper + tests

**Files:**
- Create: `backend/post_loss.py`
- Create: `backend/tests/test_post_loss.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_post_loss.py`:

```python
from sys import path as sys_path
from os.path import dirname, abspath
sys_path.insert(0, dirname(dirname(abspath(__file__))))

from post_loss import is_post_loss_trigger


def test_sl_only_counts_hard_stop():
    assert is_post_loss_trigger("stop_loss", "sl") is True
    assert is_post_loss_trigger("trailing_stop", "sl") is False
    assert is_post_loss_trigger("signal", "sl") is False


def test_tsl_only_counts_trailing():
    assert is_post_loss_trigger("stop_loss", "tsl") is False
    assert is_post_loss_trigger("trailing_stop", "tsl") is True
    assert is_post_loss_trigger("signal", "tsl") is False


def test_both_counts_either_stop():
    assert is_post_loss_trigger("stop_loss", "both") is True
    assert is_post_loss_trigger("trailing_stop", "both") is True
    assert is_post_loss_trigger("signal", "both") is False


def test_unknown_trigger_defaults_to_sl():
    assert is_post_loss_trigger("stop_loss", "garbage") is True
    assert is_post_loss_trigger("trailing_stop", "garbage") is False
```

- [ ] **Step 2: Run — expect failure**

```
cd backend && python -m pytest tests/test_post_loss.py -v
```
Expected: `ModuleNotFoundError: No module named 'post_loss'`.

- [ ] **Step 3: Create helper**

Create `backend/post_loss.py`:

```python
"""Post-loss behavior helpers. Shared by backtester and bot_runner."""


def is_post_loss_trigger(exit_reason: str, trigger: str) -> bool:
    """Return True if this exit_reason should count toward post-loss counters
    (skip-after-stop and dynamic sizing) under the given trigger setting.

    trigger: "sl" → only hard stop-loss exits
             "tsl" → only trailing-stop exits
             "both" → either
    Unknown trigger values fall back to "sl"."""
    if trigger == "both":
        return exit_reason in ("stop_loss", "trailing_stop")
    if trigger == "tsl":
        return exit_reason == "trailing_stop"
    # default / "sl"
    return exit_reason == "stop_loss"
```

- [ ] **Step 4: Run tests — expect pass**

```
cd backend && python -m pytest tests/test_post_loss.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/post_loss.py backend/tests/test_post_loss.py
git commit -m "feat(B1): shared post-loss trigger helper + tests"
```

---

## Task 3: Backtester integration

**Files:**
- Modify: `backend/routes/backtest.py`

Context anchors: `consec_sl_count = 0` (around line 107), `if ds and ds.enabled and consec_sl_count >= ds.consec_sls` (around line 170), `if exit_reason == "stop_loss":` (around line 286).

- [ ] **Step 1: Import helper**

Near the other local imports at the top of `routes/backtest.py`:

```python
from post_loss import is_post_loss_trigger
```

- [ ] **Step 2: Add skip-counter state**

Grep anchor: `consec_sl_count = 0  # track consecutive stop losses for dynamic sizing`
Add directly below:

```python
        sas = req.skip_after_stop
        skip_remaining = 0  # entries still to skip after a qualifying stop
```

- [ ] **Step 3: Gate entries on skip_remaining**

Grep anchor: `if position == 0 and hour_ok and eval_rules(req.buy_rules, req.buy_logic, indicators, i):`

Replace that single condition line with:

```python
            buy_fires = position == 0 and hour_ok and eval_rules(req.buy_rules, req.buy_logic, indicators, i)
            if buy_fires and skip_remaining > 0:
                skip_remaining -= 1
                if signal_trace is not None:
                    signal_trace.append({
                        "date": date, "price": round(price, 4), "position": "flat",
                        "action": f"SKIPPED (post-stop, {skip_remaining} left)",
                    })
                buy_fires = False

            if buy_fires:
```

(The existing body inside the old `if` now runs under `if buy_fires:` — indentation unchanged.)

- [ ] **Step 4: Make dynamic sizing honor configurable trigger**

Grep anchor: `if ds and ds.enabled and consec_sl_count >= ds.consec_sls:`

No change to this line — `consec_sl_count` increment logic is what moves (next step).

- [ ] **Step 5: Update exit-side counter bookkeeping**

Grep anchor (inside the exit block):

```python
                    if exit_reason == "stop_loss":
                        consec_sl_count += 1
                    else:
                        consec_sl_count = 0
```

Replace with:

```python
                    ds_trigger = ds.trigger if ds else "sl"
                    if is_post_loss_trigger(exit_reason, ds_trigger):
                        consec_sl_count += 1
                    else:
                        consec_sl_count = 0

                    if sas and sas.enabled and is_post_loss_trigger(exit_reason, sas.trigger):
                        skip_remaining = sas.count
```

- [ ] **Step 6: Add integration tests**

Append to `backend/tests/test_post_loss.py`:

```python
import pandas as pd
from models import StrategyRequest, SkipAfterStopConfig, DynamicSizingConfig
from signal_engine import Rule


def _synthetic_df(prices):
    idx = pd.date_range("2024-01-02", periods=len(prices), freq="B")
    df = pd.DataFrame({
        "Open": prices, "High": prices, "Low": prices, "Close": prices, "Volume": [1_000_000] * len(prices),
    }, index=idx)
    return df


def _run(req, prices, monkeypatch):
    """Drive the backtest against a synthetic price series."""
    from routes import backtest as bt
    df = _synthetic_df(prices)
    monkeypatch.setattr(bt, "_fetch", lambda *a, **k: df)
    return bt.run_backtest(req)


def _always_buy_rule():
    return Rule(indicator="price", condition="above", value=0)


def _never_sell_rule():
    return Rule(indicator="price", condition="below", value=0)


def test_skip_after_stop_blocks_n_entries(monkeypatch):
    # Price rises, then crashes through a 5% stop, then rises again.
    # With skip=2 we expect the first 2 post-stop entries to be suppressed.
    prices = [100, 101, 102, 90, 91, 92, 93, 94, 95, 96]
    req = StrategyRequest(
        ticker="X",
        buy_rules=[_always_buy_rule()],
        sell_rules=[_never_sell_rule()],
        stop_loss_pct=5.0,
        position_size=1.0,
        initial_capital=10_000,
        skip_after_stop=SkipAfterStopConfig(enabled=True, count=2, trigger="sl"),
    )
    result = _run(req, prices, monkeypatch)
    entries = [t for t in result["trades"] if t["type"] in ("buy", "short")]
    # First entry at bar 0, stop at bar 3, then 2 entries skipped, next entry at bar 6.
    entry_dates = [t["date"] for t in entries]
    assert len(entries) == 2
    assert entry_dates[0] == result["trades"][0]["date"]  # original entry
```

(If the existing backtest entry function is not named `run_backtest`, grep `backend/routes/backtest.py` for `def ` and adjust the import in this test.)

- [ ] **Step 7: Run tests**

```
cd backend && python -m pytest tests/test_post_loss.py -v
```
Expected: all pass (including the synthetic backtest test). If the synthetic test fails due to engine API mismatch, adjust the test to match the actual entrypoint — do **not** relax the behavior assertion.

- [ ] **Step 8: Commit**

```bash
git add backend/routes/backtest.py backend/tests/test_post_loss.py
git commit -m "feat(B1): backtester honors skip-after-stop + configurable dynamic-sizing trigger"
```

---

## Task 4: Bot config/state

**Files:**
- Modify: `backend/bot_manager.py`

- [ ] **Step 1: Add import for new config**

Grep anchor: `from models import` in `bot_manager.py`. Add `SkipAfterStopConfig` to that import.

- [ ] **Step 2: Add `skip_after_stop` to `BotConfig`**

Grep anchor: `dynamic_sizing: Optional[DynamicSizingConfig] = None` in `bot_manager.py`.

Add directly below:

```python
    skip_after_stop: Optional[SkipAfterStopConfig] = None
```

- [ ] **Step 3: Add `skip_remaining` to `BotState`**

Grep anchor: `consec_sl_count: int = 0` in `bot_manager.py`.

Add directly below:

```python
    skip_remaining: int = 0           # entries remaining to skip after a qualifying stop
```

- [ ] **Step 4: Persist `skip_remaining` in `to_dict`**

Grep anchor: `"consec_sl_count": self.consec_sl_count,` in `bot_manager.py`.

Add directly below:

```python
            "skip_remaining": self.skip_remaining,
```

(`from_dict` iterates `d.items()` and sets any matching attr, so no change needed there.)

- [ ] **Step 5: Commit**

```bash
git add backend/bot_manager.py
git commit -m "feat(B1): BotConfig.skip_after_stop + BotState.skip_remaining"
```

---

## Task 5: Bot runner integration

**Files:**
- Modify: `backend/bot_runner.py`

Context anchors: `state.consec_sl_count += 1` appears twice (around lines 186 and 423) inside exit-handling blocks; dynamic sizing check around line 237.

- [ ] **Step 1: Import helper**

Top of `backend/bot_runner.py`:

```python
from post_loss import is_post_loss_trigger
```

- [ ] **Step 2: Replace both exit-side counter updates**

Grep every occurrence of:

```python
                if exit_reason in ("stop_loss", "trailing_stop"):
                    state.consec_sl_count += 1
                else:
                    state.consec_sl_count = 0
```

Replace each with:

```python
                ds_trigger = cfg.dynamic_sizing.trigger if cfg.dynamic_sizing else "sl"
                if is_post_loss_trigger(exit_reason, ds_trigger):
                    state.consec_sl_count += 1
                else:
                    state.consec_sl_count = 0

                if cfg.skip_after_stop and cfg.skip_after_stop.enabled and \
                        is_post_loss_trigger(exit_reason, cfg.skip_after_stop.trigger):
                    state.skip_remaining = cfg.skip_after_stop.count
```

**Note:** this is a behavior change for bots that had `dynamic_sizing` enabled — previously the bot counted both hard and trailing stops, now it honors the configured trigger (default `"sl"` for parity with the backtester). Call this out in the commit message.

- [ ] **Step 3: Gate entry on `skip_remaining`**

Grep anchor: `if buy_signal:` in `bot_runner.py`.

Insert directly after that line, before the existing body:

```python
                if state.skip_remaining > 0:
                    state.skip_remaining -= 1
                    self._log("INFO", f"Skipping entry (post-stop cooldown, {state.skip_remaining} left)")
                    return
```

- [ ] **Step 4: Smoke test**

```
cd backend && python -m pytest tests/ -v
```
Expected: all tests pass (no new tests added here — bot_runner is covered indirectly by the shared helper test and by manual verification).

- [ ] **Step 5: Commit**

```bash
git add backend/bot_runner.py
git commit -m "feat(B1): bot_runner skip-after-stop + configurable DS trigger

Behavior change: existing dynamic-sizing bots previously counted both hard
and trailing stops. They now honor the configured trigger (default 'sl'
matching the backtester). Users who relied on the old behavior must set
trigger='both' on their bots after upgrade."
```

---

## Task 6: Frontend types

**Files:**
- Modify: `frontend/src/shared/types/index.ts`

Context anchors: `export interface DynamicSizingConfig` (around line 76), `dynamicSizing: DynamicSizingConfig` in `SavedStrategy` (around line 136), `dynamic_sizing?: DynamicSizingConfig` in `StrategyRequest` (around line 102) and `BotConfig` (around line 275).

- [ ] **Step 1: Extend `DynamicSizingConfig`**

Replace the interface body:

```typescript
export interface DynamicSizingConfig {
  enabled: boolean
  consec_sls: number      // consecutive qualifying stops before reducing size
  reduced_pct: number     // position size % to use when triggered
  trigger?: 'sl' | 'tsl' | 'both'  // default 'sl'
}
```

- [ ] **Step 2: Add `SkipAfterStopConfig` below it**

```typescript
export interface SkipAfterStopConfig {
  enabled: boolean
  count: number
  trigger: 'sl' | 'tsl' | 'both'
}
```

- [ ] **Step 3: Add `skip_after_stop` to `StrategyRequest` and `BotConfig`**

Grep for each `dynamic_sizing?: DynamicSizingConfig` occurrence and add below it:

```typescript
  skip_after_stop?: SkipAfterStopConfig
```

- [ ] **Step 4: Add to `SavedStrategy`**

Grep anchor: `dynamicSizing: DynamicSizingConfig` inside `SavedStrategy`.

Add below:

```typescript
  skipAfterStop?: SkipAfterStopConfig
```

(Optional so existing saved presets load without migration — default is applied in StrategyBuilder.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/types/index.ts
git commit -m "feat(B1): frontend types — SkipAfterStopConfig + DS trigger"
```

---

## Task 7: Strategy builder UI

**Files:**
- Modify: `frontend/src/features/strategy/StrategyBuilder.tsx`

Context anchors: import line (line 4); `useState<DynamicSizingConfig>` (line 66); `currentSnapshot` (line 103); `loadSavedStrategy` (line 125); persistence `useEffect` (line 157); `runBacktest`'s `dynamic_sizing: dynamicSizing.enabled ? dynamicSizing : undefined,` (line 180); dynamic-sizing UI (lines 312–331).

- [ ] **Step 1: Import new type**

Grep anchor in `StrategyBuilder.tsx`:

```typescript
import type { Rule, StrategyRequest, BacktestResult, DataSource, TrailingStopConfig, DynamicSizingConfig, TradingHoursConfig, SavedStrategy } from '../../shared/types'
```

Add `SkipAfterStopConfig` to the list.

- [ ] **Step 2: Add state**

Grep anchor: `const [dynamicSizing, setDynamicSizing] = useState<DynamicSizingConfig>(saved?.dynamicSizing ?? { enabled: false, consec_sls: 2, reduced_pct: 25 })`

Change the default to include `trigger` and add a sibling line:

```typescript
  const [dynamicSizing, setDynamicSizing] = useState<DynamicSizingConfig>(saved?.dynamicSizing ?? { enabled: false, consec_sls: 2, reduced_pct: 25, trigger: 'sl' })
  const [skipAfterStop, setSkipAfterStop] = useState<SkipAfterStopConfig>(saved?.skipAfterStop ?? { enabled: false, count: 1, trigger: 'sl' })
```

- [ ] **Step 3: Include in snapshot, load, persistence, request**

Grep anchor: `trailingEnabled, trailingConfig, dynamicSizing, tradingHours,` in `currentSnapshot`.

Add `skipAfterStop,` after `dynamicSizing,`.

Grep anchor in `loadSavedStrategy`:

```typescript
    setDynamicSizing(s.dynamicSizing); setTradingHours(s.tradingHours)
```

Replace with:

```typescript
    setDynamicSizing(s.dynamicSizing ?? { enabled: false, consec_sls: 2, reduced_pct: 25, trigger: 'sl' })
    setSkipAfterStop(s.skipAfterStop ?? { enabled: false, count: 1, trigger: 'sl' })
    setTradingHours(s.tradingHours)
```

Grep anchor: the localStorage persistence `useEffect` (around line 157). In both the body and the dep array, add `skipAfterStop` alongside `dynamicSizing`.

Grep anchor in `runBacktest`: `dynamic_sizing: dynamicSizing.enabled ? dynamicSizing : undefined,`

Add directly below:

```typescript
        skip_after_stop: skipAfterStop.enabled ? skipAfterStop : undefined,
```

- [ ] **Step 4: Add trigger selector to existing Dynamic Sizing block**

Grep anchor: `<span style={{ fontSize: 11, color: '#8b949e' }}>% size</span>` (closing span of the `reduced_pct` row).

Add a new row immediately after its parent `<div style={styles.settingsRow}>` closes (still inside the `{dynamicSizing.enabled && (...)}` block):

```tsx
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Trigger</label>
                <select
                  value={dynamicSizing.trigger ?? 'sl'}
                  onChange={e => setDynamicSizing(c => ({ ...c, trigger: e.target.value as 'sl' | 'tsl' | 'both' }))}
                  style={{ ...styles.settingsInput, width: 80 }}
                >
                  <option value="sl">Hard SL</option>
                  <option value="tsl">Trailing</option>
                  <option value="both">Both</option>
                </select>
              </div>
```

- [ ] **Step 5: Add Skip-After-Stop block**

Directly below the closing `)}` of the `{dynamicSizing.enabled && (...)}` block, insert:

```tsx
          <div style={{ ...styles.settingsRow, marginTop: 4 }}>
            <label style={{ ...styles.settingsLabel, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={skipAfterStop.enabled} onChange={e => setSkipAfterStop(c => ({ ...c, enabled: e.target.checked }))} />
              Skip After Stop
            </label>
          </div>
          {skipAfterStop.enabled && (
            <div style={{ paddingLeft: 12, borderLeft: '2px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Skip</label>
                <input type="number" value={skipAfterStop.count} step={1} min={1} max={20} onChange={e => setSkipAfterStop(c => ({ ...c, count: +e.target.value }))} style={{ ...styles.settingsInput, width: 40 }} />
                <span style={{ fontSize: 11, color: '#8b949e' }}>entries</span>
              </div>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Trigger</label>
                <select
                  value={skipAfterStop.trigger}
                  onChange={e => setSkipAfterStop(c => ({ ...c, trigger: e.target.value as 'sl' | 'tsl' | 'both' }))}
                  style={{ ...styles.settingsInput, width: 80 }}
                >
                  <option value="sl">Hard SL</option>
                  <option value="tsl">Trailing</option>
                  <option value="both">Both</option>
                </select>
              </div>
            </div>
          )}
```

- [ ] **Step 6: Verify typecheck**

```
cd frontend && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/shared/types/index.ts frontend/src/features/strategy/StrategyBuilder.tsx
git commit -m "feat(B1): StrategyBuilder — skip-after-stop block + DS trigger selector"
```

---

## Task 8: Bot creation plumbing

**Files:**
- Modify: `frontend/src/features/trading/AddBotBar.tsx`

Context anchor: `dynamic_sizing: s.dynamicSizing ?? null,` (line 81).

- [ ] **Step 1: Pass `skip_after_stop` through**

Add directly below that line:

```typescript
        skip_after_stop: s.skipAfterStop ?? null,
```

- [ ] **Step 2: Typecheck**

```
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/trading/AddBotBar.tsx
git commit -m "feat(B1): AddBotBar passes skip_after_stop from preset into BotConfig"
```

---

## Task 9: End-to-end manual verification

- [ ] **Step 1: Start servers**

```
./start.sh
```

- [ ] **Step 2: Backtest check**

Open `http://localhost:5173`. On a symbol with frequent stops (e.g. TSLA 1h over a down-trending window), enable a hard stop-loss, then enable "Skip After Stop" = 2 / trigger "sl". Run backtest twice (enabled vs disabled) and confirm:
- With skip enabled, trade count drops and the equity curve skips entries right after each stop-loss exit.
- Results tab's Trades list shows a gap of ≥2 missed buy-signal bars after each stop-loss.

- [ ] **Step 3: Preset roundtrip**

Save the strategy, load it back. Confirm the Skip-After-Stop block restores correctly (enabled, count, trigger). Create a bot from this preset via AddBotBar. Fetch the bot via the API (`GET /api/bots`) and confirm `skip_after_stop` is present on the returned config.

- [ ] **Step 4: Done — no commit**

Manual verification only.

---

## Self-Review Notes

- **Spec coverage:** skip-N (Task 3/5), dynamic-sizing scale-back already existed (untouched except trigger field), user-selectable trigger (Tasks 1, 3, 5, 7), preset + bot carry-through (Tasks 6, 7, 8).
- **No placeholders:** every code block is concrete.
- **Naming consistency:** Python `skip_after_stop` / `SkipAfterStopConfig` ↔ TS `skipAfterStop` / `SkipAfterStopConfig` (camelCase in `SavedStrategy`, snake_case on API payloads, matching the existing pattern for `dynamicSizing` ↔ `dynamic_sizing`).
- **Known behavior change:** `bot_runner`'s dynamic sizing previously counted both hard and trailing stops. After this change, the default `trigger="sl"` matches the backtester. Flagged in the Task 5 commit message.
