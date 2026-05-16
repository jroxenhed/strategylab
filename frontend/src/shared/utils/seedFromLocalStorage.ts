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

// Version-suffixed key so this deploy retries any browser whose previous
// `strategylab-seed-attempted` flag was set by the broken (wrong-URL) build.
const SEED_ATTEMPTED_KEY = 'strategylab-seed-attempted-v2'

const API_BASE = (import.meta as any).env?.VITE_API_URL as string || 'http://localhost:8000'

export async function seedFromLocalStorageIfAny(): Promise<void> {
  // The /seed endpoints are server-idempotent (only write when empty). We run
  // the seed unconditionally until BOTH server stores acknowledge a 200
  // response — only then mark seed-attempted. This protects against an
  // earlier broken build that set the flag before any successful POST.
  if (localStorage.getItem(SEED_ATTEMPTED_KEY) === '1') return

  const watchlistRaw = localStorage.getItem(WATCHLIST_KEY)
  const strategiesRaw = localStorage.getItem(SAVED_STRATEGIES_KEY)

  if (!watchlistRaw && !strategiesRaw) {
    // Nothing to seed — mark attempted and exit
    localStorage.setItem(SEED_ATTEMPTED_KEY, '1')
    return
  }

  let watchlistSeeded = false
  let strategiesSeeded = false
  let watchlistAck = !watchlistRaw // treat absence as already-acked
  let strategiesAck = !strategiesRaw

  if (watchlistRaw) {
    try {
      const state = JSON.parse(watchlistRaw)
      const resp = await fetch(`${API_BASE}/api/trading/watchlist/seed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state),
      })
      if (resp.ok) {
        const result: { seeded: boolean } = await resp.json()
        if (result.seeded) watchlistSeeded = true
        watchlistAck = true
      }
    } catch {
      // Network failure — skip silently; flag remains unset → retry next load
    }
  }

  if (strategiesRaw) {
    try {
      const list = JSON.parse(strategiesRaw)
      const resp = await fetch(`${API_BASE}/api/strategies/seed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(list),
      })
      if (resp.ok) {
        const result: { seeded: boolean } = await resp.json()
        if (result.seeded) strategiesSeeded = true
        strategiesAck = true
      }
    } catch {
      // Network failure — skip silently; flag remains unset → retry next load
    }
  }

  // Mark attempted only when both endpoints responded successfully — otherwise
  // a transient network failure (or a wrong-URL deploy) would permanently
  // block the migration.
  if (watchlistAck && strategiesAck) {
    localStorage.setItem(SEED_ATTEMPTED_KEY, '1')
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
