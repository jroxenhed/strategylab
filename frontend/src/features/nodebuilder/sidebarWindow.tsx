/**
 * The sidebar window in the node builder (plan D11, F453).
 *
 * The graph owns its symbol and interval: every editor run uses the primary
 * Ticker's own values (graphTicker.ts). The sidebar window is still handed
 * down, because a few places read it:
 * - a new Ticker node starts on the sidebar's symbol and interval;
 * - the Inspector's "Copy from sidebar" action copies them into a
 *   primary Ticker, once;
 * - a primary Ticker with an empty symbol falls back to the sidebar's
 *   values (the card shows them, tagged "uses sidebar"), and so does the
 *   Spawn dialog.
 *
 * NodeBuilder provides the window when App drives it (`graphWindow`). On its
 * own (tests, standalone runs) there is no window.
 */

import { createContext, useContext } from 'react'
import type { GraphWindow } from './graphRun'

export const SidebarWindowContext = createContext<GraphWindow | null>(null)

/** The tag on a primary Ticker card whose empty symbol falls back to the sidebar. */
export const USES_SIDEBAR = 'uses sidebar'

/** The sidebar window NodeBuilder provides, or null. */
export function useSidebarWindow(): GraphWindow | null {
  return useContext(SidebarWindowContext)
}
