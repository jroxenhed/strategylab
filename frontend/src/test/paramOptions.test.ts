/**
 * F221: buildParamOptions ordering — buy/sell rule thresholds must come
 * before cost params (stop loss, trailing, slippage) so the [0] default
 * lands on a strategy parameter, not a cost assumption.
 */
import { describe, it, expect } from 'vitest'
import { buildParamOptions } from '../features/strategy/paramOptions'
import type { StrategyRequest } from '../shared/types/strategy'

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
