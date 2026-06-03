/**
 * F221: buildParamOptions ordering — buy/sell rule thresholds must come
 * before cost params (stop loss, trailing, slippage) so the [0] default
 * lands on a strategy parameter, not a cost assumption.
 *
 * F187: regime-mode path emission — buildParamOptions must emit
 * long_buy_rule_* / short_sell_rule_* paths (not bare buy_rule_*) when
 * req.regime.enabled is true. applyParamPath must write long_buy_rule_0_value
 * back to req.long_buy_rules[0].value.
 */
import { describe, it, expect } from 'vitest'
import { buildParamOptions, applyParamPath } from '../features/strategy/paramOptions'
import type { StrategyRequest } from '../shared/types/strategy'

// Regime-mode request: regime.enabled = true, rules in the four directional arrays.
const REGIME_REQ: StrategyRequest = {
  ticker: 'SPY',
  start: '2024-01-01',
  end: '2024-12-31',
  interval: '1d',
  // bare arrays present for UI symmetry but NOT read by the engine in regime mode
  buy_rules: [
    { indicator: 'rsi', condition: 'is_below', value: 30, params: { period: 14 } },
  ],
  sell_rules: [
    { indicator: 'rsi', condition: 'is_above', value: 70, params: { period: 14 } },
  ],
  // regime-specific arrays that the engine actually consumes
  long_buy_rules: [
    { indicator: 'rsi', condition: 'is_below', value: 35, params: { period: 14 } },
  ],
  long_sell_rules: [
    { indicator: 'rsi', condition: 'is_above', value: 65, params: { period: 14 } },
  ],
  short_buy_rules: [
    { indicator: 'rsi', condition: 'is_above', value: 60, params: { period: 14 } },
  ],
  short_sell_rules: [
    { indicator: 'rsi', condition: 'is_below', value: 40, params: { period: 14 } },
  ],
  buy_logic: 'AND',
  sell_logic: 'AND',
  initial_capital: 10000,
  position_size: 1,
  source: 'yahoo',
  slippage_bps: 2,
  regime: {
    enabled: true,
    timeframe: '1d',
    indicator: 'ma',
    indicator_params: { period: 200, type: 'sma' },
    condition: 'above',
    min_bars: 1,
  },
}

const BASE: StrategyRequest = {
  ticker: 'AAPL',
  start: '2024-01-01',
  end: '2024-12-31',
  interval: '1d',
  buy_rules: [
    { indicator: 'rsi', condition: 'is_below', value: 30, params: { period: 14 } },
  ],
  sell_rules: [
    { indicator: 'rsi', condition: 'is_above', value: 70, params: { period: 14 } },
  ],
  buy_logic: 'AND',
  sell_logic: 'AND',
  initial_capital: 10000,
  position_size: 1,
  source: 'yahoo',
  stop_loss_pct: 2,
  slippage_bps: 2,
}

describe('buildParamOptions ordering (F221)', () => {
  it('first option is a buy-rule threshold, not slippage', () => {
    const opts = buildParamOptions(BASE)
    expect(opts[0].path).toBe('buy_rule_0_value')
    expect(opts[0].label).toMatch(/Buy Rule 1/i)
  })

  it('buy/sell rule paths precede stop/slippage', () => {
    const opts = buildParamOptions(BASE)
    const paths = opts.map(o => o.path)
    const firstCostIdx = paths.findIndex(p => p === 'stop_loss_pct' || p === 'slippage_bps')
    const lastRuleIdx = paths.map((p, i) => p.startsWith('buy_rule_') || p.startsWith('sell_rule_') ? i : -1)
      .reduce((a, b) => Math.max(a, b), -1)
    expect(lastRuleIdx).toBeLessThan(firstCostIdx)
  })

  it('falls back to slippage when no rules with thresholds exist', () => {
    const opts = buildParamOptions({
      ...BASE, buy_rules: [], sell_rules: [], stop_loss_pct: undefined,
    })
    expect(opts[0].path).toBe('slippage_bps')
  })
})

// ---------------------------------------------------------------------------
// F187 — regime-mode path emission regression
// ---------------------------------------------------------------------------

describe('buildParamOptions regime-mode path emission (F187)', () => {
  it('emits long_buy_rule_ / long_sell_rule_ / short_buy_rule_ / short_sell_rule_ paths in regime mode', () => {
    const opts = buildParamOptions(REGIME_REQ)
    const paths = opts.map(o => o.path)

    expect(paths.some(p => /^long_buy_rule_\d+_(value|params_)/.test(p))).toBe(true)
    expect(paths.some(p => /^long_sell_rule_\d+_(value|params_)/.test(p))).toBe(true)
    expect(paths.some(p => /^short_buy_rule_\d+_(value|params_)/.test(p))).toBe(true)
    expect(paths.some(p => /^short_sell_rule_\d+_(value|params_)/.test(p))).toBe(true)
  })

  it('does NOT emit bare buy_rule_ or sell_rule_ paths in regime mode (F187 regression)', () => {
    const opts = buildParamOptions(REGIME_REQ)
    const paths = opts.map(o => o.path)

    // bare buy_rule_<i>_* paths must be absent — they map to fields the engine
    // ignores when regime is enabled, producing zero variance across combos.
    const bareBuyRule = paths.filter(
      p => /^buy_rule_\d+_/.test(p)
    )
    const bareSellRule = paths.filter(
      p => /^sell_rule_\d+_/.test(p)
    )
    expect(bareBuyRule).toHaveLength(0)
    expect(bareSellRule).toHaveLength(0)
  })
})

describe('applyParamPath regime-mode writeback (F187)', () => {
  it('writes long_buy_rule_0_value to req.long_buy_rules[0].value', () => {
    const updated = applyParamPath(REGIME_REQ, 'long_buy_rule_0_value', 25)
    expect(updated.long_buy_rules?.[0]?.value).toBe(25)
    // base object must not be mutated
    expect(REGIME_REQ.long_buy_rules?.[0]?.value).toBe(35)
    // write must land on long_buy_rules, not accidentally on buy_rules
    expect(updated.buy_rules?.[0]?.value).toBe(30)
  })

  it('writes short_sell_rule_0_value to req.short_sell_rules[0].value', () => {
    const updated = applyParamPath(REGIME_REQ, 'short_sell_rule_0_value', 45)
    expect(updated.short_sell_rules?.[0]?.value).toBe(45)
    expect(REGIME_REQ.short_sell_rules?.[0]?.value).toBe(40)
  })
})
