/**
 * NodeBuilder — top-level feature component for the graph viewer + editor.
 *
 * Unit 4a: read-only graph viewer (auto-render via TanStack Query).
 * Unit 5: editable graph mode via Zustand store.
 * Unit 8b: "▶ Run Backtest" button in edit mode.
 *
 * Modes:
 * 1. View mode (default): renders the TanStack Query auto-render result read-only.
 * 2. Edit mode: the Zustand store has a graph (graph.readOnly=false); Canvas uses it.
 *
 * The "New Empty Graph" button creates a blank editable graph in the store and
 * switches to edit mode. It asks first when there are unsaved edits.
 * "Discard edits" throws the edit copy away and goes back to the view mode.
 *
 * The results strip names the symbol and interval it ran on, dims itself as
 * "stale" once the graph changes, and flags an open position or an
 * unconnected Exit. Text logic lives in resultsStrip.ts.
 */

import { memo, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { StrategyRequest } from '../../shared/types/strategy'
import { fetchAutoRender, fetchGraphBacktest, type GraphBacktestResult } from '../../api/nodebuilder'
import { apiErrorDetail } from '../../shared/utils/errors'
import Canvas from './Canvas'
import { hasEdits, useNodeBuilderStore } from './store'
import {
  buildResultsStrip,
  describeBacktestError,
  errorNodeId,
  EXIT_NOT_CONNECTED_TITLE,
  graphEvalKey,
  STALE_LABEL,
  STALE_REQUEST_TITLE,
  STALE_TITLE,
} from './resultsStrip'
import { describeUnsupportedNodes, findUnsupportedNodes, REGIME_REMOVED_TEXT } from './editNotices'
import './tokens.css'

interface NodeBuilderProps {
  request: StrategyRequest | null
  graphViewActive: boolean
}

/** One finished run: its result, what it ran on, and the state it ran against. */
interface GraphRun {
  result: GraphBacktestResult
  ticker: string
  interval: string
  /** What the graph computed and which request it ran with; a mismatch later means stale. */
  graphKey: string
  requestKey: string
}

function NodeBuilder({ request, graphViewActive }: NodeBuilderProps) {
  // Stable cache key: JSON.stringify is deterministic within a session.
  const strategyHash = request != null ? JSON.stringify(request) : null

  const { data: autoGraph, isLoading, error } = useQuery({
    queryKey: ['nodebuilder', 'auto_render', strategyHash],
    queryFn: () => fetchAutoRender(request!),
    enabled: request != null && graphViewActive,
    staleTime: Infinity,
    gcTime: Infinity,
  })

  // Store state
  const storeGraph = useNodeBuilderStore(s => s.graph)
  const dirty = useNodeBuilderStore(hasEdits)
  const regimeRemoved = useNodeBuilderStore(s => s.regimeRemoved)
  const newEmptyGraph = useNodeBuilderStore(s => s.newEmptyGraph)
  const loadFromAutoRender = useNodeBuilderStore(s => s.loadFromAutoRender)
  const discardEdits = useNodeBuilderStore(s => s.discardEdits)
  const selectNode = useNodeBuilderStore(s => s.select)

  // Edit mode = store has a graph (readOnly=false)
  const editMode = storeGraph !== null && !storeGraph.readOnly

  // Which graph to pass to Canvas
  const activeGraph = editMode ? storeGraph : (autoGraph ?? null)

  // Unit 8b: graph backtest state
  const [backtestRunning, setBacktestRunning] = useState(false)
  const [lastRun, setLastRun] = useState<GraphRun | null>(null)
  const [backtestError, setBacktestError] = useState<string | null>(null)
  // Bumped by every run and every clear, so a run still in flight after
  // Discard / New Empty Graph (or a newer run) drops its late result.
  const runIdRef = useRef(0)

  const hasNodes = storeGraph != null && Object.keys(storeGraph.nodes).length > 0

  // Nodes the compiler cannot run yet. Recomputed on every edit, so the
  // banner goes away once the user removes them.
  const unsupportedText = useMemo(
    () => (editMode ? describeUnsupportedNodes(findUnsupportedNodes(storeGraph)) : null),
    [editMode, storeGraph],
  )

  // The run also depends on the request (dates, capital), so a new chart
  // backtest makes the strip stale too. Moving a node does not: the graph
  // key leaves out positions.
  const graphKey = useMemo(() => graphEvalKey(storeGraph), [storeGraph])
  const requestKey = strategyHash ?? ''
  const graphStale = lastRun != null && lastRun.graphKey !== graphKey
  const requestStale = lastRun != null && lastRun.requestKey !== requestKey
  const strip = useMemo(
    () =>
      lastRun
        ? buildResultsStrip(lastRun.result.summary, lastRun, graphStale || requestStale)
        : null,
    [lastRun, graphStale, requestStale],
  )
  const staleTitle = graphStale ? STALE_TITLE : STALE_REQUEST_TITLE

  function clearRun() {
    runIdRef.current += 1
    setBacktestRunning(false)
    setLastRun(null)
    setBacktestError(null)
  }

  function confirmDropEdits(): boolean {
    if (!dirty) return true
    return window.confirm('You have unsaved edits to this graph. Throw them away?')
  }

  function handleNewEmptyGraph() {
    if (!confirmDropEdits()) return
    clearRun()
    newEmptyGraph()
  }

  function handleDiscardEdits() {
    if (!confirmDropEdits()) return
    clearRun()
    discardEdits()
  }

  function handleEditThisGraph() {
    if (!autoGraph) return
    clearRun()
    loadFromAutoRender(autoGraph)
  }

  async function handleRunBacktest() {
    if (storeGraph == null || !hasNodes) return
    const runId = ++runIdRef.current
    const graphKeyAtStart = graphKey
    const requestKeyAtStart = requestKey
    setBacktestRunning(true)
    setBacktestError(null)
    setLastRun(null)
    try {
      // Derive ticker/interval/source from the graph's ticker node, falling back to the
      // loaded request when available.
      const tickerNode = Object.values(storeGraph.nodes).find(n => n.type === 'ticker')
      const ticker = (tickerNode?.params?.symbol as string | undefined) ?? request?.ticker ?? 'AAPL'
      const interval = (tickerNode?.params?.interval as string | undefined) ?? request?.interval ?? '1d'
      const source = (tickerNode?.params?.source as string | undefined) ?? request?.source ?? 'yahoo'
      const start = request?.start ?? '2022-01-01'
      const end = request?.end ?? '2024-01-01'

      const result = await fetchGraphBacktest({
        graph: storeGraph,
        ticker,
        interval,
        source,
        start,
        end,
        initial_capital: request?.initial_capital ?? 10000,
        position_size: request?.position_size ?? 1.0,
        slippage_bps: request?.slippage_bps ?? 2.0,
        direction: request?.direction ?? 'long',
      })
      if (runId !== runIdRef.current) return  // superseded or cleared
      setLastRun({ result, ticker, interval, graphKey: graphKeyAtStart, requestKey: requestKeyAtStart })
    } catch (e: unknown) {
      if (runId !== runIdRef.current) return
      setBacktestError(describeBacktestError(e, storeGraph))
      // Select the node the server names, so its ring shows which one to fix.
      const badNode = errorNodeId(e)
      if (badNode && storeGraph.nodes[badNode]) selectNode(badNode)
    } finally {
      if (runId === runIdRef.current) setBacktestRunning(false)
    }
  }

  if (request == null && !editMode) {
    return (
      <div className="nodebuilder-root" style={styles.root}>
        <div style={styles.toolbar}>
          <button style={styles.btn} onClick={handleNewEmptyGraph}>
            New Empty Graph
          </button>
        </div>
        <div style={styles.empty}>
          No strategy to show yet. Go Back to Chart and run a backtest, then
          come back here to see that strategy as a graph. Or start from a New
          Empty Graph.
        </div>
      </div>
    )
  }

  return (
    <div className="nodebuilder-root" style={styles.root}>
      {/* Toolbar */}
      <div style={styles.toolbar}>
        {editMode ? (
          <span style={styles.editBadge}>Editing</span>
        ) : null}
        <button style={styles.btn} onClick={handleNewEmptyGraph}>
          New Empty Graph
        </button>
        {editMode && (
          <button
            style={styles.btn}
            onClick={handleDiscardEdits}
            title="Throw away this edited copy and go back to the read-only graph"
          >
            Discard edits
          </button>
        )}
        {!editMode && autoGraph && (
          <button
            style={{
              ...styles.btn,
              background: 'oklch(0.24 0.08 230 / 0.5)',
              border: '1px solid oklch(0.45 0.10 230 / 0.6)',
              color: 'oklch(0.85 0.14 230)',
            }}
            onClick={handleEditThisGraph}
            title="Copy the auto-rendered graph into the editor so you can modify it"
          >
            Edit this graph
          </button>
        )}
        {editMode && hasNodes && (
          <button
            style={{
              ...styles.btn,
              background: backtestRunning
                ? 'oklch(0.22 0.018 250)'
                : 'oklch(0.24 0.10 145 / 0.5)',
              border: '1px solid oklch(0.45 0.12 145 / 0.6)',
              color: 'oklch(0.85 0.18 145)',
              opacity: backtestRunning ? 0.6 : 1,
              cursor: backtestRunning ? 'wait' : 'pointer',
            }}
            onClick={handleRunBacktest}
            disabled={backtestRunning}
          >
            {backtestRunning ? 'Running…' : '▶ Run Backtest'}
          </button>
        )}
      </div>

      {/* Content */}
      {isLoading && !editMode && (
        <div style={styles.loadingWrapper}>
          <div className="chart-skeleton" style={styles.skeleton} />
        </div>
      )}
      {error && !editMode && (
        <div style={styles.errorBanner}>
          Failed to render graph: {apiErrorDetail(error, (error as Error).message)}
        </div>
      )}
      {backtestError && editMode && (
        <div style={styles.errorBanner}>
          Backtest error: {backtestError}
        </div>
      )}
      {editMode && regimeRemoved.length > 0 && (
        <div style={styles.warnBanner}>{REGIME_REMOVED_TEXT}</div>
      )}
      {unsupportedText && (
        <div style={styles.warnBanner}>{unsupportedText}</div>
      )}
      {editMode && strip && (
        <div
          style={{ ...styles.backtestHeadline, opacity: strip.stale ? 0.5 : 1 }}
          title={strip.stale ? staleTitle : undefined}
        >
          <span style={styles.backtestContext}>{strip.context}</span>
          <span style={styles.backtestDivider}>·</span>
          <span style={styles.backtestStat}>{strip.trades}</span>
          <span style={styles.backtestDivider}>·</span>
          <span
            style={{
              ...styles.backtestStat,
              color:
                strip.returnSign === 'pos'
                  ? 'oklch(0.72 0.18 145)'
                  : strip.returnSign === 'neg'
                    ? 'oklch(0.65 0.20 25)'
                    : styles.backtestStat.color,
            }}
          >
            {strip.returnText}
          </span>
          <span style={styles.backtestDivider}>·</span>
          <span style={styles.backtestStat}>{strip.sharpe}</span>
          {strip.openPosition && (
            <>
              <span style={styles.backtestDivider}>·</span>
              <span style={styles.backtestStat} title={strip.openPositionTitle ?? undefined}>
                {strip.openPosition}
              </span>
            </>
          )}
          {strip.exitWarning && (
            <span style={styles.backtestWarn} title={EXIT_NOT_CONNECTED_TITLE}>{strip.exitWarning}</span>
          )}
          {strip.stale && <span style={styles.staleBadge}>{STALE_LABEL}</span>}
        </div>
      )}
      {activeGraph && (
        <div style={styles.canvasWrapper}>
          <Canvas graph={activeGraph} />
        </div>
      )}
    </div>
  )
}

// Memoized so App re-renders don't re-render the hidden graph tree (perf P6).
// Props are App state (lastRequest) and a boolean, so they are stable.
export default memo(NodeBuilder)

const styles: Record<string, React.CSSProperties> = {
  root: {
    width: '100%',
    height: '100%',
    background: 'var(--bg-main)',
    display: 'flex',
    flexDirection: 'column',
    position: 'relative',
  },
  toolbar: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '6px 10px',
    borderBottom: '1px solid oklch(0.28 0.014 250)',
    flexShrink: 0,
    background: 'oklch(0.18 0.014 250)',
  },
  editBadge: {
    fontSize: 11,
    fontWeight: 600,
    color: 'oklch(0.72 0.18 145)',
    background: 'oklch(0.20 0.04 145 / 0.3)',
    border: '1px solid oklch(0.45 0.12 145 / 0.5)',
    borderRadius: 4,
    padding: '2px 7px',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    marginRight: 4,
  },
  btn: {
    fontSize: 12,
    padding: '4px 10px',
    borderRadius: 4,
    border: '1px solid oklch(0.40 0.018 250)',
    background: 'oklch(0.24 0.018 250)',
    color: 'oklch(0.85 0.010 250)',
    cursor: 'pointer',
  },
  canvasWrapper: {
    flex: 1,
    minHeight: 0,
    position: 'relative',
  },
  empty: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#8b949e',
    fontSize: 13,
    textAlign: 'center',
    padding: 24,
  },
  loadingWrapper: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  skeleton: {
    width: '90%',
    height: '80%',
    borderRadius: 6,
  },
  errorBanner: {
    background: 'rgba(248,81,73,0.12)',
    border: '1px solid rgba(248,81,73,0.4)',
    color: '#f85149',
    fontSize: 12,
    padding: '6px 12px',
    borderRadius: 4,
    margin: '8px 8px 0',
    flexShrink: 0,
  },
  backtestHeadline: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '5px 12px',
    background: 'oklch(0.17 0.012 250)',
    borderBottom: '1px solid oklch(0.28 0.014 250)',
    flexShrink: 0,
    fontSize: 12,
    fontVariantNumeric: 'tabular-nums',
  },
  backtestStat: {
    color: 'oklch(0.82 0.010 250)',
  },
  backtestContext: {
    color: 'oklch(0.82 0.010 250)',
    fontWeight: 600,
  },
  backtestWarn: {
    marginLeft: 6,
    color: 'oklch(0.80 0.14 75)',
    fontWeight: 600,
  },
  staleBadge: {
    marginLeft: 'auto',
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    color: 'oklch(0.80 0.010 250)',
    border: '1px solid oklch(0.45 0.010 250)',
    borderRadius: 4,
    padding: '1px 6px',
  },
  warnBanner: {
    background: 'oklch(0.30 0.08 75 / 0.18)',
    border: '1px solid oklch(0.60 0.12 75 / 0.5)',
    color: 'oklch(0.85 0.12 75)',
    fontSize: 12,
    padding: '6px 12px',
    borderRadius: 4,
    margin: '8px 8px 0',
    flexShrink: 0,
  },
  backtestDivider: {
    color: 'oklch(0.45 0.010 250)',
  },
}
