/**
 * Shared state of the graph/chart split (spec S28).
 *
 * - `chartOpen`: whether the chart panel is open (mirrors the panel).
 * - `host`: the element inside the open chart panel where App mounts the
 *   app's one `Chart` through a portal (D10: the chart is moved there, never
 *   copied). It is null while the panel is collapsed or the split is not
 *   mounted, and then App mounts no chart in graph view.
 *
 * Kept in its own small module so the `Shift+V` command and App can read it
 * without importing the split's component tree.
 */

import { create } from 'zustand'

export interface ChartPanelApi {
  expand(): void
  collapse(): void
  isCollapsed(): boolean
}

interface GraphSplitState {
  chartOpen: boolean
  host: HTMLElement | null
  setChartOpen(open: boolean): void
  setHost(el: HTMLElement | null): void
}

export const useGraphSplit = create<GraphSplitState>()(set => ({
  chartOpen: false,
  host: null,
  setChartOpen: open => set(s => (s.chartOpen === open ? s : { chartOpen: open })),
  setHost: el => set(s => (s.host === el ? s : { host: el })),
}))

// The mounted split's chart panel, so a command can open or close it.
let panel: ChartPanelApi | null = null

/** GraphChartSplit binds its chart panel here while mounted. */
export function bindChartPanel(api: ChartPanelApi | null): void {
  panel = api
}

/** True when the chart panel is open. */
export function isChartOpen(): boolean {
  return useGraphSplit.getState().chartOpen
}

/** Open, close (or flip, with no argument) the chart panel. False when no split is mounted. */
export function toggleChartPanel(open?: boolean): boolean {
  if (!panel) return false
  const next = open ?? panel.isCollapsed()
  if (next) panel.expand()
  else panel.collapse()
  return true
}

/** The element App portals the chart into, or null (no chart in graph view). */
export function useGraphChartHost(): HTMLElement | null {
  return useGraphSplit(s => s.host)
}
