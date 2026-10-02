/**
 * Graph results in the app's Results panel (spec S30).
 *
 * - `GraphResultHeader`: the line above a graph result: a blue GRAPH pill,
 *   `name @ rev N · TICKER interval · start → end · cooked HH:MM:SS`, a
 *   `stale` badge when the graph changed after the run, and `Show graph`
 *   (only when the user is not already in graph view).
 * - `NotAvailableForGraph`: replaces the Sensitivity, Optimizer and
 *   Walk-Forward bodies for a graph result. Those panels re-run a rule
 *   request (`lastRequest`), which a graph run never writes (D10).
 *
 * App tokens (`--gh-*`), since these render inside Results.tsx.
 */

import type { CSSProperties } from 'react'

/** What Results.tsx needs to show a graph result honestly. */
export interface GraphResultInfo {
  /** `name @ rev N · TICKER interval · start → end · cooked HH:MM:SS` */
  headerText: string
  /** The graph or the window changed after the run. */
  stale: boolean
  /** Switch to graph view (set only outside graph view). */
  onShowGraph?: () => void
  /** Switch back to the chart view, where the rule result returns. */
  onBackToChart?: () => void
}

export const NOT_AVAILABLE_TITLE = 'Not available for graph results'
export const NOT_AVAILABLE_BODY =
  'Sensitivity, Optimizer and Walk-Forward work on rule strategies. They re-run a rule request with changed parameters, and a graph has no such request yet.'
export const NOT_AVAILABLE_LATER = 'Graph parameter sweeps are planned for a later wave.'

const pill: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  height: 16,
  padding: '0 5px',
  borderRadius: 3,
  fontSize: 10,
  fontWeight: 600,
  letterSpacing: '0.04em',
  flexShrink: 0,
}

export function GraphResultHeader({ info }: { info: GraphResultInfo }) {
  return (
    <div role="status" data-testid="results-graph-header" style={styles.header}>
      <span style={{ ...pill, background: 'rgba(88,166,255,0.14)', color: '#58a6ff' }}>GRAPH</span>
      <span style={styles.headerText} title={info.headerText} data-testid="results-graph-header-text">{info.headerText}</span>
      {info.stale && (
        <span
          style={{ ...pill, background: 'rgba(210,153,34,0.14)', color: 'var(--gh-yellow, #d29922)' }}
          title="The graph or the sidebar window changed after this run. Run the backtest again to update."
          data-testid="results-graph-stale"
        >
          stale
        </span>
      )}
      {info.onShowGraph && (
        <button type="button" style={styles.textButton} onClick={info.onShowGraph} data-testid="results-show-graph">
          Show graph
        </button>
      )}
    </div>
  )
}

/**
 * S30 "Show graph" in the chart view: the graph Results instance lives in
 * graph view only, so the chart view gets this one line above the rule
 * Results while a graph result exists (UX-14).
 */
export function GraphResultHint({ headerText, onShowGraph }: { headerText: string; onShowGraph: () => void }) {
  return (
    <div data-testid="results-graph-hint" style={styles.hint}>
      <span style={{ ...pill, background: 'rgba(88,166,255,0.14)', color: '#58a6ff' }}>GRAPH</span>
      <span style={styles.hintText} title={headerText}>Graph result available · {headerText}</span>
      <button type="button" style={styles.textButton} onClick={onShowGraph} data-testid="results-show-graph">
        Show graph
      </button>
    </div>
  )
}

export function NotAvailableForGraph({ onBackToChart }: { onBackToChart?: () => void }) {
  return (
    <div role="region" aria-labelledby="results-not-available-title" data-testid="results-not-available" style={styles.block}>
      <h3 id="results-not-available-title" style={styles.blockTitle}>{NOT_AVAILABLE_TITLE}</h3>
      <p style={styles.blockBody}>{NOT_AVAILABLE_BODY}</p>
      {onBackToChart && (
        <button type="button" style={styles.button} onClick={onBackToChart} data-testid="results-back-to-chart">
          Back to Chart view
        </button>
      )}
      <p style={styles.blockLater}>{NOT_AVAILABLE_LATER}</p>
    </div>
  )
}

const styles: Record<string, CSSProperties> = {
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    minHeight: 28,
    padding: '0 12px',
    fontSize: 11,
    color: 'var(--gh-text-muted)',
    borderBottom: '1px solid var(--gh-border)',
    flexShrink: 0,
  },
  // Wraps to a second line in the narrow right panel (UX-07) so the end
  // date and the cooked time stay readable; the full text is also the title.
  headerText: {
    minWidth: 0,
    overflow: 'hidden',
    display: '-webkit-box',
    WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical',
    whiteSpace: 'normal',
    overflowWrap: 'anywhere',
    lineHeight: '14px',
    padding: '3px 0',
  },
  hint: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    minHeight: 24,
    padding: '0 12px',
    fontSize: 11,
    color: 'var(--gh-text-muted)',
    borderBottom: '1px solid var(--gh-border)',
  },
  hintText: {
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  textButton: {
    marginLeft: 'auto',
    background: 'none',
    border: 'none',
    color: 'var(--gh-blue)',
    fontSize: 11,
    cursor: 'pointer',
    padding: '2px 4px',
    flexShrink: 0,
  },
  block: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    textAlign: 'center',
    padding: 32,
    gap: 10,
  },
  blockTitle: {
    margin: 0,
    fontSize: 13,
    fontWeight: 600,
    color: 'var(--gh-text-primary)',
  },
  blockBody: {
    margin: 0,
    fontSize: 12,
    color: 'var(--gh-text-muted)',
    maxWidth: 420,
    lineHeight: 1.5,
  },
  button: {
    fontSize: 12,
    padding: '4px 12px',
    borderRadius: 4,
    background: '#21262d',
    color: 'var(--gh-text-primary)',
    border: '1px solid var(--gh-border)',
    cursor: 'pointer',
  },
  blockLater: {
    margin: 0,
    fontSize: 11,
    color: 'var(--gh-text-muted)',
  },
}
