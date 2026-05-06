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

  return opts
}

export function linspace(min: number, max: number, steps: number): number[] {
  if (steps <= 1) return [min]
  const out: number[] = []
  for (let i = 0; i < steps; i++) {
    out.push(+(min + (max - min) * i / (steps - 1)).toFixed(4))
  }
  return out
}
