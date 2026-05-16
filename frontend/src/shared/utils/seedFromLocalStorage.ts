/**
 * seedFromLocalStorage.ts
 *
 * One-shot migration: on first page load after the backend API is introduced,
 * pushes any existing localStorage data to the backend seed endpoints.
 * The endpoints only write when the server-side store is empty, so this is
 * safe to call on every browser but will only do work once per server.
 *
 * After running (success or skip), sets 'strategylab-seed-attempted' = '1'
 * to prevent re-attempts. localStorage data is intentionally kept as a backup.
 */

import { WATCHLIST_KEY } from '../../features/watchlist/watchlistStorage'
import { SAVED_STRATEGIES_KEY } from '../../features/strategy/savedStrategies'

const SEED_ATTEMPTED_KEY = 'strategylab-seed-attempted'

const API_BASE = (import.meta as any).env?.VITE_API_URL as string || 'http://localhost:8000'

export async function seedFromLocalStorageIfAny(): Promise<void> {
  if (localStorage.getItem(SEED_ATTEMPTED_KEY) === '1') return

  // Mark attempted before the async work so a crash/reload doesn't retry
  localStorage.setItem(SEED_ATTEMPTED_KEY, '1')

  const watchlistRaw = localStorage.getItem(WATCHLIST_KEY)
  const strategiesRaw = localStorage.getItem(SAVED_STRATEGIES_KEY)

  if (!watchlistRaw && !strategiesRaw) return

  let watchlistSeeded = false
  let strategiesSeeded = false

  if (watchlistRaw) {
    try {
      const state = JSON.parse(watchlistRaw)
      const resp = await fetch(`${API_BASE}/watchlist/seed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state),
      })
      if (resp.ok) {
        const result: { seeded: boolean } = await resp.json()
        if (result.seeded) watchlistSeeded = true
      }
    } catch {
      // Network failure — skip silently
    }
  }

  if (strategiesRaw) {
    try {
      const list = JSON.parse(strategiesRaw)
      const resp = await fetch(`${API_BASE}/strategies/seed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(list),
      })
      if (resp.ok) {
        const result: { seeded: boolean } = await resp.json()
        if (result.seeded) strategiesSeeded = true
      }
    } catch {
      // Network failure — skip silently
    }
  }

  if (watchlistSeeded || strategiesSeeded) {
    console.info('[StrategyLab] Migrated local data to server.')
    // Surface a brief toast if the app exposes a notification mechanism.
    // We use a CustomEvent so App.tsx can optionally listen without a direct import.
    try {
      window.dispatchEvent(
        new CustomEvent('strategylab:toast', {
          detail: { message: 'Migrated your local data to the server.', type: 'info' },
        })
      )
    } catch {
      // Non-critical
    }
  }
}
