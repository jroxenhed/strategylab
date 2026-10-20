/**
 * RulesStrip: the graph's strategy as plain text (F454).
 *
 * A 22px bar under the chart bar, in the same collapse style: `▸ Rules` and
 * the text on one line. Open, it lists one sentence per line and any
 * warnings below them, so the user sees at a glance what all the nodes do.
 *
 * The text comes from POST /api/nodebuilder/describe, which reads the graph
 * only (no data, no user code). It refreshes about 400 ms after the last
 * change that matters; a newer change cancels the request still on its way.
 * Moving a node or opening a frame changes nothing, so it sends nothing.
 * A failed describe shows in the strip, quietly; it never raises a notice.
 *
 * It sends the symbol, interval and direction the editor run uses (F453
 * contract), so the text always names what "Run backtest" runs. The warning
 * count sits right after the title, so a long text never hides it. While
 * the strip is open its bar shows only the title and the warning count: the
 * sentences are in the body below.
 */

import { useEffect, useRef, useState } from 'react'
import type { Graph } from '../../api/nodebuilder'
import { describeErrorText, describeGraph, isDescribeAbort, type DescribeResponse, type DescribeRunWindow } from '../../api/describe'
import { useSidebarWindow } from './sidebarWindow'
import { graphRunWindow } from './graphTicker'
import { graphHasGroups } from './ownership'
import { useGraphRunDirection } from './graphRun'
import './rulesStrip.css'

/** Wait this long after the last change before asking the server. */
export const RULES_DEBOUNCE_MS = 400
/** Where the open/closed choice is kept. */
export const RULES_OPEN_KEY = 'nb.rules.open'

/**
 * A key for what the text depends on: every node except its position, its
 * display flag and its editor view state, plus the wires and the graph's
 * meta. Sticky notes and boxes are left out.
 */
export function rulesKey(graph: Graph | null): string {
  if (graph == null) return ''
  const nodes = Object.keys(graph.nodes).sort().map(id => {
    const { position: _p, display: _d, meta: _m, ...rest } = graph.nodes[id]
    return rest
  })
  return JSON.stringify({ nodes, wires: graph.wires, meta: graph.meta })
}

export type RulesState =
  | { phase: 'empty' }
  | { phase: 'loading'; data: DescribeResponse | null }
  | { phase: 'ok'; data: DescribeResponse }
  | { phase: 'error'; text: string; data: DescribeResponse | null }

/**
 * The describe text for a graph, refreshed after each change that matters.
 * `run` is what the editor run uses: its symbol, interval and direction.
 * The server uses the direction only for a graph with no Output Group.
 */
export function useGraphRules(graph: Graph | null, run: DescribeRunWindow | null): RulesState {
  const [state, setState] = useState<RulesState>({ phase: 'empty' })
  const graphRef = useRef(graph)
  graphRef.current = graph
  const key = rulesKey(graph)
  const ticker = run?.ticker ?? ''
  const interval = run?.interval ?? ''
  const direction = run?.direction ?? null
  const hasNodes = graph != null && Object.keys(graph.nodes).length > 0

  useEffect(() => {
    if (!hasNodes) {
      setState({ phase: 'empty' })
      return
    }
    // Keep the last text on screen while the new one loads.
    setState(s => ({ phase: 'loading', data: s.phase === 'empty' ? null : s.data }))
    let ctrl: AbortController | null = null
    const timer = setTimeout(() => {
      const g = graphRef.current
      if (!g) return
      ctrl = new AbortController()
      const signal = ctrl.signal
      describeGraph(g, { ticker, interval, direction }, signal)
        .then(data => { if (!signal.aborted) setState({ phase: 'ok', data }) })
        .catch(e => {
          if (signal.aborted || isDescribeAbort(e)) return
          setState(s => ({ phase: 'error', text: describeErrorText(e), data: s.phase === 'empty' ? null : s.data }))
        })
    }, RULES_DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      // A newer change (or leaving) makes this answer old: cancel it.
      ctrl?.abort()
    }
  }, [key, ticker, interval, direction, hasNodes])

  return state
}

function readOpen(): boolean {
  try { return localStorage.getItem(RULES_OPEN_KEY) === '1' } catch { return false }
}
function writeOpen(open: boolean): void {
  try { localStorage.setItem(RULES_OPEN_KEY, open ? '1' : '0') } catch { /* ignore */ }
}

export interface RulesStripProps {
  graph: Graph | null
  /**
   * The symbol and interval the editor run uses (NodeBuilder's effective
   * window). Left out: worked out here from the sidebar, the way the run
   * does (graphRunWindow). Null: none is sent.
   */
  window?: Pick<DescribeRunWindow, 'ticker' | 'interval'> | null
  /** The settings-panel direction; left out: read from the settings panel. */
  direction?: 'long' | 'short' | null
}

export function RulesStrip({ graph, window: runWindow, direction: directionProp }: RulesStripProps) {
  const sidebar = useSidebarWindow()
  const settingsDirection = useGraphRunDirection()
  const win = runWindow !== undefined ? runWindow : (sidebar ? graphRunWindow(graph, sidebar) : null)
  // The run sends a direction only for a graph with no Output Group (D7).
  const direction = graph != null && !graphHasGroups(graph)
    ? (directionProp !== undefined ? directionProp : settingsDirection)
    : null
  const state = useGraphRules(graph, { ticker: win?.ticker, interval: win?.interval, direction })
  const [open, setOpen] = useState(readOpen)
  const toggle = () => setOpen(o => { writeOpen(!o); return !o })

  const data = state.phase === 'empty' ? null : state.data
  const lines = data?.lines ?? []
  const warnings = data?.warnings ?? []
  const busy = state.phase === 'loading'

  let summary: React.ReactNode
  if (state.phase === 'empty') {
    summary = <span className="nb-rules-dim">no nodes yet</span>
  } else if (state.phase === 'error') {
    summary = <span className="nb-rules-dim">can't read the rules yet</span>
  } else if (data == null) {
    summary = <span className="nb-rules-dim">reading the graph…</span>
  } else if (lines.length === 0) {
    summary = <span className="nb-rules-dim">nothing wired yet</span>
  } else {
    summary = lines.join(' ')
  }
  // Shown only with text on screen (not over an error).
  const warningCount = state.phase !== 'error' && warnings.length > 0
    ? (warnings.length === 1 ? '1 warning' : `${warnings.length} warnings`)
    : null

  return (
    <div className="nb-rules" data-testid="nb-rules">
      <button
        type="button"
        className="nb-chart-bar nb-rules-bar"
        data-testid="nb-rules-bar"
        aria-expanded={open}
        aria-controls="nb-rules-body"
        title={open ? 'Hide the rules' : 'Show the rules as plain text'}
        onClick={toggle}
      >
        <span className="nb-chart-bar__toggle">
          <span aria-hidden="true">{open ? '▾' : '▸'}</span>
          {' '}Rules
        </span>
        {warningCount && (
          <span className="nb-rules-warn nb-rules-count" data-testid="nb-rules-count">{warningCount}</span>
        )}
        {!open && (
          <span
            className={`nb-chart-bar__summary nb-rules-summary${busy && data ? ' nb-rules-summary--busy' : ''}`}
            data-testid="nb-rules-summary"
          >
            {summary}
          </span>
        )}
      </button>
      {open && (
        <div id="nb-rules-body" className="nb-rules-body" role="region" aria-label="Rules" data-testid="nb-rules-body">
          {state.phase === 'error' && (
            <div className="nb-rules-dim" data-testid="nb-rules-error">Can't read the rules yet: {state.text}</div>
          )}
          {state.phase === 'empty' && <div className="nb-rules-dim">Add nodes to see the rules here.</div>}
          {state.phase !== 'empty' && state.phase !== 'error' && data == null && (
            <div className="nb-rules-dim">Reading the graph…</div>
          )}
          {data != null && state.phase !== 'error' && lines.length === 0 && (
            <div className="nb-rules-dim">Nothing is wired into Entry or Exit yet.</div>
          )}
          {state.phase !== 'error' && lines.map((line, i) => (
            <div key={i} className="nb-rules-line" data-testid="nb-rules-line">{line}</div>
          ))}
          {state.phase !== 'error' && warnings.map((w, i) => (
            <div key={`w${i}`} className="nb-rules-warn" data-testid="nb-rules-warning">{w}</div>
          ))}
        </div>
      )}
    </div>
  )
}

export default RulesStrip
