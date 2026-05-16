import { describe, it, expect } from 'vitest'
import { isRuleInvalid, hasAnyInvalidRule } from '../features/strategy/ruleValidation'
import type { Rule } from '../shared/types'

describe('isRuleInvalid', () => {
  it('returns true for is_above condition with null value', () => {
    const rule: Rule = { indicator: 'rsi', condition: 'above', value: undefined }
    expect(isRuleInvalid(rule)).toBe(true)
  })

  it('returns false for is_above condition with numeric value', () => {
    const rule: Rule = { indicator: 'rsi', condition: 'above', value: 30 }
    expect(isRuleInvalid(rule)).toBe(false)
  })

  it('returns false for crossover_up condition with null value (whitelisted — uses forced param, not numeric threshold)', () => {
    // crossover_up is a forced-param condition (requires 'signal' param, not a numeric value),
    // so NEEDS_VALUE_CONDITIONS does not apply.
    const rule: Rule = { indicator: 'macd', condition: 'crossover_up', param: 'signal' }
    expect(isRuleInvalid(rule)).toBe(false)
  })

  it('returns false for muted rule with is_above and null value', () => {
    const rule: Rule = { indicator: 'rsi', condition: 'above', value: undefined, muted: true }
    expect(isRuleInvalid(rule)).toBe(false)
  })

  it('returns true for is_below condition with NaN value', () => {
    const rule: Rule = { indicator: 'rsi', condition: 'below', value: NaN }
    expect(isRuleInvalid(rule)).toBe(true)
  })

  it('returns false for crossover_up (forced-param condition) with null value', () => {
    const rule: Rule = { indicator: 'macd', condition: 'crossover_up', param: 'signal' }
    expect(isRuleInvalid(rule)).toBe(false)
  })

  it('returns false for above condition when a ref-param is supplied', () => {
    const rule: Rule = { indicator: 'price', condition: 'above', param: 'ma:50:ema' }
    expect(isRuleInvalid(rule)).toBe(false)
  })

  it('returns true for crosses_below with null value (crosses_below requires a threshold)', () => {
    const rule: Rule = { indicator: 'rsi', condition: 'crosses_below', value: undefined }
    expect(isRuleInvalid(rule)).toBe(true)
  })

  it('returns false for turns_up with null value (optional threshold)', () => {
    const rule: Rule = { indicator: 'ma', condition: 'turns_up', value: undefined }
    expect(isRuleInvalid(rule)).toBe(false)
  })
})

describe('hasAnyInvalidRule', () => {
  it('returns false when all rule lists are empty', () => {
    expect(hasAnyInvalidRule([], [])).toBe(false)
  })

  it('returns true when one list has an invalid rule', () => {
    const valid: Rule = { indicator: 'rsi', condition: 'above', value: 30 }
    const invalid: Rule = { indicator: 'rsi', condition: 'above', value: undefined }
    expect(hasAnyInvalidRule([valid], [invalid])).toBe(true)
  })

  it('returns false when all rules across lists are valid', () => {
    const r1: Rule = { indicator: 'rsi', condition: 'above', value: 30 }
    const r2: Rule = { indicator: 'macd', condition: 'crossover_up', param: 'signal' }
    expect(hasAnyInvalidRule([r1], [r2])).toBe(false)
  })

  it('ignores null/undefined lists gracefully', () => {
    const valid: Rule = { indicator: 'rsi', condition: 'above', value: 30 }
    expect(hasAnyInvalidRule([valid], null, undefined)).toBe(false)
  })
})
