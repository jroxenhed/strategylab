import { useState, useEffect, useCallback, useRef } from 'react'
import type { PremiseListItem, PremiseStatus, Disposition } from '../../api/premises'
import { listPremises, createPremise } from '../../api/premises'
import PremiseDetail from './PremiseDetail'

function statusLabel(status: PremiseStatus): string {
  switch (status) {
    case 'draft': return 'Draft'
    case 'awaiting_formalization': return 'Awaiting AI'
    case 'spec_ready': return 'Ready'
    case 'exploring': return 'Running'
    case 'explored': return 'Explored'
    case 'awaiting_confirm': return 'Awaiting Confirm'
    case 'confirmed': return 'Confirmed'
    default: return status
  }
}

function statusColor(status: PremiseStatus): string {
  switch (status) {
    case 'draft': return '#484f58'
    case 'awaiting_formalization': return '#f0883e'
    case 'spec_ready': return '#58a6ff'
    case 'exploring': return '#3fb950'
    case 'explored': return '#1f6feb'
    case 'awaiting_confirm': return '#f0883e'
    case 'confirmed': return '#238636'
    default: return '#484f58'
  }
}

// F397: disposition display helpers
type DispositionFilter = 'all' | 'active' | 'parked' | 'rejected' | 'promising'

function dispositionLabel(d: Disposition | null | undefined): string {
  switch (d) {
    case 'active': return 'Active'
    case 'parked_needs_data': return 'Parked — needs data'
    case 'parked_sharpen': return 'Parked — sharpen'
    case 'rejected': return 'Rejected'
    case 'promising': return 'Promising'
    default: return 'Active'
  }
}

function dispositionColor(d: Disposition | null | undefined): string {
  switch (d) {
    case 'active': return '#484f58'
    case 'parked_needs_data': return '#8b949e'
    case 'parked_sharpen': return '#8b949e'
    case 'rejected': return '#f85149'
    case 'promising': return '#3fb950'
    default: return '#484f58'
  }
}

function matchesDispositionFilter(d: Disposition | null | undefined, filter: DispositionFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'parked') return d === 'parked_needs_data' || d === 'parked_sharpen'
  return (d ?? 'active') === filter
}

export default function PremiseLibrary() {
  const [premises, setPremises] = useState<PremiseListItem[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState<string | null>(null)

  // F397: disposition filter
  const [dispositionFilter, setDispositionFilter] = useState<DispositionFilter>('all')

  // New premise form
  const [newText, setNewText] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  // H5: mounted guard — prevents setState-after-unmount from an in-flight fetch
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const refreshList = useCallback(async () => {
    try {
      const list = await listPremises()
      if (!mountedRef.current) return
      setPremises(list)
      setListError(null)
    } catch (e: unknown) {
      if (!mountedRef.current) return
      const msg = (e as { response?: { data?: { detail?: string } }; message?: string })
        ?.response?.data?.detail ?? (e as { message?: string })?.message ?? 'Failed to load premises'
      setListError(msg)
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    refreshList()
  }, [refreshList])

  // F397: live refresh every 15s
  const liveRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null)
  useEffect(() => {
    liveRefreshRef.current = setInterval(() => {
      refreshList()
    }, 15000)
    return () => {
      if (liveRefreshRef.current) {
        clearInterval(liveRefreshRef.current)
        liveRefreshRef.current = null
      }
    }
  }, [refreshList])

  const handleCreate = async () => {
    if (!newText.trim()) return
    setCreating(true)
    setCreateError(null)
    try {
      const resp = await createPremise({ premise_text: newText.trim() })
      setNewText('')
      await refreshList()
      setSelectedId(resp.premise_id)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to create premise'
      setCreateError(msg)
    } finally {
      setCreating(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      handleCreate()
    }
  }

  return (
    <div style={styles.root}>
      {/* Left panel — list */}
      <div style={styles.listPanel}>
        <div style={styles.listHeader}>
          <span style={styles.listTitle}>Premise Library</span>
          <button onClick={refreshList} style={styles.refreshBtn} title="Refresh list">↻</button>
        </div>

        {/* New premise form */}
        <div style={styles.newPremiseForm}>
          <textarea
            style={styles.newPremiseTextarea}
            placeholder="Describe a trading idea or signal hypothesis…"
            value={newText}
            onChange={e => setNewText(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
          />
          {createError && <div style={styles.inlineError}>{createError}</div>}
          <button
            onClick={handleCreate}
            disabled={creating || !newText.trim()}
            style={newText.trim() ? styles.createBtn : styles.createBtnDisabled}
          >
            {creating ? 'Creating…' : '+ New Premise'}
          </button>
          <div style={styles.createHint}>Cmd+Enter to create</div>
        </div>

        <div style={styles.divider} />

        {/* F397: disposition filter tabs */}
        <div style={styles.filterRow}>
          {(['all', 'active', 'parked', 'promising', 'rejected'] as DispositionFilter[]).map(f => (
            <button
              key={f}
              onClick={() => setDispositionFilter(f)}
              style={{
                ...styles.filterTab,
                ...(dispositionFilter === f ? styles.filterTabActive : {}),
              }}
            >
              {f === 'all' ? 'All' : f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>

        {/* List */}
        <div style={styles.listScroll}>
          {loading && <div style={styles.listEmpty}>Loading…</div>}
          {!loading && listError && <div style={styles.listError}>{listError}</div>}
          {!loading && !listError && premises.length === 0 && (
            <div style={styles.listEmpty}>No premises yet. Create one above.</div>
          )}
          {!loading && premises
            .filter(p => matchesDispositionFilter(p.disposition, dispositionFilter))
            .map(p => (
            <button
              key={p.premise_id}
              onClick={() => setSelectedId(p.premise_id)}
              style={{
                ...styles.listItem,
                ...(selectedId === p.premise_id ? styles.listItemActive : {}),
              }}
            >
              <div style={styles.listItemTopRow}>
                <span
                  style={{
                    ...styles.chip,
                    background: statusColor(p.status) + '22',
                    color: statusColor(p.status),
                    border: `1px solid ${statusColor(p.status)}44`,
                  }}
                >
                  {statusLabel(p.status)}
                </span>
                <span
                  style={{
                    ...styles.chip,
                    background: dispositionColor(p.disposition) + '22',
                    color: dispositionColor(p.disposition),
                    border: `1px solid ${dispositionColor(p.disposition)}44`,
                  }}
                >
                  {dispositionLabel(p.disposition)}
                </span>
              </div>
              <span style={styles.listItemExcerpt}>{p.premise_text_excerpt}</span>
              {p.machine_outcome && p.machine_outcome !== '—' && (
                <span style={styles.machineOutcome}>{p.machine_outcome}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Right panel — detail */}
      <div style={styles.detailPanel}>
        {selectedId ? (
          <PremiseDetail
            key={selectedId}
            premiseId={selectedId}
            onDeleted={() => {
              setSelectedId(null)
              refreshList()
            }}
            onStatusChange={() => {
              refreshList()
            }}
            onSelectId={(id: string) => {
              setSelectedId(id)
              refreshList()
            }}
          />
        ) : (
          <div style={styles.noSelection}>
            Select a premise to view details, or create a new one.
          </div>
        )}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    height: '100%',
    overflow: 'hidden',
  },
  listPanel: {
    width: 280,
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'column',
    borderRight: '1px solid #21262d',
    background: '#0d1117',
    overflow: 'hidden',
  },
  listHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '14px 14px 10px',
    flexShrink: 0,
  },
  listTitle: {
    fontSize: 11,
    fontWeight: 700,
    color: '#8b949e',
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
  },
  refreshBtn: {
    fontSize: 11,
    padding: '2px 8px',
    borderRadius: 4,
    background: '#21262d',
    color: '#8b949e',
    border: '1px solid #30363d',
    cursor: 'pointer',
  },
  newPremiseForm: {
    padding: '0 12px 10px',
    flexShrink: 0,
  },
  newPremiseTextarea: {
    width: '100%',
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 6,
    color: '#e6edf3',
    fontSize: 12,
    padding: '8px 10px',
    lineHeight: 1.5,
    resize: 'none',
    boxSizing: 'border-box',
    fontFamily: 'inherit',
    marginBottom: 6,
  },
  createBtn: {
    width: '100%',
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 12px',
    background: '#1f6feb',
    color: '#e6edf3',
    border: 'none',
    borderRadius: 6,
    cursor: 'pointer',
    marginBottom: 4,
  },
  createBtnDisabled: {
    width: '100%',
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 12px',
    background: '#21262d',
    color: '#484f58',
    border: 'none',
    borderRadius: 6,
    cursor: 'not-allowed',
    opacity: 0.6,
    marginBottom: 4,
  },
  createHint: {
    fontSize: 10,
    color: '#484f58',
    textAlign: 'right' as const,
  },
  inlineError: {
    marginBottom: 6,
    padding: '5px 8px',
    background: '#f8514922',
    border: '1px solid #f8514944',
    borderRadius: 4,
    color: '#f85149',
    fontSize: 11,
  },
  divider: {
    borderBottom: '1px solid #21262d',
    flexShrink: 0,
  },
  listScroll: {
    flex: 1,
    overflowY: 'auto',
    padding: '8px 0',
  },
  listEmpty: {
    padding: '12px 14px',
    fontSize: 12,
    color: '#484f58',
  },
  listError: {
    padding: '12px 14px',
    fontSize: 12,
    color: '#f85149',
  },
  filterRow: {
    display: 'flex',
    flexWrap: 'wrap' as const,
    gap: 4,
    padding: '6px 12px',
    borderBottom: '1px solid #21262d',
    flexShrink: 0,
  },
  filterTab: {
    fontSize: 10,
    padding: '2px 8px',
    borderRadius: 100,
    background: 'none',
    color: '#484f58',
    border: '1px solid #30363d',
    cursor: 'pointer',
    fontWeight: 500,
  },
  filterTabActive: {
    background: '#21262d',
    color: '#e6edf3',
    border: '1px solid #8b949e',
  },
  listItem: {
    width: '100%',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: 5,
    padding: '10px 12px',
    background: 'none',
    border: 'none',
    borderBottom: '1px solid #21262d',
    cursor: 'pointer',
    textAlign: 'left' as const,
  },
  listItemActive: {
    background: '#161b22',
  },
  listItemTopRow: {
    display: 'flex',
    gap: 5,
    flexWrap: 'wrap' as const,
    alignItems: 'center',
  },
  chip: {
    display: 'inline-block',
    fontSize: 10,
    fontWeight: 700,
    padding: '1px 6px',
    borderRadius: 100,
    letterSpacing: '0.04em',
    flexShrink: 0,
  },
  listItemExcerpt: {
    fontSize: 12,
    color: '#8b949e',
    lineHeight: 1.4,
    display: '-webkit-box',
    WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical' as const,
    overflow: 'hidden',
  },
  machineOutcome: {
    fontSize: 11,
    color: '#58a6ff',
    fontFamily: 'monospace',
    lineHeight: 1.3,
  },
  detailPanel: {
    flex: 1,
    overflow: 'hidden',
    background: '#0d1117',
    minWidth: 0,
  },
  noSelection: {
    padding: 32,
    fontSize: 13,
    color: '#484f58',
  },
}
