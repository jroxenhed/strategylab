import type { Rule, SavedStrategy } from '../../shared/types'

export const MA_MIGRATION: Record<string, { period: number; type: string }> = {
  ema20:  { period: 20,  type: 'ema' },
  ema50:  { period: 50,  type: 'ema' },
  ema200: { period: 200, type: 'ema' },
  ma8:    { period: 8,   type: 'sma' },
  ma21:   { period: 21,  type: 'sma' },
}

export const PARAM_MIGRATION: Record<string, string> = {
  ema20:  'ma:20:ema',
  ema50:  'ma:50:ema',
  ema200: 'ma:200:ema',
  ma8:    'ma:8:sma',
  ma21:   'ma:21:sma',
}

export const SAVED_STRATEGIES_KEY = 'strategylab-saved-strategies'

export function migrateRule(rule: Rule): Rule {
  const migrated = { ...rule } as Rule
  const maSpec = MA_MIGRATION[(rule as any).indicator]
  if (maSpec) {
    migrated.indicator = 'ma'
    migrated.params = maSpec
  }
  if (rule.param && PARAM_MIGRATION[rule.param]) {
    migrated.param = PARAM_MIGRATION[rule.param]
  }
  if (migrated.indicator === 'macd' && ['crossover_up', 'crossover_down'].includes(migrated.condition) && !migrated.param) {
    migrated.param = 'signal'
  }
  return migrated
}

export function loadSavedStrategies(): SavedStrategy[] {
  try {
    const raw = localStorage.getItem(SAVED_STRATEGIES_KEY)
    if (!raw) return []
    const strategies: SavedStrategy[] = JSON.parse(raw)
    for (const s of strategies) {
      if (s.buyRules) s.buyRules = s.buyRules.map(migrateRule)
      if (s.sellRules) s.sellRules = s.sellRules.map(migrateRule)
    }
    return strategies
  } catch { return [] }
}
