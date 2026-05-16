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

  // In regime mode the engine reads `long_*_rules` / `short_*_rules`; the bare
  // `buy_rules` / `sell_rules` are populated for UI symmetry but not used by
  // the backtest. Sweeping the bare lists yields zero variance (silent footgun)
  // so we emit paths into the rule lists the engine actually consumes.
  const regimeOn = req.regime?.enabled === true
  type RuleListSpec = {
    rules: Rule[]
    pathPrefix: string  // e.g. "long_buy_rule" — singular_rule
    labelPrefix: string // e.g. "Long Entry Rule"
  }
  const ruleLists: RuleListSpec[] = regimeOn
    ? [
        { rules: req.long_buy_rules  ?? [], pathPrefix: 'long_buy_rule',   labelPrefix: 'Long Entry Rule' },
        { rules: req.long_sell_rules ?? [], pathPrefix: 'long_sell_rule',  labelPrefix: 'Long Exit Rule' },
        { rules: req.short_buy_rules ?? [], pathPrefix: 'short_buy_rule',  labelPrefix: 'Short Entry Rule' },
        { rules: req.short_sell_rules?? [], pathPrefix: 'short_sell_rule', labelPrefix: 'Short Exit Rule' },
      ]
    : [
        { rules: req.buy_rules,  pathPrefix: 'buy_rule',  labelPrefix: 'Buy Rule' },
        { rules: req.sell_rules, pathPrefix: 'sell_rule', labelPrefix: 'Sell Rule' },
      ]

  for (const spec of ruleLists) {
    spec.rules.forEach((rule: Rule, i: number) => {
      if (rule.value != null) {
        opts.push({
          path: `${spec.pathPrefix}_${i}_value`,
          label: `${spec.labelPrefix} ${i + 1} Threshold (${rule.indicator.toUpperCase()})`,
          defaultMin: Math.max(1, (rule.value ?? 30) * 0.5),
          defaultMax: (rule.value ?? 30) * 1.5,
          defaultSteps, currentValue: rule.value,
        })
      }
      if (rule.params) {
        Object.entries(rule.params).forEach(([key, val]) => {
          if (typeof val === 'number') {
            opts.push({
              path: `${spec.pathPrefix}_${i}_params_${key}`,
              label: `${spec.labelPrefix} ${i + 1} ${key} (${rule.indicator.toUpperCase()})`,
              defaultMin: Math.max(1, Math.round(val * 0.5)),
              defaultMax: Math.round(val * 2),
              defaultSteps, currentValue: val,
              isInteger: Number.isInteger(val),
            })
          }
        })
      }
    })
  }

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
  // (long|short)?(buy|sell)_rule_<i>_value
  const ruleValueMatch = path.match(/^(long_|short_)?(buy|sell)_rule_(\d+)_value$/)
  if (ruleValueMatch) {
    const prefix = ruleValueMatch[1] ?? ''        // 'long_' | 'short_' | ''
    const side = ruleValueMatch[2] as 'buy' | 'sell'
    const idx = parseInt(ruleValueMatch[3], 10)
    const key = `${prefix}${side}_rules` as keyof StrategyRequest
    const rules = (req[key] ?? []) as Rule[]
    if (idx < 0 || idx >= rules.length) return req
    return {
      ...req,
      [key]: rules.map((r, i) => i === idx ? { ...r, value } : r),
    } as StrategyRequest
  }

  // (long|short)?(buy|sell)_rule_<i>_params_<key>
  const ruleParamsMatch = path.match(/^(long_|short_)?(buy|sell)_rule_(\d+)_params_(.+)$/)
  if (ruleParamsMatch) {
    const prefix = ruleParamsMatch[1] ?? ''
    const side = ruleParamsMatch[2] as 'buy' | 'sell'
    const idx = parseInt(ruleParamsMatch[3], 10)
    const paramKey = ruleParamsMatch[4]
    const key = `${prefix}${side}_rules` as keyof StrategyRequest
    const rules = (req[key] ?? []) as Rule[]
    if (idx < 0 || idx >= rules.length) return req
    const rule = rules[idx]
    const currentVal = rule.params?.[paramKey]
    const finalVal = typeof currentVal === 'number' && Number.isInteger(currentVal) ? Math.round(value) : value
    return {
      ...req,
      [key]: rules.map((r, i) =>
        i === idx ? { ...r, params: { ...(r.params ?? {}), [paramKey]: finalVal } } : r
      ),
    } as StrategyRequest
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

// ---------------------------------------------------------------------------
// Grouped param options (F237)
// ---------------------------------------------------------------------------

export interface ParamGroup {
  group: string
  options: ParamOption[]
}

/**
 * Groups a flat ParamOption[] by rule, returning an ordered list of groups.
 * Group names: "Buy Rule 1", "Buy Rule 2", …, "Sell Rule 1", …, "Risk", "Costs".
 * Internal order within each group is preserved.
 */
export function groupParamOptions(opts: ParamOption[]): ParamGroup[] {
  const map = new Map<string, ParamOption[]>()

  const groupKey = (path: string): string => {
    const m = path.match(/^(long_|short_)?(buy|sell)_rule_(\d+)_/)
    if (m) {
      const prefix = m[1] ?? ''
      const dirLabel = prefix === 'long_' ? 'Long ' : prefix === 'short_' ? 'Short ' : ''
      const sideLabel = m[2] === 'buy'
        ? (prefix ? 'Entry' : 'Buy')
        : (prefix ? 'Exit' : 'Sell')
      return `${dirLabel}${sideLabel} Rule ${parseInt(m[3], 10) + 1}`
    }
    if (path === 'slippage_bps' || path === 'borrow_rate_annual') return 'Costs'
    return 'Risk'
  }

  for (const opt of opts) {
    const key = groupKey(opt.path)
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(opt)
  }

  return Array.from(map.entries()).map(([group, options]) => ({ group, options }))
}

/**
 * Returns true when the strategy has more than 2 rules total (buy + sell),
 * triggering optgroup rendering in the param selects.
 */
export function shouldGroupParamOptions(req: StrategyRequest): boolean {
  // In regime mode count the regime rule arrays (which the engine actually
  // uses); otherwise fall back to the bare buy/sell pair.
  if (req.regime?.enabled) {
    const n =
      (req.long_buy_rules?.length ?? 0) +
      (req.long_sell_rules?.length ?? 0) +
      (req.short_buy_rules?.length ?? 0) +
      (req.short_sell_rules?.length ?? 0)
    return n > 2
  }
  return req.buy_rules.length + req.sell_rules.length > 2
}

export function linspace(min: number, max: number, steps: number): number[] {
  if (steps <= 1) return [min]
  const out: number[] = []
  for (let i = 0; i < steps; i++) {
    out.push(+(min + (max - min) * i / (steps - 1)).toFixed(4))
  }
  return out
}
