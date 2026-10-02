/**
 * GraphChartSplit: the graph and its chart as one workspace (spec S28, D10).
 *
 * The canvas column below the toolbar is a vertical split: the canvas on
 * top, then the chart panel (collapsed to its 22px chart bar by default),
 * then the Data Sheet drawer (S25) at the bottom.
 *
 * The chart itself is NOT drawn here. While the chart panel is open, its body
 * element is published in `graphSplitState` (`host`), and App mounts the
 * app's one `Chart` into it through a portal. So there is never a second
 * chart implementation or a second chart instance; closing the panel (or
 * leaving graph view) unmounts the chart through its own cleanup, which nulls
 * its refs before `chart.remove()` (Key Bugs Fixed). Sizing is the library's
 * flex layout plus lightweight-charts `autoSize`; this file adds no
 * resize listener of any kind (F218).
 *
 * The panel layout is remembered in `nb.split`. The Data Sheet keeps its own
 * height and handle (`nb.sheet`, 4.B), so it sits under the split as a drawer.
 */

import { useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { Group, Panel, Separator, useDefaultLayout, usePanelRef } from 'react-resizable-panels'
import type { Graph } from '../../api/nodebuilder'
import { DataSheet } from './DataSheet'
import { entryTimesFromTrades } from './datasheet/trades'
import { toggleSheet, useSheetUi } from './datasheet/sheetUi'
import {
  chartBarSummary,
  chartBarText,
  graphTrades,
  NO_RESULT_TEXT,
  type GraphResultState,
  type GraphWindow,
} from './graphRun'
import { bindChartPanel, useGraphSplit } from './graphSplitState'
import { STALE_TITLE } from './resultsStrip'
import { useNodeBuilderStore, type NodeBuilderState } from './store'
import { Button } from './ui/Button'
import './graphChartSplit.css'

/** Where the panel layout is kept (S28). */
export const SPLIT_STORAGE_ID = 'nb.split'
/** The chart panel's open size the first time it opens, and on a handle double-click. */
export const CHART_OPEN_SIZE = '35%'
const CHART_BAR_PX = 22

// localStorage can throw (private mode, blocked site data): never let that break the split.
const safeStorage = {
  getItem(key: string): string | null {
    try { return localStorage.getItem(key) } catch { return null }
  },
  setItem(key: string, value: string): void {
    try { localStorage.setItem(key, value) } catch { /* ignore */ }
  },
}

export interface ChartBarModel {
  /** The last graph result, or null when there is none. */
  result: GraphResultState | null
  /** The graph or the window changed after that run. */
  stale: boolean
  /** A backtest cook is running. */
  running: boolean
  /** The ticker the chart shows with no result (the sidebar's). */
  ticker: string
}

export interface GraphChartSplitProps {
  /** The canvas (and anything drawn over it). */
  canvas: ReactNode
  bar: ChartBarModel
  /** The Data Sheet drawer, or null for none. */
  sheet: ReactNode
}

export function GraphChartSplit({ canvas, bar, sheet }: GraphChartSplitProps) {
  const panelRef = usePanelRef()
  const open = useGraphSplit(s => s.chartOpen)
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({ id: SPLIT_STORAGE_ID, storage: safeStorage })

  // The panel is the truth for open/closed (a drag can collapse or open it).
  const syncOpen = useCallback(() => {
    const p = panelRef.current
    if (!p) return
    useGraphSplit.getState().setChartOpen(!p.isCollapsed())
  }, [panelRef])

  // Opening for the first time goes to the default open size, not the
  // library's minimum; later opens restore the size the user left.
  const sizedOnce = useRef(false)
  const expand = useCallback(() => {
    const p = panelRef.current
    if (!p) return
    p.expand()
    if (!sizedOnce.current) {
      sizedOnce.current = true
      const px = p.getSize()?.inPixels ?? 0
      if (px <= 200) p.resize(CHART_OPEN_SIZE)
    }
  }, [panelRef])

  useEffect(() => {
    bindChartPanel({
      expand,
      collapse: () => panelRef.current?.collapse(),
      isCollapsed: () => panelRef.current?.isCollapsed() ?? true,
    })
    syncOpen()
    return () => {
      bindChartPanel(null)
      const s = useGraphSplit.getState()
      s.setHost(null)
      s.setChartOpen(false)
    }
  }, [expand, panelRef, syncOpen])

  // A restored layout that was open counts as sized already.
  useEffect(() => {
    if (open) sizedOnce.current = true
  }, [open])

  const toggle = useCallback(() => {
    const p = panelRef.current
    if (!p) return
    if (p.isCollapsed()) expand()
    else p.collapse()
  }, [expand, panelRef])

  const resetChartSize = useCallback(() => {
    const p = panelRef.current
    if (!p) return
    if (p.isCollapsed()) p.expand()
    p.resize(CHART_OPEN_SIZE)
  }, [panelRef])

  // The chart's mount point: published while the panel is open (App portals
  // the one Chart into it), withdrawn when it closes or unmounts.
  const setHost = useCallback((el: HTMLDivElement | null) => {
    useGraphSplit.getState().setHost(el)
  }, [])

  return (
    <div className="nb-split" data-testid="nb-split">
      <Group
        orientation="vertical"
        className="nb-split__group"
        defaultLayout={defaultLayout}
        onLayoutChanged={onLayoutChanged}
      >
        <Panel id="nb-split-canvas" minSize="30%" className="nb-split__canvas">
          <div className="nb-split__fill">{canvas}</div>
        </Panel>
        <Separator
          id="nb-split-handle-chart"
          className="nb-split__handle"
          {...{ onDoubleClick: resetChartSize } as Record<string, unknown>}
        />
        <Panel
          id="nb-chart-panel"
          panelRef={panelRef}
          collapsible
          collapsedSize={`${CHART_BAR_PX}px`}
          defaultSize={`${CHART_BAR_PX}px`}
          minSize="180px"
          onResize={syncOpen}
          className="nb-split__chart"
        >
          <div className="nb-chart-panel">
            <ChartBar model={bar} open={open} onToggle={toggle} />
            {open && (
              <div
                id="nb-chart-body"
                ref={setHost}
                className="nb-chart-body"
                role="region"
                aria-label={`Price chart for ${bar.result ? bar.result.request.ticker : bar.ticker}`}
                data-testid="nb-chart-body"
              />
            )}
          </div>
        </Panel>
      </Group>
      {sheet}
    </div>
  )
}

/** The chart bar (S28): `▸ Chart` and the last backtest's summary. */
export function ChartBar({ model, open, onToggle }: { model: ChartBarModel; open: boolean; onToggle(): void }) {
  const { result, stale, running } = model
  const sum = useMemo(() => (result ? chartBarSummary(result) : null), [result])
  const text = sum ? chartBarText(sum, stale) : NO_RESULT_TEXT
  const label = running ? `running… ${sum ? chartBarText(sum, stale) : ''}`.trim() : text
  return (
    <button
      type="button"
      className="nb-chart-bar"
      data-testid="nb-chart-bar"
      aria-expanded={open}
      aria-controls="nb-chart-panel"
      aria-label={`Chart: ${label}`}
      title={stale ? STALE_TITLE : open ? 'Close the chart (Shift+V)' : 'Open the chart (Shift+V)'}
      onClick={onToggle}
    >
      <span className="nb-chart-bar__toggle">
        {running ? <span className="nb-spinner nb-chart-bar__spinner" aria-hidden="true" /> : <span aria-hidden="true">{open ? '▾' : '▸'}</span>}
        {' '}Chart
      </span>
      <span className="nb-chart-bar__summary" data-testid="nb-chart-bar-summary">
        {running && <span className="nb-chart-bar__running">running…{sum ? ' · ' : ''}</span>}
        {!sum && !running && <span className="nb-chart-bar__dim">{NO_RESULT_TEXT}</span>}
        {sum && (
          <>
            {stale && <span className="nb-chart-bar__stale">stale · </span>}
            {!running && <>{sum.context} · </>}
            {sum.trades} · <span className={`nb-chart-bar__ret nb-chart-bar__ret--${sum.returnSign}`}>{sum.returnText}</span> · {sum.sharpe}
            {sum.open && <> · {sum.open}</>}
            {sum.exitWarning && <> · <span className="nb-chart-bar__warn">{sum.exitWarning}</span></>}
          </>
        )}
      </span>
    </button>
  )
}

// ---- Data Sheet mount (S25) --------------------------------------------------------

/**
 * The newest cook the sheet can read: the later of the preview and the
 * backtest cook, by when each cook id was produced (`cookedAt`). A failed or
 * cancelled preview stamps `endedAt` but leaves the older cook id, so
 * `endedAt` would pick that older cook (DV-5).
 */
export function selectSheetCookId(s: NodeBuilderState): string | null {
  const pv = s.preview.cookId
  const bt = s.cooks.backtest.cookId
  if (pv && bt) return (s.cooks.backtest.cookedAt ?? 0) > (s.cooks.preview.cookedAt ?? 0) ? bt : pv
  return pv ?? bt ?? null
}
/**
 * The stale bar (S25): the graph changed since the sheet's cook. With auto
 * cook on, a new preview is already on its way, so the bar shows only when
 * that cannot happen: the preview failed or errors block it (UX-02, DV-15).
 */
export function selectSheetStale(s: NodeBuilderState): boolean {
  const stale = s.preview.stale || s.cooks.preview.stale
  if (!stale) return false
  if (!s.autoCook) return true
  return s.cooks.preview.phase === 'failed' || s.cooks.preview.staleNote != null
}
const selectAutoCook = (s: NodeBuilderState) => s.autoCook

export interface GraphSheetProps {
  window: GraphWindow | null
  result: GraphResultState | null
  /** The graph on screen when it is not the store's (the read-only view). */
  readOnlyGraph?: Graph | null
  onRunBacktest(): void
  /** "Edit this graph" for the read-only view's no-cook state (UX-08). */
  onEditGraph?(): void
}

/** The Data Sheet with its live inputs (cook id, staleness, trades). */
export function GraphSheet({ window, result, readOnlyGraph, onRunBacktest, onEditGraph }: GraphSheetProps) {
  const cookId = useNodeBuilderStore(selectSheetCookId)
  const stale = useNodeBuilderStore(selectSheetStale)
  const autoCook = useNodeBuilderStore(selectAutoCook)
  const tradeTimes = useMemo(() => (result ? entryTimesFromTrades(graphTrades(result)) : null), [result])
  return (
    <DataSheet
      cookId={cookId}
      window={window}
      tradeTimes={tradeTimes}
      stale={stale}
      autoCook={autoCook}
      // The stale bar's `Cook (⌘↵)`: the backtest cook, which also refreshes
      // the node data through its cook id (useAutoCook has no "cook now").
      onCook={onRunBacktest}
      onRunBacktest={onRunBacktest}
      onAutoCookOn={() => useNodeBuilderStore.getState().setAutoCook(true)}
      onEditGraph={onEditGraph}
      graph={readOnlyGraph}
    />
  )
}

/** Toolbar toggle for the Data Sheet (`▤`, key S). */
export function SheetToggle() {
  const open = useSheetUi(s => s.open)
  return (
    <Button
      kind="icon"
      pressed={open}
      aria-label="Data sheet"
      title="Data sheet (S)"
      data-testid="nb-btn-sheet"
      onClick={() => toggleSheet()}
    >
      ▤
    </Button>
  )
}

export default GraphChartSplit
