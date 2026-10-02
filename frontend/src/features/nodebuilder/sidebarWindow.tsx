/**
 * "From sidebar" Ticker params (plan D11 row "Primary symbol and interval").
 *
 * Until W5 makes the group's primary Ticker node the owner, every graph run,
 * auto-cook preview and Data Sheet cook fetches the SIDEBAR's symbol and
 * interval (graphRun.buildGraphRequest). In W4 every Ticker node reads that
 * one fetched frame (reference tickers come in W5). So in the app, a Ticker
 * node's own `symbol` and `interval` would be edited and silently ignored.
 * Instead the node and the Inspector show the sidebar values, read-only,
 * tagged "from sidebar" (UX-01).
 *
 * NodeBuilder provides the window when App drives it (`graphWindow`). On its
 * own (tests, standalone runs) there is no window and the params stay
 * editable, as before W4.
 *
 * W5: only the implicit group's primary Ticker (no prefix, in a graph with
 * no Output Group) still runs on the sidebar's symbol and interval
 * (`sidebarOwnsTicker`, ownership.ts). A reference Ticker and the Tickers
 * of a graph with groups keep their own, editable values.
 */

import { createContext, useContext, type CSSProperties } from 'react'
import { useNodeBuilderStore } from './store'
import type { GraphWindow } from './graphRun'
import { sidebarOwnsTicker } from './ownership'

export const SidebarWindowContext = createContext<GraphWindow | null>(null)

/** The tag text next to a sidebar-owned value. */
export const FROM_SIDEBAR = 'from sidebar'
export const FROM_SIDEBAR_TITLE =
  'Set in the sidebar. Until the graph owns the symbol and interval, graph runs use the sidebar values.'

/** The sidebar window NodeBuilder provides, or null. */
export function useSidebarWindow(): GraphWindow | null {
  return useContext(SidebarWindowContext)
}

/** The sidebar value for a Ticker param it owns, or null when it does not. */
export function sidebarTickerValue(win: GraphWindow | null, nodeType: string | undefined, paramKey: string): string | null {
  if (win == null || nodeType !== 'ticker') return null
  if (paramKey === 'symbol') return win.ticker
  if (paramKey === 'interval') return win.interval
  return null
}

/** Hook form for a param row: the sidebar value, or null. */
export function useSidebarParamValue(nodeId: string, paramKey: string): string | null {
  const win = useSidebarWindow()
  const nodeType = useNodeBuilderStore(s => {
    if (!win) return undefined
    const node = s.graph?.nodes[nodeId]
    return node && sidebarOwnsTicker(node.params, s.graph) ? node.type : undefined
  })
  return sidebarTickerValue(win, nodeType, paramKey)
}

const rowStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  fontFamily: 'var(--nb-font-mono)',
  fontSize: 10,
  color: 'var(--nb-text-muted)',
  lineHeight: '14px',
  minHeight: 16,
}

const valueStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  color: 'var(--nb-text)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}

const tagStyle: CSSProperties = {
  flexShrink: 0,
  fontSize: 9,
  padding: '0 4px',
  borderRadius: 3,
  border: '1px solid var(--nb-border)',
  color: 'var(--nb-text-muted)',
}

/** A read-only param row: label, the sidebar value, and the "from sidebar" tag. */
export function FromSidebarRow({ label, value, testId }: { label: string; value: string; testId: string }) {
  return (
    <div style={rowStyle} data-testid={testId} data-from-sidebar="true" title={FROM_SIDEBAR_TITLE}>
      <span style={{ flexShrink: 0 }}>{label}</span>
      <span style={valueStyle} aria-readonly="true">{value}</span>
      <span style={tagStyle}>{FROM_SIDEBAR}</span>
    </div>
  )
}
