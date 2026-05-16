/**
 * F-UX3: Rule-threshold validation helpers.
 *
 * NEEDS_VALUE_CONDITIONS mirrors the NEEDS_VALUE list in RuleRow.tsx.
 * Keep them in sync — RuleRow.tsx is the UI source of truth, this file
 * is the testable logic layer. If you add a condition to NEEDS_VALUE in
 * RuleRow.tsx you must also add it here, and vice versa.
 *
 * Threshold-free conditions (whitelist): crossover_up, crossover_down,
 * crosses_above_signal, crosses_below_signal, turns_up, turns_down,
 * rising, falling, decelerating, accelerating, rising_over, falling_over
 * are NOT in NEEDS_VALUE so they pass automatically.
 *
 * Cross-reference: backend/signal_engine.py ALLOWED_CONDITIONS for the
 * canonical set of condition strings accepted by the server.
 */

import type { Rule } from '../../shared/types'

/** Conditions that require a numeric threshold value. Mirrors RuleRow.tsx NEEDS_VALUE. */
export const NEEDS_VALUE_CONDITIONS: ReadonlySet<string> = new Set([
  'above',
  'below',
  'crosses_above',
  'crosses_below',
  'turns_up_below',
  'turns_down_above',
  'rising_over',
  'falling_over',
])

/** Ref-style params that substitute for a numeric value (cross-reference params). */
const REF_PARAMS: ReadonlySet<string> = new Set(['signal', 'close', 'd'])

function isRefParam(param?: string): boolean {
  if (!param) return false
  if (REF_PARAMS.has(param)) return true
  return (
    param.startsWith('ma:') ||
    param.startsWith('bb:') ||
    param.startsWith('atr:') ||
    param.startsWith('volume_sma:') ||
    param.startsWith('stoch:') ||
    param.startsWith('adx:')
  )
}

/** Forced-param conditions that don't need a separate numeric value. */
const FORCED_PARAM_CONDITIONS: ReadonlyMap<string, string[]> = new Map([
  ['macd', ['crossover_up', 'crossover_down']],
  ['stochastic', ['crossover_up', 'crossover_down']],
])

/**
 * Returns true when this single rule is in an invalid state:
 * it requires a numeric threshold but has none, and is not muted.
 */
export function isRuleInvalid(rule: Rule): boolean {
  if (rule.muted) return false
  const hasForcedParam = FORCED_PARAM_CONDITIONS.get(rule.indicator)?.includes(rule.condition) ?? false
  const hasRefParam = isRefParam(rule.param) || hasForcedParam
  const needsValue = NEEDS_VALUE_CONDITIONS.has(rule.condition) && !hasRefParam
  if (!needsValue) return false
  return typeof rule.value !== 'number' || isNaN(rule.value)
}

/**
 * Returns true when any rule in any of the provided rule lists is invalid.
 * Pass all rule arrays for the active strategy (buy, sell, long/short variants, regime).
 */
export function hasAnyInvalidRule(...ruleLists: (Rule[] | null | undefined)[]): boolean {
  for (const list of ruleLists) {
    if (!list) continue
    for (const rule of list) {
      if (isRuleInvalid(rule)) return true
    }
  }
  return false
}
