/**
 * NodeBuilder — top-level feature component for the graph viewer + editor.
 *
 * Modes:
 * 1. View mode (default): renders the TanStack Query auto-render result of
 *    the chart's rule strategy, read-only. "Edit this graph" copies it into
 *    the editor.
 * 2. Edit mode: the Zustand store has a graph (graph.readOnly=false); Canvas uses it.
 *
 * Graphs are named server objects (W1). The graph toolbar (GraphToolbar,
 * S01) names the graph and holds New, Open, Save, Save as, Rename,
 * Duplicate, Delete, Export and Import; useGraphSession does the work
 * (dialogs, Graph Browser, drafts, the 409 conflict flow). Notices show in
 * one stack under the toolbar (NoticeStack, S07).
 *
 * Diagnostics (1.G) are validated 300 ms after each commit; the toolbar
 * shows the count and Run is disabled while there are errors.
 *
 * The results strip names the symbol and interval it ran on, dims itself as
 * "stale" once the graph changes, and flags an open position or an
 * unconnected Exit. Text logic lives in resultsStrip.ts.
 */

import { Fragment, memo, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { StrategyRequest } from '../../shared/types/strategy'
import { fetchAutoRender, fetchGraphBacktest, type GraphBacktestResult, type GraphNode } from '../../api/nodebuilder'
import { errorDiagnostics } from '../../api/graphs'
import type { Diagnostic } from '../../api/nodebuilderValidate'
import { apiErrorDetail } from '../../shared/utils/errors'
import Canvas from './Canvas'
import { NODE_CATALOG } from './catalog'
import { newNodeId } from './operations'
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
import {
  findUnsupportedNodes,
  REGIME_LEARN_MORE_TEXT,
  REGIME_REMOVED_TEXT,
  UNSUPPORTED_PREFIX,
  UNSUPPORTED_SUFFIX,
  unsupportedLabel,
} from './editNotices'
import { registerCommands } from './commands'
import { useGlobalKeys } from './commands/useGlobalKeys'
import {
  getDiagnosticsView,
  retryValidation,
  setServerDiagnostics,
  useDiagnostics,
  useDiagnosticsController,
} from './useDiagnostics'
import { DiagnosticsPopover } from './DiagnosticsPopover'
import GraphToolbar, { type ToolbarMode } from './GraphToolbar'
import { runDisabledReason, VALIDATE_OFFLINE_KEY, validateOfflineText } from './graphText'
import NoticeStack from './NoticeStack'
import { pushNotice, resolveNotice, type Notice } from './notices'
import { runLegacySeed, seedBannerText } from './persistence'
import { useGraphSession } from './useGraphSession'
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

/** A failed run: the sentence to show and the node at fault, if known. */
interface RunError {
  text: string
  nodeId: string | null
}

// The legacy seed runs once per page load, however often NodeBuilder mounts.
let seedStarted = false

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
  const graphMeta = useNodeBuilderStore(s => s.graphMeta)
  const layoutEpoch = useNodeBuilderStore(s => s.layoutEpoch)
  const regimeRemoved = useNodeBuilderStore(s => s.regimeRemoved)
  const loadFromAutoRender = useNodeBuilderStore(s => s.loadFromAutoRender)
  const selectNode = useNodeBuilderStore(s => s.select)

  // Diagnostics: one controller for the whole builder; the counts drive
  // the toolbar chip and the Run button.
  useDiagnosticsController()
  const diagnostics = useDiagnostics()
  const [diagAnchor, setDiagAnchor] = useState<HTMLElement | null>(null)

  // S05 "validate offline": one S07 error banner per failure streak, with
  // Retry. It goes away once a validate succeeds (the streak ends); a
  // dismissed banner stays dismissed until the next streak.
  const validateOffline = diagnostics.offline
  const validateOfflineDetail = diagnostics.offlineDetail
  const offlineBannerShown = useRef(false)
  useEffect(() => {
    if (!validateOffline) {
      if (offlineBannerShown.current) resolveNotice(VALIDATE_OFFLINE_KEY)
      offlineBannerShown.current = false
      return
    }
    if (offlineBannerShown.current) return
    offlineBannerShown.current = true
    pushNotice({
      key: VALIDATE_OFFLINE_KEY,
      severity: 'error',
      text: validateOfflineText(validateOfflineDetail),
      actions: [{ label: 'Retry', run: retryValidation, testId: 'nb-validate-retry' }],
    })
  }, [validateOffline, validateOfflineDetail])

  const session = useGraphSession()

  // Global commands (Cmd+S, Cmd+O, Cmd+Enter, Cmd+Z) work in every mode,
  // from the canvas and from a param field (foundation 6.2).
  const rootRef = useRef<HTMLDivElement>(null)
  useGlobalKeys(rootRef)

  // Showing the graph view hands it the keyboard, so Cmd+O works straight
  // away (focus was on the toggle that opened the view). Focus already inside
  // the builder (the canvas) is left alone.
  useEffect(() => {
    if (!graphViewActive) return
    const root = rootRef.current
    if (root && !root.contains(document.activeElement)) root.focus({ preventScroll: true })
  }, [graphViewActive])

  // Edit mode = store has a graph (readOnly=false)
  const editMode = storeGraph !== null && !storeGraph.readOnly

  // Which graph to pass to Canvas
  const activeGraph = editMode ? storeGraph : (autoGraph ?? null)

  // Graph backtest state
  const [backtestRunning, setBacktestRunning] = useState(false)
  const [lastRun, setLastRun] = useState<GraphRun | null>(null)
  const [runError, setRunError] = useState<RunError | null>(null)
  // Bumped by every run, every clear and Stop, so a run still in flight
  // after a graph change (or a newer run) drops its late result.
  const runIdRef = useRef(0)
  // The request of the run in flight; Stop and a clear abort it (S01), so the
  // server stops working on a result nobody will see.
  const runAbortRef = useRef<AbortController | null>(null)
  function abortRun() {
    runAbortRef.current?.abort()
    runAbortRef.current = null
  }

  const hasNodes = storeGraph != null && Object.keys(storeGraph.nodes).length > 0
  const errorCount = diagnostics.errorCount

  // Nodes the compiler cannot run yet. Recomputed on every edit, so the
  // banner goes away once the user removes them.
  const unsupported = useMemo(
    () => (editMode ? findUnsupportedNodes(storeGraph) : []),
    [editMode, storeGraph],
  )
  // The regime banner's "Learn more" shows one more sentence.
  const [regimeHelp, setRegimeHelp] = useState(false)

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
    abortRun()
    setBacktestRunning(false)
    setLastRun(null)
    setRunError(null)
  }

  // A different graph on screen (open, new, edit copy) drops the last run
  // and any run still in flight. A save or Save as keeps it.
  const firstEpoch = useRef(true)
  useEffect(() => {
    if (firstEpoch.current) {
      firstEpoch.current = false
      return
    }
    clearRun()
  }, [layoutEpoch])

  // One-time import of the old browser-only saved graphs (S01).
  useEffect(() => {
    if (seedStarted) return
    seedStarted = true
    void runLegacySeed().then(outcome => {
      if (!outcome) return
      if (outcome.tooLarge) {
        pushNotice({ key: 'seed_imported', severity: 'warn', text: seedBannerText(outcome) })
        return
      }
      if (outcome.imported + outcome.duplicates + outcome.unreadable === 0) return
      pushNotice({
        key: 'seed_imported',
        severity: 'info',
        text: seedBannerText(outcome),
        actions: [{ label: 'Open…', run: session.openBrowser }],
      })
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Reopen the last graph the first time the graph view is shown.
  const openLastGraph = session.openLastGraph
  useEffect(() => {
    if (graphViewActive) openLastGraph()
  }, [graphViewActive, openLastGraph])

  // The empty graph's ghost Ticker card (foundation 7): add a Ticker.
  function addTicker() {
    const entry = NODE_CATALOG.find(e => e.name === 'ticker')
    if (!entry) return
    useNodeBuilderStore.getState().addNode({
      id: newNodeId(), type: 'ticker', name: 'ticker', parent: null,
      params: { ...entry.defaults.params } as GraphNode['params'],
      position: [0, 0], display: false, bypass: false,
    })
  }

  function handleEditThisGraph() {
    if (!autoGraph) return
    loadFromAutoRender(autoGraph)
  }

  function handleStop() {
    runIdRef.current += 1
    abortRun()
    setBacktestRunning(false)
  }

  async function handleRunBacktest() {
    // Read the stores now, not this render's copies: Cmd+Enter in a param
    // field commits the field and runs in the same key press, before React
    // renders again.
    const { graph: runGraph, commitSeq: seqAtStart } = useNodeBuilderStore.getState()
    if (runGraph == null || runGraph.readOnly || backtestRunning) return
    const runHasNodes = Object.keys(runGraph.nodes).length > 0
    if (runDisabledReason(getDiagnosticsView().errorCount, runHasNodes) != null) return
    const runId = ++runIdRef.current
    abortRun()
    const ctrl = new AbortController()
    runAbortRef.current = ctrl
    const graphKeyAtStart = graphEvalKey(runGraph)
    const requestKeyAtStart = requestKey
    setBacktestRunning(true)
    setRunError(null)
    setLastRun(null)
    try {
      // Derive ticker/interval from the graph's ticker node, falling back to the
      // loaded request when available. The data source is the sidebar's only
      // (plan D11): a Ticker has no source param since W2, and a stale one left
      // on an older graph must not override the sidebar.
      const tickerNode = Object.values(runGraph.nodes).find(n => n.type === 'ticker')
      const ticker = (tickerNode?.params?.symbol as string | undefined) ?? request?.ticker ?? 'AAPL'
      const interval = (tickerNode?.params?.interval as string | undefined) ?? request?.interval ?? '1d'
      const source = request?.source ?? 'yahoo'
      const start = request?.start ?? '2022-01-01'
      const end = request?.end ?? '2024-01-01'

      const result = await fetchGraphBacktest({
        graph: runGraph,
        ticker,
        interval,
        source,
        start,
        end,
        initial_capital: request?.initial_capital ?? 10000,
        position_size: request?.position_size ?? 1.0,
        slippage_bps: request?.slippage_bps ?? 2.0,
        direction: request?.direction ?? 'long',
      }, ctrl.signal)
      if (runId !== runIdRef.current) return  // superseded, cleared or stopped
      setLastRun({ result, ticker, interval, graphKey: graphKeyAtStart, requestKey: requestKeyAtStart })
    } catch (e: unknown) {
      if (runId !== runIdRef.current) return
      // A 400 with diagnostics replaces the current set until the next commit
      // (S05), but only while the graph on screen is the one that ran: after
      // an edit, the /validate scheduled for the new graph owns the list.
      const diags = errorDiagnostics(e)
      if (diags && diags.length > 0 && useNodeBuilderStore.getState().commitSeq === seqAtStart) {
        setServerDiagnostics(diags as Diagnostic[])
      }
      const badNode = errorNodeId(e)
      setRunError({ text: describeBacktestError(e, runGraph), nodeId: badNode })
      // Select the node the server names, so its ring shows which one to fix
      // (select() ignores a node deleted meanwhile).
      if (badNode) selectNode(badNode)
    } finally {
      if (runAbortRef.current === ctrl) runAbortRef.current = null
      if (runId === runIdRef.current) setBacktestRunning(false)
    }
  }

  // Latest handlers for the keyboard commands, registered once.
  const keyHandlers = useRef({ save: session.save, open: session.openBrowser, run: handleRunBacktest })
  useEffect(() => {
    keyHandlers.current = { save: session.save, open: session.openBrowser, run: handleRunBacktest }
  })
  useEffect(
    () =>
      registerCommands([
        {
          id: 'graph.save',
          label: 'Save',
          keys: ['mod+s'],
          scope: 'global',
          inFields: true,
          run: () => keyHandlers.current.save(),
        },
        {
          id: 'graph.open',
          label: 'Open…',
          keys: ['mod+o'],
          scope: 'global',
          inFields: true,
          run: () => keyHandlers.current.open(),
        },
        {
          id: 'cook.run',
          label: 'Run backtest',
          keys: ['mod+enter'],
          scope: 'global',
          inFields: true,
          run: () => void keyHandlers.current.run(),
        },
      ]),
    [],
  )

  // Banners that follow state rather than events.
  const derivedNotices: Notice[] = []
  if (runError && editMode) {
    const node = runError.nodeId ? storeGraph?.nodes[runError.nodeId] : undefined
    derivedNotices.push({
      // Its own key: a pushed save/import/delete `server_error` must not hide
      // the run error (NoticeStack drops a derived notice whose key is pushed).
      key: 'run_error',
      severity: 'error',
      text: (
        <>
          {runError.text}
          {node && (
            <>
              {' · '}
              <button type="button" className="nb-banner__link" onClick={() => selectNode(node.id)}>
                {node.name || node.id}
              </button>
            </>
          )}
        </>
      ),
      onDismiss: () => setRunError(null),
      actions: [{ label: 'Retry', run: () => handleRunBacktest() }],
    })
  }
  if (editMode && regimeRemoved.length > 0) {
    derivedNotices.push({
      key: 'regime_removed',
      severity: 'warn',
      text: regimeHelp ? `${REGIME_REMOVED_TEXT} ${REGIME_LEARN_MORE_TEXT}` : REGIME_REMOVED_TEXT,
      actions: [
        { label: 'Learn more', title: REGIME_LEARN_MORE_TEXT, run: () => setRegimeHelp(v => !v), testId: 'nb-regime-learn-more' },
      ],
    })
  }
  if (unsupported.length > 0) {
    derivedNotices.push({
      key: 'unsupported_nodes',
      severity: 'error',
      text: (
        <>
          {UNSUPPORTED_PREFIX}
          {unsupported.map((u, i) => (
            <Fragment key={u.id}>
              {i > 0 && ', '}
              <button
                type="button"
                className="nb-banner__link"
                title={`Select ${storeGraph?.nodes[u.id]?.name || u.id}`}
                onClick={() => selectNode(u.id)}
              >
                {unsupportedLabel(u)}
              </button>
            </Fragment>
          ))}
          {UNSUPPORTED_SUFFIX}
        </>
      ),
    })
  }

  const mode: ToolbarMode = editMode ? 'edit' : request != null && autoGraph ? 'view' : 'none'
  const toolbar = (
    <GraphToolbar
      mode={mode}
      name={graphMeta?.name ?? null}
      rev={graphMeta?.id ? graphMeta.rev : null}
      dirty={dirty}
      loading={session.busy === 'loading'}
      saving={session.busy === 'saving'}
      running={backtestRunning}
      hasNodes={hasNodes}
      errorCount={errorCount}
      warningCount={diagnostics.warningCount}
      diagnosticsUnknown={diagnostics.pending && !diagnostics.hasResult}
      onRun={() => void handleRunBacktest()}
      onStop={handleStop}
      onSave={session.save}
      onNew={session.newGraph}
      onOpen={session.openBrowser}
      onSaveAs={session.saveAs}
      onRename={session.rename}
      onRenameInline={session.renameInline}
      onDuplicate={session.duplicate}
      onExport={session.exportJson}
      onImport={session.importJson}
      onDelete={session.deleteCurrent}
      onEditThisGraph={handleEditThisGraph}
      onCloseGraph={session.closeGraph}
      onDiagnosticsClick={el => setDiagAnchor(a => (a ? null : el))}
    />
  )

  const shared = (
    <>
      {session.element}
      {editMode && (
        <DiagnosticsPopover
          open={diagAnchor != null}
          anchorEl={diagAnchor}
          onClose={() => setDiagAnchor(null)}
          onSelectNode={id => selectNode(id)}
        />
      )}
    </>
  )

  if (request == null && !editMode) {
    return (
      <div ref={rootRef} className="nodebuilder-root" style={styles.root} tabIndex={-1} data-nb-builder="">
        {toolbar}
        <NoticeStack />
        <div style={styles.empty}>
          <span>
            No strategy to show yet. Go Back to Chart and run a backtest, then
            come back here to see that strategy as a graph. Or start a New graph
            from ⋯, or use{' '}
            <button type="button" className="nb-empty-graph__link" onClick={session.openBrowser}>⋯ › Open</button>
            {' '}to load a saved graph.
          </span>
        </div>
        {shared}
      </div>
    )
  }

  return (
    <div ref={rootRef} className="nodebuilder-root" style={styles.root} tabIndex={-1} data-nb-builder="">
      {toolbar}
      <NoticeStack extra={derivedNotices} />

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
          {editMode && !hasNodes && (
            <div className="nb-empty-graph" data-testid="nb-empty-graph">
              <div>Press Tab to add a node</div>
              <div>Start with a Ticker, then indicators, comparisons, and an Output Group</div>
              <div>
                or{' '}
                <button type="button" className="nb-empty-graph__link" onClick={session.openBrowser} data-testid="nb-empty-open">
                  ⋯ › Open
                </button>{' '}
                to load a saved graph
              </div>
              <button type="button" className="nb-empty-graph__ghost" onClick={addTicker} aria-label="Add Ticker" data-testid="nb-empty-ticker">
                Ticker
              </button>
            </div>
          )}
        </div>
      )}
      {shared}
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
    outline: 'none',
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
  backtestDivider: {
    color: 'oklch(0.45 0.010 250)',
  },
}
