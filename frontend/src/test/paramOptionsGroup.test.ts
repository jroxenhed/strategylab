/**
 * Unit tests for groupParamOptions and shouldGroupParamOptions (F237).
 */
import { describe, it, expect } from 'vitest'
import { buildParamOptions, groupParamOptions, shouldGroupParamOptions } from '../features/strategy/paramOptions'
import type { StrategyRequest } from '../shared/types/strategy'

// Minimal rule factory
const rsiRule = (value: number) => ({
  indicator: 'rsi' as const,
  condition: 'below' as const,
  value,
  muted: false,
  negated: false,
})

const BASE: StrategyRequest = {
  ticker: 'AAPL',
  start: '2020-01-01',
  end: '2026-01-01',
  interval: '1d',
  buy_rules: [rsiRule(30)],
  sell_rules: [rsiRule(70)],
  buy_logic: 'AND',
  sell_logic: 'AND',
  initial_capital: 10000,
  position_size: 1,
  source: 'yahoo',
  slippage_bps: 2,
}

// ─── shouldGroupParamOptions ──────────────────────────────────────────────────

describe('shouldGroupParamOptions', () => {
  it('returns false when buy + sell = 2 (one each)', () => {
    expect(shouldGroupParamOptions(BASE)).toBe(false)
  })

  it('returns true when buy = 2, sell = 1 (total 3)', () => {
    const req: StrategyRequest = {
      ...BASE,
      buy_rules: [rsiRule(30), rsiRule(25)],
      sell_rules: [rsiRule(70)],
    }
    expect(shouldGroupParamOptions(req)).toBe(true)
  })

  it('returns true when buy = 1, sell = 2 (total 3)', () => {
    const req: StrategyRequest = {
      ...BASE,
      buy_rules: [rsiRule(30)],
      sell_rules: [rsiRule(70), rsiRule(80)],
    }
    expect(shouldGroupParamOptions(req)).toBe(true)
  })

  it('returns false when buy = 2, sell = 0 (total 2)', () => {
    const req: StrategyRequest = {
      ...BASE,
      buy_rules: [rsiRule(30), rsiRule(25)],
      sell_rules: [],
    }
    expect(shouldGroupParamOptions(req)).toBe(false)
  })
})

// ─── groupParamOptions ────────────────────────────────────────────────────────

describe('groupParamOptions — 1 buy + 1 sell rule', () => {
  it('produces Buy Rule 1, Sell Rule 1, and Costs groups (no Risk without stop_loss)', () => {
    const opts = buildParamOptions(BASE, 5)
    const groups = groupParamOptions(opts)
    const names = groups.map(g => g.group)
    expect(names).toContain('Buy Rule 1')
    expect(names).toContain('Sell Rule 1')
    expect(names).toContain('Costs')
    // No Risk group because BASE has no stop_loss_pct or trailing_stop
    expect(names).not.toContain('Risk')
  })

  it('every option appears in exactly one group', () => {
    const opts = buildParamOptions(BASE, 5)
    const groups = groupParamOptions(opts)
    const allPaths = groups.flatMap(g => g.options.map(o => o.path))
    expect(allPaths.sort()).toEqual(opts.map(o => o.path).sort())
  })
})

describe('groupParamOptions — 3+ rules produce 3+ groups', () => {
  it('3 buy rules → 3 Buy Rule groups', () => {
    const req: StrategyRequest = {
      ...BASE,
      buy_rules: [rsiRule(30), rsiRule(28), rsiRule(25)],
      sell_rules: [rsiRule(70)],
    }
    const opts = buildParamOptions(req, 5)
    const groups = groupParamOptions(opts)
    const names = groups.map(g => g.group)
    expect(names).toContain('Buy Rule 1')
    expect(names).toContain('Buy Rule 2')
    expect(names).toContain('Buy Rule 3')
    expect(names).toContain('Sell Rule 1')
    // At least 4 named groups
    expect(groups.length).toBeGreaterThanOrEqual(4)
  })

  it('options within a group preserve internal order', () => {
    const req: StrategyRequest = {
      ...BASE,
      buy_rules: [
        { indicator: 'ma', condition: 'above', value: 50, params: { period: 20 }, muted: false, negated: false },
      ],
      sell_rules: [rsiRule(70)],
    }
    const opts = buildParamOptions(req, 5)
    const groups = groupParamOptions(opts)
    const buyGroup = groups.find(g => g.group === 'Buy Rule 1')
    expect(buyGroup).toBeDefined()
    // value comes before params in buildParamOptions iteration order
    const paths = buyGroup!.options.map(o => o.path)
    const valueIdx = paths.indexOf('buy_rule_0_value')
    const paramIdx = paths.indexOf('buy_rule_0_params_period')
    expect(valueIdx).toBeGreaterThanOrEqual(0)
    expect(paramIdx).toBeGreaterThanOrEqual(0)
    expect(valueIdx).toBeLessThan(paramIdx)
  })
})

describe('groupParamOptions — Risk group', () => {
  it('stop_loss_pct and trailing_stop_value land in Risk group', () => {
    const req: StrategyRequest = {
      ...BASE,
      stop_loss_pct: 5,
      trailing_stop: { type: 'pct', value: 3, source: 'high', activate_on_profit: false, activate_pct: 0 },
    }
    const opts = buildParamOptions(req, 5)
    const groups = groupParamOptions(opts)
    const riskGroup = groups.find(g => g.group === 'Risk')
    expect(riskGroup).toBeDefined()
    const paths = riskGroup!.options.map(o => o.path)
    expect(paths).toContain('stop_loss_pct')
    expect(paths).toContain('trailing_stop_value')
  })
})

describe('groupParamOptions — Costs group', () => {
  it('slippage_bps lands in Costs group', () => {
    const opts = buildParamOptions(BASE, 5)
    const groups = groupParamOptions(opts)
    const costsGroup = groups.find(g => g.group === 'Costs')
    expect(costsGroup).toBeDefined()
    expect(costsGroup!.options.map(o => o.path)).toContain('slippage_bps')
  })
})
