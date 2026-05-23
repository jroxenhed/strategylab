/**
 * NodeBuilder — top-level feature component for the graph viewer + editor.
 *
 * Unit 4a: read-only graph viewer (auto-render via TanStack Query).
 * Unit 5: editable graph mode via Zustand store.
 *
 * Modes:
 * 1. View mode (default): renders the TanStack Query auto-render result read-only.
 * 2. Edit mode: the Zustand store has a graph (graph.readOnly=false); Canvas uses it.
 *
 * The "New Empty Graph" button creates a blank editable graph in the store and
 * switches to edit mode. This is the minimum-viable entry point for Unit 5
 * validation. The full Tab-menu / port-drag entry point arrives in Unit 6.
 */

import { useQuery } from '@tanstack/react-query'
import type { StrategyRequest } from '../../shared/types/strategy'
import { fetchAutoRender } from '../../api/nodebuilder'
import Canvas from './Canvas'
import { useNodeBuilderStore } from './store'
import './tokens.css'

interface NodeBuilderProps {
  request: StrategyRequest | null
  graphViewActive: boolean
}

export default function NodeBuilder({ request, graphViewActive }: NodeBuilderProps) {
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
  const newEmptyGraph = useNodeBuilderStore(s => s.newEmptyGraph)

  // Edit mode = store has a graph (readOnly=false)
  const editMode = storeGraph !== null && !storeGraph.readOnly

  // Which graph to pass to Canvas
  const activeGraph = editMode ? storeGraph : (autoGraph ?? null)

  if (request == null && !editMode) {
    return (
      <div className="nodebuilder-root" style={styles.root}>
        <div style={styles.empty}>
          Load a saved strategy first to view it as a graph.
        </div>
        <div style={styles.toolbar}>
          <button style={styles.btn} onClick={newEmptyGraph}>
            New Empty Graph
          </button>
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
        <button style={styles.btn} onClick={newEmptyGraph}>
          New Empty Graph
        </button>
      </div>

      {/* Content */}
      {isLoading && !editMode && (
        <div style={styles.loadingWrapper}>
          <div className="chart-skeleton" style={styles.skeleton} />
        </div>
      )}
      {error && !editMode && (
        <div style={styles.errorBanner}>
          Failed to render graph: {(error as Error).message}
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
}
