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
 * Results (W4, D10): a run goes through App's run handler (`onRunGraph`),
 * which builds the request from the sidebar window and leaves out the
 * fields the graph owns (graphRun.ts, D11). The result lives in App's
 * `graphResult`, never in `lastRequest`. Below the canvas, GraphChartSplit
 * (S28) holds the chart panel, whose bar shows the last run's summary
 * ("stale" once the graph or the window changes), and the Data Sheet (S25).
 * Auto cook (useAutoCook, S27) refreshes node data after each edit.
 *
 * Layout slots (W3 pre-step 3.0): the frame below has named places that
 * other items fill through slots.ts without editing this file: toolbarLeft
 * and toolbarRight inside GraphToolbar's clusters, rightPanel right of the canvas
 * column (the Inspector), bottomPanel under the canvas, statusBar along the
 * bottom, then overlays and dialogs. Slot components reach the session and
 * the Run and Stop actions through BuilderContext (useBuilder). The cook
 * state is mirrored into the store's status slice for the status bar.
 */

import { Fragment, memo, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { StrategyRequest } from '../../shared/types/strategy'
import { fetchAutoRender, type GraphNode } from '../../api/nodebuilder'
import { errorDiagnostics } from '../../api/graphs'
import type { Diagnostic } from '../../api/nodebuilderValidate'
import { apiErrorDetail } from '../../shared/utils/errors'
import Canvas from './Canvas'
import { NODE_CATALOG } from './catalog'
import { newNodeId } from './operations'
import { hasEdits, useNodeBuilderStore } from './store'
import { focusNode } from './viewOps'
import { IDLE_COOK } from './store/status'
import { describeBacktestError, errorNodeId, graphEvalKey } from './resultsStrip'
import { SidebarWindowContext } from './sidebarWindow'
import {
  requestSettingsKey,
  runGraphBacktest,
  useGraphRunSettingsKey,
  windowKey,
  windowOfRequest,
  type GraphResultState,
  type GraphRunArgs,
  type GraphRunHandler,
  type GraphWindow,
} from './graphRun'
import { GraphChartSplit, GraphSheet, type ChartBarModel } from './GraphChartSplit'
import { useAutoCook } from './useAutoCook'
import {
  findUnsupportedNodes,
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
import { graphHasGroups } from './ownership'
import { BuilderContext, Slot, type BuilderApi } from './slots'
import { isTypingTarget } from './canvasHelpers'
import './tokens.css'

interface NodeBuilderProps {
  request: StrategyRequest | null
  graphViewActive: boolean
  /**
   * The sidebar window (D11): ticker, dates, interval and source. Runs,
   * auto cook and the Data Sheet use it. Null: fall back to `request`.
   */
  graphWindow?: GraphWindow | null
  /** App's run handler (D10): builds the request and runs it. */
  onRunGraph?: GraphRunHandler
  /** App's graph result (D10). With `onGraphResult` set, App keeps the result. */
  graphResult?: GraphResultState | null
  onGraphResult?: (result: GraphResultState | null) => void
}

/** A failed run: the sentence to show and the node at fault, if known. */
interface RunError {
  text: string
  nodeId: string | null
}

// The legacy seed runs once per page load, however often NodeBuilder mounts.
let seedStarted = false

function NodeBuilder({ request, graphViewActive, graphWindow = null, onRunGraph, graphResult, onGraphResult }: NodeBuilderProps) {
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

  // Graph backtest state. The result is App's when App passes
  // `onGraphResult` (D10); on its own (tests) the builder keeps it here.
  const [backtestRunning, setBacktestRunning] = useState(false)
  const [localResult, setLocalResult] = useState<GraphResultState | null>(null)
  const result = onGraphResult ? (graphResult ?? null) : localResult
  function publishResult(next: GraphResultState | null) {
    if (onGraphResult) onGraphResult(next)
    else setLocalResult(next)
  }
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

  // The window runs and cooks use: the sidebar's when App passes it (D11).
  // On its own (tests), the builder falls back to the loaded request.
  const fallbackWindow = useMemo<GraphWindow>(() => ({
    ticker: request?.ticker ?? 'AAPL',
    start: request?.start ?? '2022-01-01',
    end: request?.end ?? '2024-01-01',
    interval: request?.interval ?? '1d',
    source: request?.source ?? 'yahoo',
  }), [request])
  const runWindow = graphWindow ?? fallbackWindow

  // Auto cook (S27): a debounced /preview after each edit, on the sidebar
  // window. It never runs the backtest; it stops while the view is hidden.
  useAutoCook({ window: graphWindow, active: graphViewActive })

  // A result goes stale when the graph changes what it computes, or when the
  // sidebar window changes. Moving a node does not: the graph key leaves out
  // positions.
  const graphKey = useMemo(() => graphEvalKey(storeGraph), [storeGraph])
  const graphStale = result != null && result.graphKey !== graphKey
  const windowStale = result != null && graphWindow != null
    && windowKey(windowOfRequest(result.request)) !== windowKey(graphWindow)
  // ...or when capital, direction or an APPLIES TO GRAPH setting changed in
  // the settings panel since the run (CI-09). App-driven runs only: on its
  // own (tests) the builder runs from the loaded request.
  // A graph with Output Groups sends no direction (D7), so its key has none.
  const runSettingsKey = useGraphRunSettingsKey(graphHasGroups(storeGraph))
  const settingsStale = result != null && onRunGraph != null
    && requestSettingsKey(result.request) !== runSettingsKey
  const resultStale = graphStale || windowStale || settingsStale

  // The status bar reads the cook state from the store (status slice).
  const setCook = useNodeBuilderStore(s => s.setCook)
  useEffect(() => { setCook({ stale: resultStale }) }, [resultStale, setCook])

  // The chart bar (S28) under the canvas.
  const chartBar = useMemo<ChartBarModel>(
    () => ({ result, stale: resultStale, running: backtestRunning, ticker: runWindow.ticker }),
    [result, resultStale, backtestRunning, runWindow.ticker],
  )

  function clearRun() {
    runIdRef.current += 1
    abortRun()
    setBacktestRunning(false)
    publishResult(null)
    setRunError(null)
    setCook(IDLE_COOK)
  }

  // A different graph on screen (open, new, edit copy) drops the last run
  // and any run still in flight. A save or Save as keeps it. So does the
  // elk tidy that lands a moment after "Edit this graph" (3.F): it bumps the
  // epoch but only moves nodes, so the same graph id and eval key mean the
  // run still belongs to the graph on screen.
  const firstEpoch = useRef(true)
  const loadKeyRef = useRef<string | null>(null)
  useEffect(() => {
    const s = useNodeBuilderStore.getState()
    const loadKey = `${s.graphMeta?.id ?? ''}#${graphEvalKey(s.graph)}`
    const sameGraph = loadKey === loadKeyRef.current
    loadKeyRef.current = loadKey
    if (firstEpoch.current) {
      firstEpoch.current = false
      return
    }
    if (sameGraph) return
    clearRun()
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    setCook({ phase: 'cancelled', endedAt: Date.now() })
  }

  // Without App's handler (tests), run the request here. The ticker and
  // interval then come from the graph's Ticker node first, as before W4.
  function runStandalone(args: GraphRunArgs): Promise<GraphResultState> {
    const tickerNode = Object.values(args.graph.nodes).find(n => n.type === 'ticker')
    const base = graphWindow ?? {
      ...fallbackWindow,
      ticker: (tickerNode?.params?.symbol as string | undefined) ?? fallbackWindow.ticker,
      interval: (tickerNode?.params?.interval as string | undefined) ?? fallbackWindow.interval,
    }
    const direction = request?.direction === 'short' ? 'short' : request?.direction === 'long' ? 'long' : undefined
    return runGraphBacktest(args, { ...base, initial_capital: request?.initial_capital ?? 10000 }, { direction })
  }

  async function handleRunBacktest() {
    // Read the stores now, not this render's copies: Cmd+Enter in a param
    // field commits the field and runs in the same key press, before React
    // renders again.
    const { graph: runGraph, commitSeq: seqAtStart, graphMeta: meta } = useNodeBuilderStore.getState()
    if (runGraph == null || runGraph.readOnly || backtestRunning) return
    const runHasNodes = Object.keys(runGraph.nodes).length > 0
    if (runDisabledReason(getDiagnosticsView().errorCount, runHasNodes) != null) return
    const runId = ++runIdRef.current
    abortRun()
    const ctrl = new AbortController()
    runAbortRef.current = ctrl
    setBacktestRunning(true)
    setRunError(null)
    // The last result stays on screen while this run cooks (the chart bar
    // reads "running…"); it is replaced when the new one arrives.
    // `stale` is left alone (UX-03): it follows resultStale, so a failed or
    // stopped run keeps the old result marked stale in the Results header.
    setCook({ phase: 'cooking', kind: 'backtest', startedAt: Date.now(), endedAt: null, failedNodeId: null })
    try {
      // The window and capital come from the sidebar and the settings panel
      // (D11); the data source is the sidebar's only. Graph-owned fields
      // (size, stop, costs) are never sent (graphRun.buildGraphRequest).
      const args: GraphRunArgs = {
        graph: runGraph,
        graphId: meta?.id ?? null,
        rev: meta?.id ? meta.rev : null,
        graphName: meta?.name ?? null,
        signal: ctrl.signal,
      }
      const next = await (onRunGraph ? onRunGraph(args) : runStandalone(args))
      if (runId !== runIdRef.current) return  // superseded, cleared or stopped
      publishResult(next)
      // The backtest's cook id lets auto cook refresh the sparklines from it.
      setCook({ phase: 'cooked', endedAt: Date.now(), cookId: next.response.cook_id ?? null })
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
      setCook({ phase: 'failed', endedAt: Date.now(), failedNodeId: badNode })
      // Select the node the server names, so its ring shows which one to fix
      // (select() ignores a node deleted meanwhile).
      if (badNode) focusNode(badNode)
    } finally {
      if (runAbortRef.current === ctrl) runAbortRef.current = null
      if (runId === runIdRef.current) setBacktestRunning(false)
    }
  }

  // Latest handlers for the keyboard commands, registered once.
  const keyHandlers = useRef({ save: session.save, open: session.openBrowser, run: handleRunBacktest, stop: handleStop })
  useEffect(() => {
    keyHandlers.current = { save: session.save, open: session.openBrowser, run: handleRunBacktest, stop: handleStop }
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
        {
          // S27: Esc cancels a running backtest cook. Only while one runs, so
          // Esc keeps its other meanings (clear selection) the rest of the time.
          id: 'cook.cancel',
          label: 'Cancel backtest',
          keys: ['escape'],
          when: s => s.cooks.backtest.phase === 'cooking',
          run: () => keyHandlers.current.stop(),
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
              <button type="button" className="nb-banner__link" onClick={() => focusNode(node.id)}>
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
                onClick={() => focusNode(u.id)}
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

  // What slot components reach through useBuilder(). One object for the
  // builder's life, so the panels that read it do not re-render with every
  // NodeBuilder render; its members always use the latest session and run.
  const builderRef = useRef({ session, run: handleRunBacktest, stop: handleStop })
  useEffect(() => {
    builderRef.current = { session, run: handleRunBacktest, stop: handleStop }
  })
  const [builderApi] = useState<BuilderApi>(() => ({
    get session() { return builderRef.current.session },
    runBacktest: () => void builderRef.current.run(),
    stopBacktest: () => builderRef.current.stop(),
    openDiagnostics: anchor => setDiagAnchor(a => (a === anchor ? null : anchor)),
  }))

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

  // The toolbar row. GraphToolbar draws the toolbarLeft / toolbarRight slots
  // inside its own clusters (A1 order) and the S22 hint in its middle.
  const toolbarRow = (
    <div style={styles.toolbarRow}>
      <div style={styles.toolbarMain}>{toolbar}</div>
    </div>
  )

  let content: ReactNode
  if (request == null && !editMode) {
    content = (
      <>
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
      </>
    )
  } else {
    content = (
      <>
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
        {/* S28: canvas, chart panel (its bar holds the run summary that the
             old results strip showed) and the Data Sheet, in one split. */}
        {activeGraph && (
          <GraphChartSplit
            bar={chartBar}
            sheet={
              <GraphSheet
                window={graphWindow}
                result={result}
                readOnlyGraph={editMode ? undefined : activeGraph}
                onRunBacktest={builderApi.runBacktest}
                onEditGraph={editMode ? undefined : handleEditThisGraph}
              />
            }
            canvas={
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
            }
          />
        )}
      </>
    )
  }

  // The frame and its slots (see the file header). The Inspector (rightPanel)
  // runs the full height beside the toolbar and the canvas; the status bar
  // runs the full width under both.
  return (
    <BuilderContext.Provider value={builderApi}>
    {/* D11 before W5: Ticker nodes show the sidebar symbol and interval (UX-01). */}
    <SidebarWindowContext.Provider value={graphWindow ?? null}>
      <div
        ref={rootRef}
        className="nodebuilder-root"
        style={styles.root}
        tabIndex={-1}
        data-nb-builder=""
        // S19: no browser menu anywhere in the builder (minimap, Inspector,
        // toolbar, status bar, the ContextMenu key on the focused root);
        // text fields keep theirs (UX-05).
        onContextMenu={e => { if (!isTypingTarget(e.target)) e.preventDefault() }}
      >
        <div style={styles.main}>
          <div style={styles.column} data-nb-column="">
            {toolbarRow}
            {content}
            <Slot name="bottomPanel" />
          </div>
          <Slot name="rightPanel" />
        </div>
        <Slot name="statusBar" />
        {shared}
        <Slot name="overlays" />
        <Slot name="dialogs" />
      </div>
    </SidebarWindowContext.Provider>
    </BuilderContext.Provider>
  )
}

// Memoized so App re-renders don't re-render the hidden graph tree (perf P6).
// Props are App state (lastRequest, graphResult, the memoized window), a
// boolean and stable callbacks, so they are stable.
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
  // Canvas column plus the right panel.
  main: {
    flex: 1,
    minHeight: 0,
    display: 'flex',
    flexDirection: 'row',
    position: 'relative',
  },
  // Toolbar, notices, the canvas/chart/sheet split and the bottom panel.
  column: {
    flex: 1,
    minWidth: 0,
    display: 'flex',
    flexDirection: 'column',
    position: 'relative',
  },
  toolbarRow: {
    display: 'flex',
    alignItems: 'stretch',
    flexShrink: 0,
  },
  toolbarMain: {
    flex: 1,
    minWidth: 0,
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
}
