/**
 * graphText.ts — small pure helpers for the graph toolbar (S01) and the
 * Graph Browser (S02): the Run button's disabled reason, the diagnostics
 * chip label, and the browser's sort and search. Kept apart from the
 * components so they can be tested on their own.
 */

import type { GraphListItem } from '../../api/graphs'

/** The Run button's reason for being disabled, or null when it can run. */
export function runDisabledReason(errorCount: number, hasNodes: boolean): string | null {
  if (errorCount > 0) return `Fix ${errorCount} error${errorCount === 1 ? '' : 's'} to run`
  if (!hasNodes) return 'Add a Ticker and an Output to run'
  return null
}

/** The diagnostics chip's accessible label, e.g. `1 error, 2 warnings`. */
export function diagnosticsLabel(errors: number, warnings: number): string {
  const e = `${errors} error${errors === 1 ? '' : 's'}`
  const w = `${warnings} warning${warnings === 1 ? '' : 's'}`
  return `${e}, ${w}`
}

/** True on macOS and iOS, where the command key is ⌘; elsewhere it is Ctrl. */
export function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } }
  const platform = nav.userAgentData?.platform || nav.platform || ''
  return /mac|iphone|ipad|ipod/i.test(platform)
}

/** A `Cmd`+key cap for this platform: `⌘S` on a Mac, `Ctrl+S` elsewhere. */
export function modKeyCap(key: string, mac: boolean = isMacPlatform()): string {
  return mac ? `⌘${key}` : `Ctrl+${key}`
}

/** S07 notice key for the S05 "validate offline" banner. */
export const VALIDATE_OFFLINE_KEY = 'validate_offline'

/** S05: `Could not reach the server to validate. <detail>` (Retry is the banner action). */
export function validateOfflineText(detail: string | null): string {
  const d = detail?.trim()
  return d ? `Could not reach the server to validate. ${d}` : 'Could not reach the server to validate.'
}

export type BrowserSort = 'updated' | 'name' | 'nodes'

/** Updated sorts newest first; Nodes sorts biggest first. */
export function sortGraphs(items: GraphListItem[], sort: BrowserSort): GraphListItem[] {
  const out = [...items]
  if (sort === 'name') out.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
  else if (sort === 'nodes') out.sort((a, b) => b.node_count - a.node_count)
  else out.sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
  return out
}

/** Case-insensitive substring match on name and description. */
export function filterGraphs(items: GraphListItem[], query: string): GraphListItem[] {
  const q = query.trim().toLowerCase()
  if (!q) return items
  return items.filter(g => g.name.toLowerCase().includes(q) || (g.description ?? '').toLowerCase().includes(q))
}
