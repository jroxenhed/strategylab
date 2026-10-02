/**
 * Minimap colors (F435 W3 item 3.H, spec S22): each React Flow node's fill
 * in the minimap, read from the design tokens.
 */

import type { Node as RFNode } from '@xyflow/react'
import { boxTintVar } from './store/annotations'

/**
 * Fallbacks for the color tokens the minimap uses, the same values as
 * tokens.css. Used when the stylesheet cannot be read (tests); the live page
 * reads the tokens themselves, so a token change there wins.
 */
export const TOKEN_FALLBACK: Record<string, string> = {
  '--nb-cat-ticker': '#22d3ee',
  '--nb-cat-data': '#60a5fa',
  '--nb-cat-indicator': '#34d399',
  '--nb-cat-comparison': '#fbbf24',
  '--nb-cat-logic': '#fb923c',
  '--nb-cat-signal': '#2dd4bf',
  '--nb-cat-rules': '#f87171',
  '--nb-cat-settings': '#a3adc2',
  '--nb-cat-code': '#c084fc',
  '--nb-cat-output': '#f1f5f9',
  '--nb-cat-network': '#818cf8',
  '--nb-note-border': 'rgba(217, 119, 6, 0.4)',
  '--nb-text-dim': '#7a8296',
  '--nb-text-muted': '#98a1b3',
  '--nb-selection': '#38bdf8',
}

const tokenCache = new Map<string, string>()

/** The value of a color token (`--nb-cat-indicator` → `#34d399`). */
export function tokenColor(name: string): string {
  const cached = tokenCache.get(name)
  if (cached) return cached
  let value = ''
  try {
    const root = document.querySelector('.nodebuilder-root') ?? document.documentElement
    value = getComputedStyle(root).getPropertyValue(name).trim()
  } catch {
    value = ''
  }
  if (value) {
    tokenCache.set(name, value)
    return value
  }
  // Not cached: the stylesheet may simply not be applied yet.
  return TOKEN_FALLBACK[name] ?? TOKEN_FALLBACK['--nb-text-dim']
}

/** `var(--x)` → `--x`; anything else unchanged. */
function varName(v: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(v.trim())
  return m ? m[1] : v
}

/** The minimap fill of a React Flow node (S22). */
export function minimapNodeColor(n: RFNode): string {
  const data = (n.data ?? {}) as { catalog?: { cat?: string } | null; box?: { color?: string | null } }
  if (n.type === 'nbBox') return tokenColor(varName(boxTintVar(data.box?.color)))
  if (n.type === 'nbNote') return tokenColor('--nb-note-border')
  const cat = data.catalog?.cat
  if (!cat) return tokenColor('--nb-text-dim')
  const token = `--nb-cat-${cat}`
  return token in TOKEN_FALLBACK ? tokenColor(token) : tokenColor('--nb-text-dim')
}

/**
 * The minimap fill with the S13 rule applied (UX-22): nodes in `unsupported`
 * (known types the canvas draws as the unsupported card, as well as unknown
 * types) are `--nb-text-dim`; everything else as `minimapNodeColor`.
 */
export function minimapNodeColorWith(unsupported: ReadonlySet<string>): (n: RFNode) => string {
  if (unsupported.size === 0) return minimapNodeColor
  return n => (unsupported.has(n.id) ? tokenColor('--nb-text-dim') : minimapNodeColor(n))
}
