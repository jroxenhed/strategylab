/**
 * State and plain helpers for the Spawn bots dialog (S34). The components
 * live in SpawnBotsDialog.tsx; this module holds what is not a component,
 * so the dialog file stays fast-refresh friendly.
 *
 * - `useSpawnUi`: whether the dialog is open, a pending "spawn after the
 *   graph opens" request from the bot card, and the "Created" toast.
 * - The browser-remembered broker and data source (never capital or names).
 * - The default bot name and the plain copy for each server error code.
 */

import { create } from 'zustand'
import { OPEN_GRAPH_EVENT, type OpenGraphDetail } from './graphLinks'
import type { GraphGroupInfo } from './graphGroups'
import { plainErrorText, type GraphActionError, type SpawnBroker, type SpawnDataSource } from '../../api/graphSpawn'

// ---------------------------------------------------------------------------
// Open state, the pending "spawn after open" request, and the toast
// ---------------------------------------------------------------------------

interface SpawnToast {
  text: string
  botId: string | null
  seq: number
}

interface SpawnUiState {
  open: boolean
  /** A graph id the Trading view asked to spawn from; the dialog opens once it is loaded. */
  pendingGraphId: string | null
  toast: SpawnToast | null
}

export const useSpawnUi = create<SpawnUiState>(() => ({ open: false, pendingGraphId: null, toast: null }))

export function openSpawnDialog(): void {
  useSpawnUi.setState({ open: true })
}

export function closeSpawnDialog(): void {
  useSpawnUi.setState({ open: false })
}

let toastSeq = 0
export function showSpawnToast(text: string, botId: string | null): void {
  toastSeq += 1
  useSpawnUi.setState({ toast: { text, botId, seq: toastSeq } })
}

/** Drop a pending "spawn after open" request for `graphId` (the open was cancelled or failed). */
export function clearPendingSpawn(graphId: string): void {
  if (useSpawnUi.getState().pendingGraphId === graphId) useSpawnUi.setState({ pendingGraphId: null })
}

export function resetSpawnUi(): void {
  useSpawnUi.setState({ open: false, pendingGraphId: null, toast: null })
}

/**
 * The window listener for "open this graph and spawn" (BotCard's `Spawn…`
 * link, S35). App opens the graph; this remembers to open the dialog once
 * that graph is the one in the editor. Returns the remover.
 */
export function listenForSpawnRequests(): () => void {
  if (typeof window === 'undefined') return () => {}
  const onOpen = (e: Event) => {
    const d = (e as CustomEvent<OpenGraphDetail>).detail
    if (d && d.spawn && typeof d.graphId === 'string') useSpawnUi.setState({ pendingGraphId: d.graphId })
  }
  window.addEventListener(OPEN_GRAPH_EVENT, onOpen)
  return () => window.removeEventListener(OPEN_GRAPH_EVENT, onOpen)
}

// ---------------------------------------------------------------------------
// Browser-remembered broker and data source (never capital or names)
// ---------------------------------------------------------------------------

export const BROKER_KEY = 'nb.spawn.broker'
export const SOURCE_KEY = 'nb.spawn.source'

export const BROKERS: { value: SpawnBroker; label: string }[] = [
  { value: 'alpaca', label: 'Alpaca' },
  { value: 'ibkr', label: 'IBKR' },
]

export const SOURCES: { value: SpawnDataSource; label: string }[] = [
  { value: 'yahoo', label: 'Yahoo' },
  { value: 'alpaca', label: 'Alpaca SIP' },
  { value: 'alpaca-iex', label: 'Alpaca IEX' },
  { value: 'ibkr', label: 'IBKR' },
]

/** `Alpaca IEX` for `alpaca-iex`; an unknown source reads as itself. */
export function sourceLabel(v: string): string {
  return SOURCES.find(s => s.value === v)?.label ?? v
}

export function isBroker(v: unknown): v is SpawnBroker {
  return v === 'alpaca' || v === 'ibkr'
}

export function isSource(v: unknown): v is SpawnDataSource {
  return v === 'yahoo' || v === 'alpaca' || v === 'alpaca-iex' || v === 'ibkr'
}

export function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Private mode or blocked storage: the default is fine next time.
  }
}

/** The data source that goes with a broker when the user has not picked one. */
export function defaultSourceFor(broker: SpawnBroker): SpawnDataSource {
  return broker === 'ibkr' ? 'ibkr' : 'alpaca-iex'
}

export function initialBroker(): SpawnBroker {
  const v = readPref(BROKER_KEY)
  return isBroker(v) ? v : 'alpaca'
}

export function initialSource(broker: SpawnBroker): SpawnDataSource {
  const v = readPref(SOURCE_KEY)
  return isSource(v) ? v : defaultSourceFor(broker)
}

/** The default bot name (`strategy_name`), S34. */
export function defaultBotName(graphName: string, group: string): string {
  return `${graphName} ▸ ${group}`
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

export function dirWord(d: GraphGroupInfo['direction']): string {
  return d === 'regime_switch' ? 'switch' : d ?? ''
}

/** The group names a 400 body names: `detail.group`, `detail.groups`, else the names its message mentions. */
export function namedGroups(err: GraphActionError, groups: readonly GraphGroupInfo[]): Set<string> {
  const out = new Set<string>()
  const d = err.detail
  if (d) {
    if (typeof d.group === 'string') out.add(d.group)
    if (Array.isArray(d.groups)) for (const g of d.groups) if (typeof g === 'string') out.add(g)
  }
  if (out.size === 0 && err.message) {
    for (const g of groups) {
      const re = new RegExp(`(^|[^a-z0-9_])${g.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^a-z0-9_]|$)`)
      if (re.test(err.message)) out.add(g.name)
    }
  }
  return out
}

export interface ErrorView {
  text: string
  action: 'reload' | 'diagnostics' | 'retry' | null
}

/** Plain copy for a failed spawn (S34 states table). */
export function spawnErrorView(err: GraphActionError): ErrorView {
  if (err.kind === 'network') return { text: 'Could not reach the server.', action: 'retry' }
  if (err.status === 409 && err.code === 'rev_conflict') {
    const rev = err.currentRev != null ? `rev ${err.currentRev}` : 'a newer rev'
    return { text: `The graph was saved elsewhere (${rev}). Reload it, then spawn again.`, action: 'reload' }
  }
  if (err.code === 'graph_invalid') return { text: 'The graph has errors.', action: 'diagnostics' }
  if (err.code === 'code_disabled') {
    return { text: 'Code nodes are disabled on this server, so this graph cannot run as a bot.', action: null }
  }
  if (err.code === 'reference_unavailable') {
    // A reference Ticker could not be fetched on a leg's data source.
    const d = err.detail ?? {}
    const sym = typeof d.symbol === 'string' && d.symbol ? d.symbol.toUpperCase()
      : typeof d.ticker === 'string' && d.ticker ? d.ticker.toUpperCase()
        : null
    const src = typeof d.data_source === 'string' && d.data_source ? sourceLabel(d.data_source) : null
    const group = typeof d.group === 'string' && d.group ? d.group : null
    const what = sym ? `the reference ticker ${sym}` : 'a reference ticker'
    const where = src ? ` from ${src}` : ''
    const fix = group ? `Pick another data source for ${group}, or try again later.` : 'Pick another data source, or try again later.'
    return { text: `Could not load ${what}${where}. ${fix}`, action: 'retry' }
  }
  const hasDiags = (err.diagnostics?.length ?? 0) > 0
  return { text: plainErrorText(err), action: hasDiags ? 'diagnostics' : null }
}
