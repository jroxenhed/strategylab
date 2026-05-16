/**
 * Unit tests for applyParamPath (F227).
 *
 * Verifies that the path resolver produces correct immutable updates for every
 * path variant produced by buildParamOptions without mutating the original request.
 */
import { describe, it, expect } from 'vitest'
import { applyParamPath } from '../features/strategy/paramOptions'
import type { StrategyRequest } from '../shared/types/strategy'

const BASE: StrategyRequest = {
  ticker: 'AAPL',
  start: '2020-01-01',
  end: '2026-01-01',
  interval: '1d',
  buy_rules: [
    { indicator: 'rsi', condition: 'below', value: 30, muted: false, negated: false },
    { indicator: 'macd', condition: 'crossover_up', muted: false, negated: false },
    { indicator: 'ma', condition: 'above', value: 50, params: { period: 20 }, muted: false, negated: false },
  ],
  sell_rules: [
    { indicator: 'rsi', condition: 'above', value: 70, muted: false, negated: false },
    { indicator: 'macd', condition: 'crossover_down', muted: false, negated: false },
  ],
  buy_logic: 'AND',
  sell_logic: 'AND',
  initial_capital: 10000,
  position_size: 1,
  source: 'yahoo',
  stop_loss_pct: 5,
  trailing_stop: { type: 'pct', value: 3, source: 'high', activate_on_profit: false, activate_pct: 0 },
  slippage_bps: 2,
}

describe('applyParamPath — buy/sell rule value', () => {
  it('round-trip: write buy_rule_0_value produces correct immutable update', () => {
    const result = applyParamPath(BASE, 'buy_rule_0_value', 42)
    // Target rule updated
    expect(result.buy_rules[0].value).toBe(42)
    // Other rules unchanged (by reference equality for objects)
    expect(result.buy_rules[1]).toBe(BASE.buy_rules[1])
    expect(result.buy_rules[2]).toBe(BASE.buy_rules[2])
    // Sell rules completely untouched
    expect(result.sell_rules).toBe(BASE.sell_rules)
    // Top-level request is new object
    expect(result).not.toBe(BASE)
    // Original is not mutated
    expect(BASE.buy_rules[0].value).toBe(30)
  })

  it('multi-rule: write sell_rule_1_value touches only that rule', () => {
    const result = applyParamPath(BASE, 'sell_rule_1_value', 99)
    // sell_rules[1] updated (was undefined, now 99)
    expect(result.sell_rules[1].value).toBe(99)
    // sell_rules[0] untouched by reference
    expect(result.sell_rules[0]).toBe(BASE.sell_rules[0])
    // buy_rules untouched
    expect(result.buy_rules).toBe(BASE.buy_rules)
  })
})

describe('applyParamPath — rule params', () => {
  it('buy_rule_2_params_period writes into params object, preserves integer type', () => {
    const result = applyParamPath(BASE, 'buy_rule_2_params_period', 25.7)
    // Integer original → rounded
    expect(result.buy_rules[2].params?.period).toBe(26)
    // Other buy rules untouched
    expect(result.buy_rules[0]).toBe(BASE.buy_rules[0])
    expect(result.buy_rules[1]).toBe(BASE.buy_rules[1])
  })
})

describe('applyParamPath — top-level fields', () => {
  it('stop_loss_pct', () => {
    const result = applyParamPath(BASE, 'stop_loss_pct', 7.5)
    expect(result.stop_loss_pct).toBe(7.5)
    // Other fields unchanged
    expect(result.slippage_bps).toBe(BASE.slippage_bps)
  })

  it('trailing_stop_value preserves all other trailing_stop fields', () => {
    const result = applyParamPath(BASE, 'trailing_stop_value', 4)
    expect(result.trailing_stop?.value).toBe(4)
    expect(result.trailing_stop?.type).toBe('pct')
    expect(result.trailing_stop?.source).toBe('high')
    expect(result.trailing_stop?.activate_on_profit).toBe(false)
  })

  it('slippage_bps', () => {
    const result = applyParamPath(BASE, 'slippage_bps', 5)
    expect(result.slippage_bps).toBe(5)
  })

  it('unknown path returns original req unchanged', () => {
    const result = applyParamPath(BASE, 'unknown_path_xyz', 99)
    expect(result).toBe(BASE)
  })

  it('out-of-bounds rule index returns original req unchanged', () => {
    const result = applyParamPath(BASE, 'buy_rule_99_value', 42)
    expect(result).toBe(BASE)
  })
})
