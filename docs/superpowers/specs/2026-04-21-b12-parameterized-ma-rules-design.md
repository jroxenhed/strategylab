# B12: Parameterized Moving Average Rules

Replace 5 hardcoded MA entries (ma8, ma21, ema20, ema50, ema200) in the strategy builder with a single generic `ma(period, type)` rule indicator. Users pick any period + SMA/EMA. Foundation for B13 (BB/ATR/Volume rules) and B14 (Stochastic/ADX).

Related: [Strategy builder indicators ideation](../../ideas/2026-04-21-strategy-builder-indicators-ideation.md)

## Scope

- Rule model gains generic `params` field; `indicator` becomes type-level (4 types instead of 8)
- Signal engine computes MAs on demand from rule declarations instead of hardcoding
- RuleRow UI shows inline period + type inputs for MA rules
- Auto-migration for saved strategies (localStorage) and bot configs (bots.json)
- S-G (Savitzky-Golay) smoothing removed entirely

**Out of scope:** chart-strategy auto-sync, unified indicator registry, BB/ATR/Volume/Stochastic/ADX rules (B13/B14).

## Current state

### Frontend (RuleRow.tsx)

8 hardcoded indicators: `macd, rsi, price, ema20, ema50, ema200, ma8, ma21`. Five of these are just MAs with fixed periods. Each has its own CONDITIONS entry, CAN_USE_PARAM entry, and PARAM_OPTIONS entry — heavy duplication.

### Backend (signal_engine.py)

`compute_indicators()` always computes a fixed set: EMA(20/50/200), MA(8/21) with optional S-G smoothing. `series_map` and `ref_map` are hardcoded dicts mapping indicator names to computed arrays. MA8/MA21 have S-G smoothing code (experimental, unused in practice).

### Rule model (types/index.ts + signal_engine.py)

`Rule.indicator` is an instance-level string (`'ema20'`, `'ma8'`). No generic params field. Cross-references use `Rule.param` with values like `'ema50'`, `'ma21'`.

## Rule model changes

### Backend (signal_engine.py)

```python
class Rule(BaseModel):
    indicator: str                        # 'macd' | 'rsi' | 'price' | 'ma'
    condition: str
    value: Optional[float] = None
    param: Optional[str] = None           # cross-ref: 'signal', 'close', 'ma:50:ema'
    threshold: Optional[float] = None
    muted: Optional[bool] = False
    negated: Optional[bool] = False
    params: Optional[dict] = None         # indicator config: {"period": 20, "type": "ema"}
```

### Frontend (types/index.ts)

```typescript
export interface Rule {
  indicator: 'macd' | 'rsi' | 'price' | 'ma'
  condition: string
  value?: number
  param?: string              // cross-ref: 'signal' | 'close' | 'ma:50:ema'
  threshold?: number
  muted?: boolean
  negated?: boolean
  params?: Record<string, any>  // indicator config: { period: 20, type: 'ema' }
}
```

### StrategyRequest (models.py)

Remove all S-G fields:
- `ma_type`
- `sg8_window`, `sg8_poly`
- `sg21_window`, `sg21_poly`
- `predictive_sg`
- `use_sg8`, `use_sg21`

## Signal engine changes

### compute_indicators() becomes rule-driven

Instead of computing a fixed set of MAs, scan the rules to determine what's needed:

```python
def compute_indicators(close, high, low, rules):
    ohlcv = OHLCVSeries(close=close, high=high or close, low=low or close, volume=pd.Series(dtype=float))
    indicators = {"close": close}

    # MACD + RSI always computed (cheap, commonly used)
    macd_result = compute_instance("macd", {"fast": 12, "slow": 26, "signal": 9}, ohlcv)
    indicators["macd"] = macd_result["macd"]
    indicators["signal"] = macd_result["signal"]
    indicators["histogram"] = macd_result["histogram"]

    rsi_result = compute_instance("rsi", {"period": 14}, ohlcv)
    indicators["rsi"] = rsi_result["rsi"]

    # Collect MA specs from rules (both indicator and param references)
    ma_specs = set()
    for rule in rules:
        if rule.indicator == "ma" and rule.params:
            ma_specs.add((rule.params["period"], rule.params.get("type", "ema")))
        if rule.param and rule.param.startswith("ma:"):
            _, period, ma_type = rule.param.split(":")
            ma_specs.add((int(period), ma_type))

    # Compute each unique MA via the existing registry
    for period, ma_type in ma_specs:
        key = f"ma_{period}_{ma_type}"
        ma_result = compute_instance("ma", {"period": period, "type": ma_type}, ohlcv)
        indicators[key] = ma_result["ma"]

    return indicators
```

### Dynamic series resolution

Replace hardcoded `series_map`/`ref_map` with resolution functions:

```python
def resolve_series(rule, indicators):
    """Resolve the primary series for a rule's indicator."""
    if rule.indicator == "ma" and rule.params:
        key = f"ma_{rule.params['period']}_{rule.params.get('type', 'ema')}"
        return indicators[key]
    # Fixed indicators
    fixed = {"macd": "macd", "rsi": "rsi", "price": "close"}
    return indicators.get(fixed.get(rule.indicator, rule.indicator))

def resolve_ref(rule, indicators):
    """Resolve the cross-reference series for a rule's param."""
    if not rule.param:
        return None
    if rule.param == "signal":
        return indicators["signal"]
    if rule.param == "close":
        return indicators["close"]
    if rule.param.startswith("ma:"):
        _, period, ma_type = rule.param.split(":")
        key = f"ma_{int(period)}_{ma_type}"
        return indicators[key]
    return None
```

### eval_rule() update

Replace `series = series_map[rule.indicator]` and `ref = ref_map.get(rule.param)` with calls to `resolve_series()` and `resolve_ref()`.

### S-G removal

Delete all S-G smoothing code:
- `compute_indicators()` — remove S-G parameters, `_sg_active` dict, `ma8_sg`/`ma21_sg` keys
- `eval_rule()` — remove the S-G dead zone logic in `turns_up`/`turns_down` (the `_sg_active` lookup and `eps` calculation at ~line 201-205). Without S-G, epsilon is always 0.
- Top-level S-G functions: `_sg_predictive_coeffs()`, `_apply_sg_predictive()`, `_apply_sg()`, and the `scipy.signal` import

## Frontend changes

### RuleRow.tsx constants

```typescript
export const INDICATORS = ['macd', 'rsi', 'price', 'ma'] as const

export const CONDITIONS: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down', 'crosses_above', 'crosses_below', 'above', 'below'],
  rsi: ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above'],
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ma: ['turns_up', 'turns_down', 'decelerating', 'accelerating', 'rising', 'falling',
       'above', 'below', 'crosses_above', 'crosses_below', 'rising_over', 'falling_over'],
}

export const NEEDS_PARAM: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down'],
}

export const CAN_USE_PARAM: Record<string, string[]> = {
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ma: ['above', 'below', 'crosses_above', 'crosses_below'],
}
```

`PARAM_OPTIONS` becomes dynamic — see RuleRow UI section.

### RuleRow UI layout

When `indicator === 'ma'`, show inline period + type inputs immediately after the indicator dropdown:

```
[Mute] [NOT] [MA v] [20] [EMA v] [Crosses above v] [Value/ref v] [🗑]
```

- Period: `<input type="number" min={1} max={500}/>` at ~45px width
- Type: `<select>` with SMA/EMA options at ~55px width
- Both write to `rule.params.period` and `rule.params.type`

When indicator switches to `'ma'`, populate default params `{ period: 20, type: 'ema' }`. When switching away from `'ma'`, clear `params`.

### Cross-reference (param) UI

Replace the fixed `PARAM_OPTIONS` dropdown with a two-stage picker:

1. First dropdown: `[Value] [Price] [MA]`
2. If "MA" selected: show inline period + type inputs (same compact style as the indicator side)

The `param` string encodes as:
- `undefined` → "Value" (use numeric input)
- `'close'` → "Price"
- `'ma:50:ema'` → "MA" with period=50, type=EMA

This replaces the 6-entry PARAM_OPTIONS with a compact, extensible pattern. For B13, adding BB as a reference target means adding one more option to the first dropdown.

### Indicator display label

The indicator dropdown shows "MA" but the full label (used in validation messages, etc.) should be derived: `MA ${params.period} ${params.type.toUpperCase()}` → "MA 20 EMA".

## Migration

### Frontend: migrateRule()

Applied on localStorage load in StrategyBuilder.tsx. Idempotent — running on already-migrated rules is a no-op.

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
  const migrated = { ...rule }
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

Applied in two places:
- **Active strategy** (`strategylab-strategy` key) — migrate buyRules/sellRules on load in `loadStrategy()`
- **Saved strategies library** (`strategylab-saved-strategies` key) — migrate each saved strategy's rules on load in `loadSavedStrategies()`

### Backend: migrate_rule()

Same mapping in Python. Applied in two places:

1. **bots.json load** — migrate bot rules in-place on first load, re-save the file
2. **backtest route** — defensive migration on incoming API requests (handles old clients)

### No version flag needed

Migration is idempotent — `indicator === 'ma'` doesn't match any migration key. Old format auto-converts; new format passes through unchanged.

## Call site changes

Three places call `compute_indicators()` — all need updating:

### routes/backtest.py
1. Remove S-G parameters from the call
2. Pass `req.buy_rules + req.sell_rules` so it can determine which MAs to compute (both sides — a sell rule referencing an MA that only appears in sell rules still needs its series computed)
3. Apply `migrate_rule()` defensively to incoming rules

### bot_runner.py (line ~123)
1. Pass `cfg.buy_rules + cfg.sell_rules` to `compute_indicators()`
2. Apply `migrate_rule()` to bot rules on load (see Migration section)

### routes/trading.py (line ~231, signal preview endpoint)
1. Pass request rules to `compute_indicators()`
2. Apply `migrate_rule()` defensively

### Frontend: StrategyBuilder.tsx
Remove S-G field serialization (lines ~190-197: `ma_type`, `sg8_window`, `sg8_poly`, etc.). Remove the `maSettings` prop and its thread from App.tsx. Remove the sidebar MA settings UI that configures S-G parameters.

## Extensibility for B13/B14

This design establishes patterns that B13 (BB/ATR/Volume) and B14 (Stochastic/ADX) will reuse:

- **`params` field**: BB would use `{ period: 20, std_dev: 2 }`, Stochastic `{ k: 14, d: 3 }`
- **Rule-driven compute**: same scan-rules-then-compute pattern extends to any indicator
- **`resolve_series()` / `resolve_ref()`**: add cases for new indicator types
- **CONDITIONS by type**: add entries for `bb`, `atr`, `stochastic`, `adx`
- **Cross-reference encoding**: `'ma:50:ema'` format works for simple indicators; B13 may want a structured `paramRef` if encodings like `'bb:20:2:upper'` get unwieldy

The core architectural decisions (type-level indicators, generic params, dynamic compute, resolution functions) carry forward without rework. B13/B14 add new entries to the existing pattern rather than restructuring it.
