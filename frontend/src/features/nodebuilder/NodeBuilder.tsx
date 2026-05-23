/**
 * NodeBuilder — top-level feature component for the Unit 4a read-only graph viewer.
 *
 * Calls POST /api/nodebuilder/auto_render, renders <Canvas> with the result.
 * TanStack Query caches by strategy hash (staleTime: Infinity — deterministic transform).
 */

import { useQuery } from '@tanstack/react-query'
import type { StrategyRequest } from '../../shared/types/strategy'
import { fetchAutoRender } from '../../api/nodebuilder'
import Canvas from './Canvas'
import './tokens.css'

interface NodeBuilderProps {
  request: StrategyRequest | null
  graphViewActive: boolean
}

export default function NodeBuilder({ request, graphViewActive }: NodeBuilderProps) {
  // Stable cache key: JSON.stringify is deterministic within a session (key order
  // is consistent since objects are built programmatically, not from user input).
  const strategyHash = request != null ? JSON.stringify(request) : null

  const { data: graph, isLoading, error } = useQuery({
    queryKey: ['nodebuilder', 'auto_render', strategyHash],
    queryFn: () => fetchAutoRender(request!),
    enabled: request != null && graphViewActive,
    staleTime: Infinity,
    gcTime: Infinity,
  })

  if (request == null) {
    return (
      <div className="nodebuilder-root" style={styles.root}>
        <div style={styles.empty}>
          Load a saved strategy first to view it as a graph.
        </div>
      </div>
    )
  }

  return (
    <div className="nodebuilder-root" style={styles.root}>
      {isLoading && (
        <div style={styles.loadingWrapper}>
          <div className="chart-skeleton" style={styles.skeleton} />
        </div>
      )}
      {error && (
        <div style={styles.errorBanner}>
          Failed to render graph: {(error as Error).message}
        </div>
      )}
      {graph && !isLoading && (
        <Canvas graph={graph} />
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
  empty: {
    margin: 'auto',
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
