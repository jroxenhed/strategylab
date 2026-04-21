# B12: Parameterized Moving Average Rules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 5 hardcoded MA indicators (ema20, ema50, ema200, ma8, ma21) with a single generic `ma(period, type)` rule indicator, and remove all Savitzky-Golay smoothing code.

**Architecture:** Add a `params` dict to the Rule model (both Python and TypeScript). Signal engine becomes rule-driven — scans rules to determine which MAs to compute instead of computing a fixed set. Frontend collapses 5 MA indicator dropdown entries into one "MA" entry with inline period/type inputs. Migration is idempotent for both localStorage strategies and bots.json.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript (frontend), lightweight-charts, pandas

**Spec:** `docs/superpowers/specs/2026-04-21-b12-parameterized-ma-rules-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `backend/signal_engine.py` | Rule model `params` field, rule-driven `compute_indicators`, `resolve_series`/`resolve_ref`, S-G removal |
| Modify | `backend/models.py` | Remove S-G fields from `StrategyRequest` |
| Modify | `backend/bot_manager.py` | `migrate_rule()` + apply on `load()` |
| Modify | `backend/routes/backtest.py` | Pass rules to `compute_indicators`, remove S-G params, remove `_trace_series_map`, apply `migrate_rule` to ALL rule references, fix `ema_overlays` indicator check |
| Modify | `backend/routes/trading.py` | Pass rules to `compute_indicators`, fix hardcoded `ema50` reference inline, apply `migrate_rule` |
| Modify | `backend/bot_runner.py` | Pass rules to `compute_indicators` |
| Modify | `frontend/src/shared/types/index.ts` | Add `params` to Rule, remove S-G fields from StrategySettings |
| Modify | `frontend/src/features/strategy/RuleRow.tsx` | Collapse MA indicators, inline period/type inputs, dynamic param picker |
| Modify | `frontend/src/features/strategy/StrategyBuilder.tsx` | `migrateRule()`, remove S-G serialization, remove `maSettings` prop |
| Modify | `frontend/src/App.tsx` | Remove `MASettings` interface, `DEFAULT_MA_SETTINGS`, `maSettings` state + prop |
| Create | `backend/tests/test_signal_engine.py` | Tests for migration, rule-driven compute, resolve functions |

---

## Task 1: Backend Rule Model — Add `params` Field

**Files:**
- Modify: `backend/signal_engine.py:8-15` (Rule class)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_signal_engine.py`:

```python
import pytest
from signal_engine import Rule


def test_rule_params_field_default_none():
    rule = Rule(indicator="ma", condition="turns_up")
    assert rule.params is None


def test_rule_params_field_with_ma_config():
    rule = Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})
    assert rule.params == {"period": 20, "type": "ema"}


def test_rule_params_field_ignored_for_non_ma():
    rule = Rule(indicator="rsi", condition="above", value=70)
    assert rule.params is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_signal_engine.py -v`
Expected: FAIL — `Rule` doesn't accept `params` keyword argument

- [ ] **Step 3: Add `params` field to Rule**

In `backend/signal_engine.py`, change the Rule class to:

```python
class Rule(BaseModel):
    indicator: str
    condition: str
    value: Optional[float] = None
    param: Optional[str] = None
    threshold: Optional[float] = None
    muted: bool = False
    negated: bool = False
    params: Optional[dict] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_signal_engine.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/signal_engine.py backend/tests/test_signal_engine.py
git commit -m "feat(B12): add params field to Rule model"
```

---

## Task 2: Backend Migration Function

**Files:**
- Modify: `backend/signal_engine.py` (add `migrate_rule` near Rule class)
- Modify: `backend/tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_signal_engine.py`:

```python
from signal_engine import migrate_rule


def test_migrate_rule_ema20():
    old = Rule(indicator="ema20", condition="above", value=150)
    new = migrate_rule(old)
    assert new.indicator == "ma"
    assert new.params == {"period": 20, "type": "ema"}
    assert new.condition == "above"
    assert new.value == 150


def test_migrate_rule_ma8():
    old = Rule(indicator="ma8", condition="turns_up")
    new = migrate_rule(old)
    assert new.indicator == "ma"
    assert new.params == {"period": 8, "type": "sma"}


def test_migrate_rule_ema200():
    old = Rule(indicator="ema200", condition="rising")
    new = migrate_rule(old)
    assert new.indicator == "ma"
    assert new.params == {"period": 200, "type": "ema"}


def test_migrate_rule_param_ema50():
    old = Rule(indicator="price", condition="crosses_above", param="ema50")
    new = migrate_rule(old)
    assert new.indicator == "price"
    assert new.param == "ma:50:ema"


def test_migrate_rule_param_ma21():
    old = Rule(indicator="ma8", condition="above", param="ma21")
    new = migrate_rule(old)
    assert new.indicator == "ma"
    assert new.params == {"period": 8, "type": "sma"}
    assert new.param == "ma:21:sma"


def test_migrate_rule_idempotent():
    already_new = Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})
    result = migrate_rule(already_new)
    assert result.indicator == "ma"
    assert result.params == {"period": 20, "type": "ema"}


def test_migrate_rule_non_ma_unchanged():
    rule = Rule(indicator="rsi", condition="above", value=70)
    result = migrate_rule(rule)
    assert result.indicator == "rsi"
    assert result.params is None
    assert result.param is None


def test_migrate_rule_param_close_unchanged():
    rule = Rule(indicator="price", condition="above", param="close")
    result = migrate_rule(rule)
    assert result.param == "close"


def test_migrate_rule_param_signal_unchanged():
    rule = Rule(indicator="macd", condition="crossover_up", param="signal")
    result = migrate_rule(rule)
    assert result.param == "signal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_signal_engine.py -v -k migrate`
Expected: FAIL — `migrate_rule` not importable

- [ ] **Step 3: Implement `migrate_rule`**

Add to `backend/signal_engine.py` right after the `Rule` class:

```python
_MA_MIGRATION: dict[str, dict] = {
    "ema20":  {"period": 20,  "type": "ema"},
    "ema50":  {"period": 50,  "type": "ema"},
    "ema200": {"period": 200, "type": "ema"},
    "ma8":    {"period": 8,   "type": "sma"},
    "ma21":   {"period": 21,  "type": "sma"},
}

_PARAM_MIGRATION: dict[str, str] = {
    "ema20":  "ma:20:ema",
    "ema50":  "ma:50:ema",
    "ema200": "ma:200:ema",
    "ma8":    "ma:8:sma",
    "ma21":   "ma:21:sma",
}


def migrate_rule(rule: Rule) -> Rule:
    """Convert legacy hardcoded MA indicators to generic ma(period, type). Idempotent."""
    data = rule.model_dump()
    ma_spec = _MA_MIGRATION.get(rule.indicator)
    if ma_spec:
        data["indicator"] = "ma"
        data["params"] = ma_spec
    if rule.param and rule.param in _PARAM_MIGRATION:
        data["param"] = _PARAM_MIGRATION[rule.param]
    return Rule(**data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_signal_engine.py -v`
Expected: PASS (all 12 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/signal_engine.py backend/tests/test_signal_engine.py
git commit -m "feat(B12): add migrate_rule for legacy MA indicator conversion"
```

---

## Task 3: Rule-Driven `compute_indicators` + Resolve Functions

**Files:**
- Modify: `backend/signal_engine.py:79-118` (`compute_indicators`), lines 128-146 (`series_map`/`ref_map` in `eval_rule`)
- Modify: `backend/tests/test_signal_engine.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_signal_engine.py`:

```python
import pandas as pd
from signal_engine import compute_indicators, resolve_series, resolve_ref


def _make_close(n=100):
    """Generate a simple close price series for testing."""
    return pd.Series([100.0 + i * 0.1 for i in range(n)])


def test_compute_indicators_with_ma_rules():
    close = _make_close()
    rules = [Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})]
    indicators = compute_indicators(close, rules=rules)
    assert "ma_20_ema" in indicators
    assert len(indicators["ma_20_ema"]) == len(close)


def test_compute_indicators_with_param_ref():
    close = _make_close()
    rules = [Rule(indicator="price", condition="crosses_above", param="ma:50:sma")]
    indicators = compute_indicators(close, rules=rules)
    assert "ma_50_sma" in indicators


def test_compute_indicators_deduplicates_ma_specs():
    close = _make_close()
    rules = [
        Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"}),
        Rule(indicator="ma", condition="above", params={"period": 20, "type": "ema"}, param="ma:50:ema"),
    ]
    indicators = compute_indicators(close, rules=rules)
    assert "ma_20_ema" in indicators
    assert "ma_50_ema" in indicators


def test_compute_indicators_always_has_macd_rsi():
    close = _make_close()
    rules = []
    indicators = compute_indicators(close, rules=rules)
    assert "macd" in indicators
    assert "signal" in indicators
    assert "rsi" in indicators
    assert "close" in indicators


def test_resolve_series_ma():
    rule = Rule(indicator="ma", condition="turns_up", params={"period": 20, "type": "ema"})
    indicators = {"ma_20_ema": pd.Series([1, 2, 3])}
    result = resolve_series(rule, indicators)
    assert list(result) == [1, 2, 3]


def test_resolve_series_fixed():
    for ind, key in [("macd", "macd"), ("rsi", "rsi"), ("price", "close")]:
        rule = Rule(indicator=ind, condition="above", value=50)
        indicators = {key: pd.Series([10, 20])}
        result = resolve_series(rule, indicators)
        assert result is not None


def test_resolve_ref_ma_encoded():
    rule = Rule(indicator="price", condition="above", param="ma:50:ema")
    indicators = {"ma_50_ema": pd.Series([100, 200])}
    result = resolve_ref(rule, indicators)
    assert list(result) == [100, 200]


def test_resolve_ref_signal():
    rule = Rule(indicator="macd", condition="crossover_up", param="signal")
    indicators = {"signal": pd.Series([1, 2])}
    assert resolve_ref(rule, indicators) is not None


def test_resolve_ref_close():
    rule = Rule(indicator="ma", condition="above", params={"period": 20, "type": "ema"}, param="close")
    indicators = {"close": pd.Series([100])}
    assert resolve_ref(rule, indicators) is not None


def test_resolve_ref_none_when_no_param():
    rule = Rule(indicator="rsi", condition="above", value=70)
    assert resolve_ref(rule, {}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_signal_engine.py -v -k "compute_indicators or resolve"`
Expected: FAIL — `compute_indicators` doesn't accept `rules` kwarg, `resolve_series`/`resolve_ref` don't exist

- [ ] **Step 3: Rewrite `compute_indicators` to be rule-driven**

Replace `compute_indicators` (lines 79-118) in `backend/signal_engine.py` with:

```python
def compute_indicators(close: pd.Series, high: pd.Series = None, low: pd.Series = None,
                       rules: list[Rule] = None) -> dict[str, pd.Series]:
    """Compute indicators based on what rules require. MACD/RSI always included."""
    from indicators import compute_instance, OHLCVSeries

    if rules is None:
        rules = []

    ohlcv = OHLCVSeries(close=close, high=high if high is not None else close,
                        low=low if low is not None else close, volume=pd.Series(dtype=float))

    macd_result = compute_instance("macd", {"fast": 12, "slow": 26, "signal": 9}, ohlcv)
    rsi_result = compute_instance("rsi", {"period": 14}, ohlcv)

    result = {
        "macd": macd_result["macd"],
        "signal": macd_result["signal"],
        "histogram": macd_result["histogram"],
        "rsi": rsi_result["rsi"],
        "close": close,
    }

    if high is not None and low is not None:
        atr_result = compute_instance("atr", {"period": 14}, ohlcv)
        result["atr"] = atr_result["atr"]

    ma_specs: set[tuple[int, str]] = set()
    for rule in rules:
        if rule.indicator == "ma" and rule.params:
            ma_specs.add((rule.params["period"], rule.params.get("type", "ema")))
        if rule.param and rule.param.startswith("ma:"):
            parts = rule.param.split(":", 2)
            if len(parts) != 3:
                continue
            _, period, ma_type = parts
            ma_specs.add((int(period), ma_type))

    for period, ma_type in ma_specs:
        key = f"ma_{period}_{ma_type}"
        ma_result = compute_instance("ma", {"period": period, "type": ma_type}, ohlcv)
        result[key] = ma_result["ma"]

    return result
```

- [ ] **Step 4: Add `resolve_series` and `resolve_ref` functions**

Add right after `compute_indicators` in `backend/signal_engine.py`:

```python
def resolve_series(rule: Rule, indicators: dict[str, pd.Series]) -> pd.Series | None:
    """Resolve the primary series for a rule's indicator."""
    if rule.indicator == "ma" and rule.params:
        key = f"ma_{rule.params['period']}_{rule.params.get('type', 'ema')}"
        return indicators.get(key)
    fixed = {"macd": "macd", "rsi": "rsi", "price": "close"}
    return indicators.get(fixed.get(rule.indicator, rule.indicator))


def resolve_ref(rule: Rule, indicators: dict[str, pd.Series]) -> pd.Series | None:
    """Resolve the cross-reference series for a rule's param."""
    if not rule.param:
        return None
    if rule.param == "signal":
        return indicators.get("signal")
    if rule.param == "close":
        return indicators.get("close")
    if rule.param.startswith("ma:"):
        parts = rule.param.split(":", 2)
        if len(parts) == 3:
            _, period, ma_type = parts
            try:
                key = f"ma_{int(period)}_{ma_type}"
                return indicators.get(key)
            except ValueError:
                return None
    return None
```

- [ ] **Step 5: Update `eval_rule` to use resolve functions**

In `eval_rule` (around line 121), replace the `series_map` and `ref_map` dicts (lines 128-146) and the `s = series_map.get(ind)` lookup with:

```python
    s = resolve_series(rule, indicators)
    if s is None:
        return False

    v_now = s.iloc[i]
    v_prev = s.iloc[i - 1]
```

And replace all `ref_map[rule.param]` / `ref_map.get(rule.param)` references in the condition branches with `resolve_ref(rule, indicators)`. Specifically, in every condition that checks `rule.param`:

```python
    if cond in ("crossover_up", "crosses_above"):
        ref = resolve_ref(rule, indicators) if rule.param else None
        if ref is not None:
            return v_prev < ref.iloc[i - 1] and v_now >= ref.iloc[i]
        elif rule.value is not None:
            return v_prev < rule.value <= v_now
    elif cond in ("crossover_down", "crosses_below"):
        ref = resolve_ref(rule, indicators) if rule.param else None
        if ref is not None:
            return v_prev > ref.iloc[i - 1] and v_now <= ref.iloc[i]
        elif rule.value is not None:
            return v_prev > rule.value >= v_now
    elif cond == "above":
        ref = resolve_ref(rule, indicators) if rule.param else None
        if ref is not None:
            return v_now > ref.iloc[i]
        elif rule.value is not None:
            return v_now > rule.value
    elif cond == "below":
        ref = resolve_ref(rule, indicators) if rule.param else None
        if ref is not None:
            return v_now < ref.iloc[i]
        elif rule.value is not None:
            return v_now < rule.value
```

**Key change from current code:** `ref` is resolved first, then if it's None (unrecognized param OR no param), falls through to the `rule.value` branch — preserving current behavior where an unrecognized param doesn't prevent value comparison.

**Important:** Preserve all other conditions (turns_up, turns_down, rising, falling, etc.) as-is — they don't use `ref_map`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_signal_engine.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add backend/signal_engine.py backend/tests/test_signal_engine.py
git commit -m "feat(B12): rule-driven compute_indicators + resolve functions"
```

---

## Task 4: Remove All S-G Smoothing Code

**Files:**
- Modify: `backend/signal_engine.py` (delete S-G functions, remove S-G dead zone in turns_up/turns_down)
- Modify: `backend/models.py:60-66` (remove S-G fields from StrategyRequest)
- Modify: `backend/tests/test_signal_engine.py`

- [ ] **Step 1: Write a test confirming S-G is gone**

Append to `backend/tests/test_signal_engine.py`:

```python
def test_no_sg_active_in_indicators():
    close = _make_close()
    rules = [Rule(indicator="ma", condition="turns_up", params={"period": 8, "type": "sma"})]
    indicators = compute_indicators(close, rules=rules)
    assert "_sg_active" not in indicators
    assert "ma8_sg" not in indicators
    assert "ma21_sg" not in indicators
```

- [ ] **Step 2: Run test — should already pass (compute_indicators rewritten in Task 3)**

Run: `cd backend && python -m pytest tests/test_signal_engine.py::test_no_sg_active_in_indicators -v`
Expected: PASS (the rewritten `compute_indicators` from Task 3 doesn't produce these keys)

- [ ] **Step 3: Delete S-G functions from `signal_engine.py`**

Delete these functions entirely from `backend/signal_engine.py`:
- `_sg_predictive_coeffs` (starts at line 18)
- `_apply_sg_predictive` (starts at line 38)
- `_apply_sg` (starts at line 58)

Delete the `from scipy.signal import savgol_filter` import at line 5.

- [ ] **Step 4: Remove S-G dead zone in `turns_up`/`turns_down`**

In the `turns_up`/`turns_down` branch of `eval_rule`, remove the S-G dead zone lines:

```python
        # DELETE these lines:
        sg_active = indicators.get("_sg_active", {})
        has_sg = sg_active.get(ind, True) if isinstance(sg_active, dict) else True
        eps = abs(v_now) * 3e-5 if has_sg else 0
```

Replace `eps` with `0` in the comparisons, which simplifies to:

```python
    elif cond in ("turns_up", "turns_down"):
        lookback = int(rule.value) if rule.value is not None else 1
        if i < lookback + 1:
            return False
        if cond == "turns_up":
            for k in range(lookback):
                if s.iloc[i - k] - s.iloc[i - k - 1] <= 0:
                    return False
            if s.iloc[i - lookback] - s.iloc[i - lookback - 1] >= 0:
                return False
```

And similarly for `turns_down` — flip the signs. The threshold check (`rule.threshold`) remains unchanged.

- [ ] **Step 5: Remove S-G fields from `StrategyRequest`**

In `backend/models.py`, delete lines 60-66:

```python
    # DELETE these fields:
    ma_type: str = "ema"
    sg8_window: int = 7
    sg8_poly: int = 2
    sg21_window: int = 7
    sg21_poly: int = 2
    predictive_sg: bool = False
    use_sg8: bool = True
    use_sg21: bool = True
```

Also delete `use_sg21` if it appears on the next line (check actual file — the grep showed it was cut off).

- [ ] **Step 6: Run full backend test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS (existing tests should not reference S-G fields)

- [ ] **Step 7: Commit**

```bash
git add backend/signal_engine.py backend/models.py
git commit -m "feat(B12): remove all Savitzky-Golay smoothing code"
```

---

## Task 5: Update Backend Call Sites

**Files:**
- Modify: `backend/routes/backtest.py:85-90` (compute_indicators call), lines 114-124 (`_trace_series_map`)
- Modify: `backend/routes/trading.py:231` (compute_indicators call), lines 239-240 (hardcoded `ema50`)
- Modify: `backend/bot_runner.py:122-123` (compute_indicators call)

- [ ] **Step 1: Update `routes/backtest.py`**

At line 85, change the `compute_indicators` call to:

```python
        all_rules = [migrate_rule(r) for r in req.buy_rules + req.sell_rules]
        indicators = compute_indicators(close, high=high, low=low, rules=all_rules)
```

Add `migrate_rule` to the import at line 8:

```python
from signal_engine import Rule, compute_indicators, eval_rules, eval_rule, migrate_rule
```

Also migrate the rules used in eval_rules (around lines 95-100 where `eval_rules` is called). Replace `req.buy_rules` / `req.sell_rules` with pre-migrated lists:

```python
        buy_rules = [migrate_rule(r) for r in req.buy_rules]
        sell_rules = [migrate_rule(r) for r in req.sell_rules]
```

Then replace **every** `req.buy_rules` / `req.sell_rules` reference in the function with `buy_rules` / `sell_rules`. There are 8 occurrences — all must be updated:

| Line | Current | Replace with |
|------|---------|-------------|
| ~171 | `eval_rules(req.buy_rules, ...)` | `eval_rules(buy_rules, ...)` |
| ~213 | `_trace_rules(req.buy_rules, ...)` | `_trace_rules(buy_rules, ...)` |
| ~268 | `eval_rules(req.sell_rules, ...)` | `eval_rules(sell_rules, ...)` |
| ~313 | `_trace_rules(req.sell_rules, ...)` | `_trace_rules(sell_rules, ...)` |
| ~317 | `_trace_rules(req.sell_rules, ...)` | `_trace_rules(sell_rules, ...)` |
| ~327 | `[r for r in req.sell_rules ...]` | `[r for r in sell_rules ...]` |
| ~329 | `_trace_rules(req.sell_rules, ...)` | `_trace_rules(sell_rules, ...)` |
| ~376 | `for rule in req.buy_rules:` | `for rule in buy_rules:` |

**Missing any one of these causes silent rule evaluation failures** — `resolve_series` won't find unmigrated indicator names like `'ema50'` in the new indicators dict.

Delete the `_trace_series_map` dict (lines 114-124). The `_trace_rules` function (line 126) uses it at line 134: `ind_series = _trace_series_map.get(r.indicator, indicators.get("close"))`. Replace that line with:

```python
                ind_series = resolve_series(r, indicators) or indicators.get("close")
```

**Fix the `ema_overlays` builder** (line 376-397). After migration, `rule.indicator` is `'ma'` not `'ema...'`, so `rule.indicator.startswith("ema")` never matches. Replace:

```python
        # Before:
        if rule.condition in ("rising_over", "falling_over") and rule.indicator.startswith("ema"):
            ema_key = rule.indicator.lower()
            ema_series = indicators.get(ema_key)
```

with:

```python
        # After:
        if rule.condition in ("rising_over", "falling_over") and rule.indicator == "ma" and rule.params:
            ema_series = resolve_series(rule, indicators)
```

And update the overlay dict's `"indicator"` field from `ema_key` to a readable label:

```python
            ema_overlays.append({
                "indicator": f"ma_{rule.params['period']}_{rule.params.get('type', 'ema')}",
                ...
            })
```

Add `resolve_series` to the import at line 8:

```python
from signal_engine import Rule, compute_indicators, eval_rules, eval_rule, migrate_rule, resolve_series
```

- [ ] **Step 2: Update `routes/trading.py`**

At line 231, change to:

```python
            all_rules = [migrate_rule(r) for r in req.buy_rules + req.sell_rules]
            indicators = compute_indicators(df["Close"], high=df["High"], low=df["Low"], rules=all_rules)
```

Add `migrate_rule` to the import at line 15:

```python
from signal_engine import Rule, compute_indicators, eval_rules, migrate_rule
```

At lines 234-235, migrate the rules passed to `eval_rules`:

```python
            buy_rules = [migrate_rule(r) for r in req.buy_rules]
            sell_rules = [migrate_rule(r) for r in req.sell_rules]
            buy_signal = eval_rules(buy_rules, req.buy_logic, indicators, i)
            sell_signal = eval_rules(sell_rules, req.sell_logic, indicators, i)
```

**Critical:** At line 240, `ema50_val = float(indicators["ema50"].iloc[i])` will crash — `compute_indicators` no longer produces `ema50` by default. The response at lines 243-250 returns `"ema50": round(ema50_val, 2)`. Fix inline (do NOT defer to a later task):

**Option A** — if grep shows no frontend consumer of the `ema50` field, just delete lines 240 and 248.

**Option B** — if frontend reads it, compute inline after `compute_indicators`:

```python
            from indicators import compute_instance, OHLCVSeries
            _ohlcv = OHLCVSeries(close=df["Close"], high=df["High"], low=df["Low"], volume=pd.Series(dtype=float))
            ema50_val = float(compute_instance("ma", {"period": 50, "type": "ema"}, _ohlcv)["ma"].iloc[i])
```

- [ ] **Step 3: Update `bot_runner.py`**

At line 122-123, change to:

```python
            all_rules = [migrate_rule(r) for r in self.config.buy_rules + self.config.sell_rules]
            indicators = await self._run_in_executor(
                compute_indicators, df["Close"], df["High"], df["Low"], all_rules
            )
```

`_run_in_executor` (line 63) uses `loop.run_in_executor(None, fn, *args)` — positional only. `rules` is the 4th parameter of `compute_indicators`, so passing it as the 4th positional arg works.

Add `migrate_rule` to the import at line 20:

```python
from signal_engine import compute_indicators, eval_rules, migrate_rule
```

**Also migrate the rules used in `eval_rules` calls.** At lines 238-239 and 412-413, `cfg.buy_rules` / `cfg.sell_rules` are passed directly. Migrate them once at the start of `_tick()`, before any use:

```python
        buy_rules = [migrate_rule(r) for r in cfg.buy_rules]
        sell_rules = [migrate_rule(r) for r in cfg.sell_rules]
```

Then replace `cfg.buy_rules` → `buy_rules` and `cfg.sell_rules` → `sell_rules` in the two `eval_rules` calls (lines 238 and 412).

- [ ] **Step 4: Run the full backend test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/routes/backtest.py backend/routes/trading.py backend/bot_runner.py
git commit -m "feat(B12): update all compute_indicators call sites to pass rules"
```

---

## Task 6: Backend bots.json Migration

**Files:**
- Modify: `backend/bot_manager.py:455-474` (load method)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_signal_engine.py`:

```python
def test_migrate_rules_in_bot_config_dict():
    """Simulate what BotManager.load() does with legacy bot configs."""
    legacy_rules = [
        {"indicator": "ema20", "condition": "rising"},
        {"indicator": "ma8", "condition": "turns_up", "param": "ma21"},
    ]
    migrated = [migrate_rule(Rule(**r)) for r in legacy_rules]
    assert migrated[0].indicator == "ma"
    assert migrated[0].params == {"period": 20, "type": "ema"}
    assert migrated[1].indicator == "ma"
    assert migrated[1].params == {"period": 8, "type": "sma"}
    assert migrated[1].param == "ma:21:sma"
```

- [ ] **Step 2: Run test to verify it passes (migration function already exists)**

Run: `cd backend && python -m pytest tests/test_signal_engine.py::test_migrate_rules_in_bot_config_dict -v`
Expected: PASS (uses `migrate_rule` from Task 2)

- [ ] **Step 3: Add migration to `BotManager.load()`**

In `backend/bot_manager.py`, add import at top:

```python
from signal_engine import migrate_rule, Rule
```

In the `load()` method, after `cfg_dict` is prepared and before `config = BotConfig(**cfg_dict)`, add:

```python
                for key in ("buy_rules", "sell_rules"):
                    if key in cfg_dict and cfg_dict[key]:
                        cfg_dict[key] = [migrate_rule(Rule(**r)).model_dump() for r in cfg_dict[key]]
```

**Also** add `self.save()` at the end of `load()` (after the for-loop, before the except), so the migrated format is persisted to disk immediately — this prevents the migration from being a permanent runtime dependency:

```python
            if self.bots:
                self.save()
```

- [ ] **Step 4: Run full backend test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/bot_manager.py backend/tests/test_signal_engine.py
git commit -m "feat(B12): migrate legacy MA rules on bots.json load"
```

---

## Task 7: Frontend Type Changes

**Files:**
- Modify: `frontend/src/shared/types/index.ts:21-29` (Rule interface), lines 83-90 (S-G fields)

- [ ] **Step 1: Update Rule interface**

In `frontend/src/shared/types/index.ts`, change the Rule interface to:

```typescript
export interface Rule {
  indicator: 'macd' | 'rsi' | 'price' | 'ma'
  condition: string
  value?: number
  param?: string
  threshold?: number
  muted?: boolean
  negated?: boolean
  params?: Record<string, any>
}
```

- [ ] **Step 2: Remove S-G fields from StrategySettings**

In `frontend/src/shared/types/index.ts`, find the interface or type containing lines 83-90 (`ma_type`, `sg8_window`, etc.) and delete those fields.

- [ ] **Step 3: Run TypeScript type check to find all breakage**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -50`
Expected: Errors in RuleRow.tsx (old indicator literals), StrategyBuilder.tsx (S-G fields), App.tsx (MASettings). These are expected — we fix them in Tasks 8-10.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/shared/types/index.ts
git commit -m "feat(B12): update Rule type to generic ma + params, remove S-G types"
```

---

## Task 8: Frontend Migration Function + StrategyBuilder Cleanup

**Files:**
- Modify: `frontend/src/features/strategy/StrategyBuilder.tsx`

- [ ] **Step 1: Add `migrateRule` function**

At the top of `StrategyBuilder.tsx` (after imports, before component), add:

```typescript
const MA_MIGRATION: Record<string, { period: number; type: string }> = {
  ema20:  { period: 20,  type: 'ema' },
  ema50:  { period: 50,  type: 'ema' },
  ema200: { period: 200, type: 'ema' },
  ma8:    { period: 8,   type: 'sma' },
  ma21:   { period: 21,  type: 'sma' },
}

const PARAM_MIGRATION: Record<string, string> = {
  ema20:  'ma:20:ema',
  ema50:  'ma:50:ema',
  ema200: 'ma:200:ema',
  ma8:    'ma:8:sma',
  ma21:   'ma:21:sma',
}

function migrateRule(rule: Rule): Rule {
  const migrated = { ...rule } as Rule
  const maSpec = MA_MIGRATION[rule.indicator]
  if (maSpec) {
    migrated.indicator = 'ma'
    migrated.params = maSpec
  }
  if (rule.param && PARAM_MIGRATION[rule.param]) {
    migrated.param = PARAM_MIGRATION[rule.param]
  }
  return migrated
}
```

- [ ] **Step 2: Apply migration in `loadStrategy`**

Update `loadStrategy()`:

```typescript
function loadStrategy() {
  try {
    const raw = localStorage.getItem(STRATEGY_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed.buyRules) parsed.buyRules = parsed.buyRules.map(migrateRule)
    if (parsed.sellRules) parsed.sellRules = parsed.sellRules.map(migrateRule)
    return parsed
  } catch { return null }
}
```

- [ ] **Step 3: Apply migration in `loadSavedStrategies`**

Update `loadSavedStrategies()`:

```typescript
function loadSavedStrategies(): SavedStrategy[] {
  try {
    const raw = localStorage.getItem(SAVED_STRATEGIES_KEY)
    if (!raw) return []
    const strategies: SavedStrategy[] = JSON.parse(raw)
    for (const s of strategies) {
      if (s.buyRules) s.buyRules = s.buyRules.map(migrateRule)
      if (s.sellRules) s.sellRules = s.sellRules.map(migrateRule)
    }
    return strategies
  } catch { return [] }
}
```

- [ ] **Step 4: Remove S-G serialization from the backtest request body**

Delete lines 190-197 in `StrategyBuilder.tsx`:

```typescript
        // DELETE these lines from the request body:
        ma_type: maSettings?.type,
        sg8_window: maSettings?.sg8Window,
        sg8_poly: maSettings?.sg8Poly,
        sg21_window: maSettings?.sg21Window,
        sg21_poly: maSettings?.sg21Poly,
        predictive_sg: maSettings?.predictiveSg,
        use_sg8: maSettings?.showSg8 ?? true,
        use_sg21: maSettings?.showSg21 ?? true,
```

- [ ] **Step 5: Remove `maSettings` prop**

Remove `maSettings` from the Props interface (line 19) and the component parameter list (line 44). Remove the `MASettings` import from App (line 5).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/strategy/StrategyBuilder.tsx
git commit -m "feat(B12): add frontend migrateRule, remove S-G serialization"
```

---

## Task 9: RuleRow — Collapse MA Indicators + Inline Inputs

**Files:**
- Modify: `frontend/src/features/strategy/RuleRow.tsx:4-53` (constants), component JSX

- [ ] **Step 1: Replace constants**

Replace the constants block (lines 4-53) with:

```typescript
export const INDICATORS = ['macd', 'rsi', 'price', 'ma'] as const

export const CONDITIONS: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down', 'crosses_above', 'crosses_below', 'above', 'below'],
  rsi: ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above'],
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ma: ['turns_up', 'turns_down', 'decelerating', 'accelerating', 'rising', 'falling',
       'above', 'below', 'crosses_above', 'crosses_below', 'rising_over', 'falling_over'],
}

export const CONDITION_LABELS: Record<string, string> = {
  crossover_up: 'Crosses above signal',
  crossover_down: 'Crosses below signal',
  crosses_above: 'Crosses above',
  crosses_below: 'Crosses below',
  above: 'Is above',
  below: 'Is below',
  turns_up_below: 'Turns up from below',
  turns_down_above: 'Turns down from above',
  rising: 'Is rising',
  falling: 'Is falling',
  rising_over: 'Rising over N bars',
  falling_over: 'Falling over N bars',
  turns_up: 'Turns up',
  turns_down: 'Turns down',
  decelerating: 'Decelerating',
  accelerating: 'Accelerating',
}

export const NEEDS_VALUE = ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above', 'rising_over', 'falling_over']
export const OPTIONAL_VALUE = ['turns_up', 'turns_down']

export const NEEDS_PARAM: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down'],
}

export const CAN_USE_PARAM: Record<string, string[]> = {
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ma: ['above', 'below', 'crosses_above', 'crosses_below'],
}

export const emptyRule = (): Rule => ({ indicator: 'macd', condition: 'crossover_up' })
```

Delete the old `PARAM_OPTIONS` array entirely.

- [ ] **Step 2: Add MA params inline inputs to the component JSX**

After the indicator `<select>`, when `rule.indicator === 'ma'`, render inline period + type inputs:

```tsx
{rule.indicator === 'ma' && (
  <>
    <input
      type="number"
      min={2}
      max={500}
      value={rule.params?.period ?? 20}
      onChange={e => onChange({ ...rule, params: { ...rule.params, period: parseInt(e.target.value) || 20, type: rule.params?.type ?? 'ema' } })}
      style={{ width: 45 }}
    />
    <select
      value={rule.params?.type ?? 'ema'}
      onChange={e => onChange({ ...rule, params: { ...rule.params, period: rule.params?.period ?? 20, type: e.target.value } })}
      style={{ width: 55 }}
    >
      <option value="sma">SMA</option>
      <option value="ema">EMA</option>
    </select>
  </>
)}
```

- [ ] **Step 3: Handle indicator switch — populate/clear params**

In the indicator `<select>` onChange handler, ensure:
- Switching TO `'ma'` populates default `params: { period: 20, type: 'ema' }`
- Switching AWAY FROM `'ma'` clears `params` to `undefined`

```tsx
onChange={e => {
  const newInd = e.target.value as Rule['indicator']
  const update: Partial<Rule> = { indicator: newInd, condition: CONDITIONS[newInd][0] }
  if (newInd === 'ma') {
    update.params = { period: 20, type: 'ema' }
  } else {
    update.params = undefined
  }
  onChange({ ...rule, ...update })
}}
```

- [ ] **Step 4: Replace PARAM_OPTIONS with dynamic cross-reference picker**

Replace the old param dropdown with a two-stage picker. First dropdown: `[Value] [Price] [MA]`. If "MA" selected, show inline period + type.

Helper to decode current param:

```typescript
function decodeParam(param?: string): { mode: 'value' | 'close' | 'ma'; period?: number; type?: string } {
  if (!param) return { mode: 'value' }
  if (param === 'close') return { mode: 'close' }
  if (param.startsWith('ma:')) {
    const parts = param.split(':')
    if (parts.length === 3) {
      const period = parseInt(parts[1])
      return { mode: 'ma', period: isNaN(period) ? 50 : period, type: parts[2] || 'ema' }
    }
  }
  return { mode: 'value' }
}

function encodeParam(mode: string, period?: number, type?: string): string | undefined {
  if (mode === 'close') return 'close'
  if (mode === 'ma') return `ma:${period ?? 50}:${type ?? 'ema'}`
  return undefined
}
```

Render the param section (where `CAN_USE_PARAM` or `NEEDS_PARAM` allows it):

```tsx
{(CAN_USE_PARAM[rule.indicator]?.includes(rule.condition)) && (() => {
  const decoded = decodeParam(rule.param)
  return (
    <>
      <select
        value={decoded.mode}
        onChange={e => onChange({ ...rule, param: encodeParam(e.target.value, decoded.period, decoded.type), value: e.target.value === 'value' ? rule.value : undefined })}
      >
        <option value="value">Value</option>
        <option value="close">Price</option>
        <option value="ma">MA</option>
      </select>
      {decoded.mode === 'ma' && (
        <>
          <input
            type="number"
            min={2}
            max={500}
            value={decoded.period ?? 50}
            onChange={e => onChange({ ...rule, param: encodeParam('ma', parseInt(e.target.value) || 50, decoded.type) })}
            style={{ width: 45 }}
          />
          <select
            value={decoded.type ?? 'ema'}
            onChange={e => onChange({ ...rule, param: encodeParam('ma', decoded.period, e.target.value) })}
            style={{ width: 55 }}
          >
            <option value="sma">SMA</option>
            <option value="ema">EMA</option>
          </select>
        </>
      )}
    </>
  )
})()}
```

- [ ] **Step 5: Update indicator display label**

For the indicator dropdown option text, show "MA" for the `ma` type. For validation messages in `validateRules`, derive a readable label:

```typescript
function indicatorLabel(rule: Rule): string {
  if (rule.indicator === 'ma' && rule.params) {
    return `MA ${rule.params.period} ${(rule.params.type || 'ema').toUpperCase()}`
  }
  return rule.indicator.toUpperCase()
}
```

Update `validateRules` to use this label in error messages.

- [ ] **Step 6: Run TypeScript type check**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -30`
Expected: Remaining errors should only be in App.tsx (MASettings — Task 10)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/strategy/RuleRow.tsx
git commit -m "feat(B12): collapse MA indicators into generic ma + inline params UI"
```

---

## Task 10: App.tsx — Remove MASettings

**Files:**
- Modify: `frontend/src/App.tsx:16-30` (MASettings interface + default), line 67 (state), lines 72-74 (memoization), line 203 (prop)

- [ ] **Step 1: Remove MASettings interface and default**

Delete the `MASettings` interface (line 16) and `DEFAULT_MA_SETTINGS` constant (line 30).

- [ ] **Step 2: Remove maSettings state**

Delete the `const [maSettings]` line (67).

- [ ] **Step 3: Remove maSettings from memoization deps**

In the `useMemo` at line 72, remove `maSettings` from the dependency object and the deps array.

- [ ] **Step 4: Remove maSettings prop from StrategyBuilder**

At line 203, remove `maSettings={maSettings}` from the StrategyBuilder JSX props.

- [ ] **Step 5: Remove the MASettings export**

If `MASettings` is exported (check: `export interface MASettings`), remove the export. StrategyBuilder.tsx was the only consumer (already cleaned up in Task 8).

- [ ] **Step 6: Run TypeScript type check — should be clean**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(B12): remove MASettings interface and S-G state from App"
```

---

## Task 11: End-to-End Smoke Test

**Files:** No new files — manual verification

- [ ] **Step 1: Start the backend**

Run: `cd backend && python main.py`
Verify: Server starts without import errors

- [ ] **Step 2: Start the frontend**

Run: `cd frontend && npm run dev`
Verify: No build errors

- [ ] **Step 3: Test the strategy builder UI**

1. Open browser to `http://localhost:5173`
2. Add a buy rule — select "MA" indicator
3. Verify inline period (default 20) and type (default EMA) inputs appear
4. Change period to 50, type to SMA — verify no errors
5. Select condition "Crosses above" — verify param picker shows [Value] [Price] [MA]
6. Select "MA" as the cross-reference — verify inline period/type inputs appear for the ref
7. Run a backtest — verify it completes without errors

- [ ] **Step 4: Test migration of saved strategies**

1. If you have saved strategies with old-format MA rules, load one
2. Verify it loads correctly with the new MA format
3. If no saved strategies exist, create one, save, reload page, verify it persists

- [ ] **Step 5: Test bot creation with MA rules**

1. Create a bot with MA rules
2. Stop the backend, restart it
3. Verify the bot loads correctly (migration applied on bots.json load)

- [ ] **Step 6: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix(B12): smoke test fixes"
```

---

## Hazards & Non-Obvious Details

1. **`bot_runner.py` passes args positionally** to `_run_in_executor(None, fn, *args)`. `rules` is the 4th parameter of `compute_indicators`, so positional works. Verified.

2. **8 references to `req.buy_rules`/`req.sell_rules` in backtest.py** — Task 5 Step 1 enumerates all of them with a table. Missing any one causes silent rule evaluation failures. The `_trace_rules` calls and the `ema_overlays` loop are easy to miss.

3. **`ema_overlays` builder** checks `rule.indicator.startswith("ema")` — after migration, indicator is `'ma'`, so this silently breaks. Fixed in Task 5 Step 1 to check `rule.indicator == "ma" and rule.params` and use `resolve_series`.

4. **`trading.py:240` `ema50` reference** — fixed inline in Task 5 Step 2, not deferred. Must be fixed in the same commit as the `compute_indicators` change.

5. **MACD `crossover_up`/`crossover_down` param is `'signal'`** — auto-filled by `NEEDS_PARAM` and never goes through the cross-reference picker. `resolve_ref` handles it. Don't break this path.

6. **`compute_indicators` with `rules=[]`** — still produces MACD/RSI/close. No MA keys. Any code that reads hardcoded `indicators["ema50"]` will crash.

7. **The `ema` compute function in `indicators.py`** (`compute_ema`) is NO LONGER CALLED by `compute_indicators`. `compute_ma` handles individual MAs now. Don't delete `compute_ema` — it may be used by chart indicator routes.

8. **`param.split(':')` safety** — `resolve_ref` and `compute_indicators` both guard against malformed `'ma:...'` params with `split(':', 2)` + length check + `try/except ValueError` on `int()` conversion. Frontend `encodeParam` uses `period ?? 50` fallback but this doesn't catch `NaN` — the backend guards are the safety net.

9. **Frontend `min={2}` matches backend** — `PARAM_CONSTRAINTS["ma"]["period"]` is `(2, 500)`. Frontend period inputs use `min={2}` to match.

10. **bots.json migration is persisted** — `BotManager.load()` calls `self.save()` after migration so the disk file is updated immediately, preventing the migration from being a permanent runtime dependency.

11. **S-G removal changes backtest results** — removing S-G dead zone epsilon changes `turns_up`/`turns_down` sensitivity. Raw MAs have more micro-oscillations. Existing strategies may produce different results. This is intentional (S-G was experimental) but users may notice.
