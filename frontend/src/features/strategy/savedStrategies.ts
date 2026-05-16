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

const API_BASE = (import.meta as any).env?.VITE_API_URL as string || 'http://localhost:8000'

function applyMigrations(strategies: SavedStrategy[]): SavedStrategy[] {
  for (const s of strategies) {
    if (s.buyRules) s.buyRules = s.buyRules.map(migrateRule)
    if (s.sellRules) s.sellRules = s.sellRules.map(migrateRule)
    if (!s.strategyType) {
      if (s.regime?.enabled === true) {
        s.strategyType = 'regime'
      } else if (s.direction === 'short') {
        s.strategyType = 'short'
      } else {
        s.strategyType = 'long'
      }
    }
  }
  return strategies
}

/**
 * loadSavedStrategies — fetches from the backend API, falls back to
 * localStorage on network failure.
 */
export async function loadSavedStrategies(): Promise<SavedStrategy[]> {
  try {
    const resp = await fetch(`${API_BASE}/strategies`)
    if (resp.ok) {
      const data: SavedStrategy[] = await resp.json()
      // Mirror to localStorage as backup
      localStorage.setItem(SAVED_STRATEGIES_KEY, JSON.stringify(data))
      return applyMigrations(data)
    }
  } catch {
    // Network failure — fall through to localStorage fallback
  }

  try {
    const raw = localStorage.getItem(SAVED_STRATEGIES_KEY)
    if (!raw) return []
    const strategies: SavedStrategy[] = JSON.parse(raw)
    return applyMigrations(strategies)
  } catch { return [] }
}

/**
 * saveSavedStrategies — PUTs the full list to the backend API and mirrors
 * to localStorage.
 */
export async function saveSavedStrategies(strategies: SavedStrategy[]): Promise<void> {
  localStorage.setItem(SAVED_STRATEGIES_KEY, JSON.stringify(strategies))
  try {
    await fetch(`${API_BASE}/strategies`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(strategies),
    })
  } catch {
    // Network failure — local mirror preserved
  }
}
