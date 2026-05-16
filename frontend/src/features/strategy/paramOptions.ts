import type { StrategyRequest, Rule } from '../../shared/types'

export interface ParamOption {
  path: string
  label: string
  defaultMin: number
  defaultMax: number
  defaultSteps: number
  currentValue: number | null
  isInteger?: boolean
}

export function buildParamOptions(req: StrategyRequest, defaultSteps = 9): ParamOption[] {
  const opts: ParamOption[] = []

  req.buy_rules.forEach((rule: Rule, i: number) => {
    if (rule.value != null) {
      opts.push({
        path: `buy_rule_${i}_value`,
        label: `Buy Rule ${i + 1} Threshold (${rule.indicator.toUpperCase()})`,
        defaultMin: Math.max(1, (rule.value ?? 30) * 0.5),
        defaultMax: (rule.value ?? 30) * 1.5,
        defaultSteps, currentValue: rule.value,
      })
    }
    if (rule.params) {
      Object.entries(rule.params).forEach(([key, val]) => {
        if (typeof val === 'number') {
          opts.push({
            path: `buy_rule_${i}_params_${key}`,
            label: `Buy Rule ${i + 1} ${key} (${rule.indicator.toUpperCase()})`,
            defaultMin: Math.max(1, Math.round(val * 0.5)),
            defaultMax: Math.round(val * 2),
            defaultSteps, currentValue: val,
            isInteger: Number.isInteger(val),
          })
        }
      })
    }
  })

  req.sell_rules.forEach((rule: Rule, i: number) => {
    if (rule.value != null) {
      opts.push({
        path: `sell_rule_${i}_value`,
        label: `Sell Rule ${i + 1} Threshold (${rule.indicator.toUpperCase()})`,
        defaultMin: Math.max(1, (rule.value ?? 70) * 0.5),
        defaultMax: (rule.value ?? 70) * 1.5,
        defaultSteps, currentValue: rule.value,
      })
    }
    if (rule.params) {
      Object.entries(rule.params).forEach(([key, val]) => {
        if (typeof val === 'number') {
          opts.push({
            path: `sell_rule_${i}_params_${key}`,
            label: `Sell Rule ${i + 1} ${key} (${rule.indicator.toUpperCase()})`,
            defaultMin: Math.max(1, Math.round(val * 0.5)),
            defaultMax: Math.round(val * 2),
            defaultSteps, currentValue: val,
            isInteger: Number.isInteger(val),
          })
        }
      })
    }
  })

  if (req.stop_loss_pct != null) {
    opts.push({
      path: 'stop_loss_pct', label: 'Stop Loss %',
      defaultMin: Math.max(0.1, req.stop_loss_pct * 0.3),
      defaultMax: req.stop_loss_pct * 2,
      defaultSteps, currentValue: req.stop_loss_pct,
    })
  }
  if (req.trailing_stop?.value != null) {
    opts.push({
      path: 'trailing_stop_value', label: 'Trailing Stop Value',
      defaultMin: Math.max(0.1, req.trailing_stop.value * 0.3),
      defaultMax: req.trailing_stop.value * 2,
      defaultSteps, currentValue: req.trailing_stop.value,
    })
  }
  opts.push({
    path: 'slippage_bps', label: 'Slippage (bps)',
    defaultMin: 0, defaultMax: 20,
    defaultSteps, currentValue: req.slippage_bps ?? 2,
  })

  return opts
}

/**
 * Apply a single optimizer param-path write to a StrategyRequest, returning a
 * new request object (immutable update). Mirrors the path encoding used by
 * buildParamOptions so the read/write pair stays colocated.
 *
 * Supported paths:
 *   buy_rule_${i}_value            → req.buy_rules[i].value
 *   buy_rule_${i}_params_${key}    → req.buy_rules[i].params[key]
 *   sell_rule_${i}_value           → req.sell_rules[i].value
 *   sell_rule_${i}_params_${key}   → req.sell_rules[i].params[key]
 *   stop_loss_pct                  → req.stop_loss_pct
 *   trailing_stop_value            → req.trailing_stop.value (other fields preserved)
 *   slippage_bps                   → req.slippage_bps
 */
export function applyParamPath(req: StrategyRequest, path: string, value: number): StrategyRequest {
  // buy_rule_<i>_value or sell_rule_<i>_value
  const ruleValueMatch = path.match(/^(buy|sell)_rule_(\d+)_value$/)
  if (ruleValueMatch) {
    const side = ruleValueMatch[1] as 'buy' | 'sell'
    const idx = parseInt(ruleValueMatch[2], 10)
    const key = side === 'buy' ? 'buy_rules' : 'sell_rules'
    const rules = req[key]
    if (idx < 0 || idx >= rules.length) return req
    return {
      ...req,
      [key]: rules.map((r, i) => i === idx ? { ...r, value } : r),
    }
  }

  // buy_rule_<i>_params_<key> or sell_rule_<i>_params_<key>
  const ruleParamsMatch = path.match(/^(buy|sell)_rule_(\d+)_params_(.+)$/)
  if (ruleParamsMatch) {
    const side = ruleParamsMatch[1] as 'buy' | 'sell'
    const idx = parseInt(ruleParamsMatch[2], 10)
    const paramKey = ruleParamsMatch[3]
    const key = side === 'buy' ? 'buy_rules' : 'sell_rules'
    const rules = req[key]
    if (idx < 0 || idx >= rules.length) return req
    const rule = rules[idx]
    const currentVal = rule.params?.[paramKey]
    // Preserve integer vs float: if existing value is integer, round the new one.
    const finalVal = typeof currentVal === 'number' && Number.isInteger(currentVal) ? Math.round(value) : value
    return {
      ...req,
      [key]: rules.map((r, i) =>
        i === idx ? { ...r, params: { ...(r.params ?? {}), [paramKey]: finalVal } } : r
      ),
    }
  }

  if (path === 'stop_loss_pct') {
    return { ...req, stop_loss_pct: value }
  }

  if (path === 'trailing_stop_value') {
    if (!req.trailing_stop) return req
    return { ...req, trailing_stop: { ...req.trailing_stop, value } }
  }

  if (path === 'slippage_bps') {
    return { ...req, slippage_bps: value }
  }

  return req
}

export function linspace(min: number, max: number, steps: number): number[] {
  if (steps <= 1) return [min]
  const out: number[] = []
  for (let i = 0; i < steps; i++) {
    out.push(+(min + (max - min) * i / (steps - 1)).toFixed(4))
  }
  return out
}
