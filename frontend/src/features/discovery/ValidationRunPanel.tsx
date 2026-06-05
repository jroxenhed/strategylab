import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '../../api/client'

// --- Types ---

interface ValidationProgress {
  dates_done: number
  dates_total: number
  current_date: string | null
  symbols_loaded: number
  universe_size: number
  events_so_far: { signal: number; null: number } | null
}

interface ValidationStatus {
  status: 'idle' | 'running' | 'done' | 'error' | 'cancelled' | 'timeout'
  started_at: string | null
  duration_secs: number | null
  error: string | null
  progress?: ValidationProgress | null
}

interface ValidationResult {
  signal_hit_rate?: number | null
  signal_ci_low?: number | null
  signal_ci_high?: number | null
  null_hit_rate?: number | null
  null_ci_low?: number | null
  null_ci_high?: number | null
  signal_events?: number | null
  null_events?: number | null
  duration_secs?: number | null
  [key: string]: unknown
}

// --- Helpers ---

function fmtDuration(secs: number | null | undefined): string {
  if (secs == null) return ''
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return m > 0 ? `${m}m ${s.toString().padStart(2, '0')}s` : `${s}s`
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

// --- Component ---

export default function ValidationRunPanel() {
  const [collapsed, setCollapsed] = useState(false)
  const [status, setStatus] = useState<ValidationStatus | null>(null)
  const [result, setResult] = useState<ValidationResult | null>(null)
  const [loadingResult, setLoadingResult] = useState(false)
  const [confirmingCancel, setConfirmingCancel] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.get<ValidationStatus>('/api/turnaround/validate/status')
      setStatus(res.data)
    } catch {
      // silently ignore — server might not have the endpoint in older builds
    }
  }, [])

  const fetchResult = useCallback(async () => {
    setLoadingResult(true)
    try {
      const res = await api.get<ValidationResult>('/api/turnaround/validate/result')
      setResult(res.data)
    } catch {
      setResult(null)
    } finally {
      setLoadingResult(false)
    }
  }, [])

  // On mount: fetch status once; if done/cancelled, also fetch result
  useEffect(() => {
    fetchStatus().then(() => {
      // fetchStatus sets status; we need to check after it resolves
    })
  }, [fetchStatus])

  // Fetch result when status reaches a terminal done state
  useEffect(() => {
    if (status?.status === 'done') {
      fetchResult()
    }
  }, [status?.status, fetchResult])

  // Poll every 2s while running
  useEffect(() => {
    if (status?.status === 'running') {
      intervalRef.current = setInterval(fetchStatus, 2000)
    } else {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [status?.status, fetchStatus])

  const handleCancel = async () => {
    setConfirmingCancel(false)
    setCancelError(null)
    try {
      await api.post('/api/turnaround/validate/cancel')
      await fetchStatus()
    } catch (e: unknown) {
      if (e && typeof e === 'object' && 'response' in e) {
        const err = e as { response: { status: number } }
        if (err.response?.status === 409) {
          // no run in flight — just refresh status
          await fetchStatus()
          return
        }
      }
      setCancelError('Cancel failed')
    }
  }

  const handleRefresh = () => {
    fetchStatus()
  }

  const st = status?.status ?? 'unknown'
  const isRunning = st === 'running'
  const isDone = st === 'done'

  const progress = status?.progress ?? null
  const datesDone = progress?.dates_done ?? 0
  const datesTotal = progress?.dates_total ?? 0
  const symsLoaded = progress?.symbols_loaded ?? 0
  const universeSize = progress?.universe_size ?? 0
  const currentDate = progress?.current_date ?? null
  const eventsSig = progress?.events_so_far?.signal ?? 0
  const eventsNull = progress?.events_so_far?.null ?? 0

  // Progress fractions (guard against divide-by-zero)
  const dateFrac = datesTotal > 0 ? Math.min(datesDone / datesTotal, 1) : 0
  const symFrac = universeSize > 0 ? Math.min(symsLoaded / universeSize, 1) : 0

  return (
    <div style={styles.section}>
      {/* Header row */}
      <div style={styles.header}>
        <button style={styles.collapseBtn} onClick={() => setCollapsed(c => !c)} aria-label="Toggle panel">
          <span style={{ display: 'inline-block', transform: collapsed ? 'rotate(-90deg)' : 'rotate(0deg)', transition: 'transform 0.15s' }}>▾</span>
        </button>
        <span style={styles.title}>Validation Run</span>
        {status && (
          <span style={{ ...styles.statusBadge, ...statusBadgeStyle(st) }}>
            {st.toUpperCase()}
          </span>
        )}
        {isRunning && status?.duration_secs != null && (
          <span style={styles.elapsed}>
            {fmtDuration(status.duration_secs)} elapsed
          </span>
        )}
        <div style={{ flex: 1 }} />
        {!isRunning && (
          <button style={styles.refreshBtn} onClick={handleRefresh} title="Refresh status">↻</button>
        )}
      </div>

      {!collapsed && (
        <div style={styles.body}>

          {/* No status yet */}
          {!status && (
            <div style={styles.idleNote}>Fetching status…</div>
          )}

          {/* Idle */}
          {st === 'idle' && (
            <div style={styles.idleNote}>No run in flight.</div>
          )}

          {/* Running */}
          {isRunning && (
            <div style={styles.runBody}>

              {/* Symbol counter — visually prominent (date 1 takes 60+ min, this is the real signal) */}
              <div style={styles.progressGroup}>
                <div style={styles.progressLabel}>
                  <span style={styles.progressLabelMain}>Symbols loaded</span>
                  <span style={styles.progressLabelVal}>
                    {symsLoaded.toLocaleString()} / {universeSize > 0 ? universeSize.toLocaleString() : '…'}
                    {universeSize > 0 && ` (${(symFrac * 100).toFixed(0)}%)`}
                  </span>
                </div>
                <div style={styles.barTrack}>
                  <div style={{ ...styles.barFill, ...styles.barFillSymbol, width: `${(symFrac * 100).toFixed(1)}%` }} />
                </div>
              </div>

              {/* Date macro bar */}
              <div style={styles.progressGroup}>
                <div style={styles.progressLabel}>
                  <span style={styles.progressLabelSecondary}>Date</span>
                  <span style={styles.progressLabelValSecondary}>
                    {datesDone} of {datesTotal > 0 ? datesTotal : '…'}
                    {currentDate ? ` (${currentDate})` : ''}
                  </span>
                </div>
                <div style={{ ...styles.barTrack, height: 4 }}>
                  <div style={{ ...styles.barFill, ...styles.barFillDate, width: `${(dateFrac * 100).toFixed(1)}%` }} />
                </div>
              </div>

              {/* Live event counts */}
              {(eventsSig > 0 || eventsNull > 0) && (
                <div style={styles.liveEvents}>
                  <span style={styles.liveEventsLabel}>Events so far:</span>
                  <span style={{ color: '#3fb950' }}>{eventsSig} signal</span>
                  <span style={{ color: '#8b949e', margin: '0 4px' }}>/</span>
                  <span style={{ color: '#58a6ff' }}>{eventsNull} null</span>
                </div>
              )}

              {/* Cancel */}
              <div style={styles.cancelRow}>
                {cancelError && <span style={{ color: '#f85149', fontSize: 11, marginRight: 8 }}>{cancelError}</span>}
                {confirmingCancel ? (
                  <>
                    <span style={styles.confirmText}>Cancel the running validation?</span>
                    <button style={styles.confirmBtn} onClick={handleCancel}>Confirm</button>
                    <button style={styles.cancelConfirmBtn} onClick={() => setConfirmingCancel(false)}>No</button>
                  </>
                ) : (
                  <button style={styles.cancelBtn} onClick={() => setConfirmingCancel(true)}>Cancel run</button>
                )}
              </div>
            </div>
          )}

          {/* Done — one-line result summary */}
          {isDone && (
            <div style={styles.terminalBody}>
              {loadingResult ? (
                <span style={styles.idleNote}>Loading result…</span>
              ) : result ? (
                <div style={styles.resultRow}>
                  <span style={styles.resultItem}>
                    <span style={styles.resultLabel}>Signal hit rate</span>
                    <span style={{ color: '#3fb950', fontWeight: 600 }}>
                      {fmtPct(result.signal_hit_rate)}
                    </span>
                    {result.signal_ci_low != null && result.signal_ci_high != null && (
                      <span style={styles.ci}>[{fmtPct(result.signal_ci_low)}–{fmtPct(result.signal_ci_high)}]</span>
                    )}
                    {result.signal_events != null && (
                      <span style={styles.eventCount}>n={result.signal_events}</span>
                    )}
                  </span>
                  <span style={styles.resultSep}>vs</span>
                  <span style={styles.resultItem}>
                    <span style={styles.resultLabel}>Null hit rate</span>
                    <span style={{ color: '#58a6ff', fontWeight: 600 }}>
                      {fmtPct(result.null_hit_rate)}
                    </span>
                    {result.null_ci_low != null && result.null_ci_high != null && (
                      <span style={styles.ci}>[{fmtPct(result.null_ci_low)}–{fmtPct(result.null_ci_high)}]</span>
                    )}
                    {result.null_events != null && (
                      <span style={styles.eventCount}>n={result.null_events}</span>
                    )}
                  </span>
                  {result.duration_secs != null && (
                    <span style={styles.resultDuration}>in {fmtDuration(result.duration_secs)}</span>
                  )}
                </div>
              ) : (
                <span style={styles.idleNote}>Run complete — no result data available.</span>
              )}
            </div>
          )}

          {/* Error */}
          {st === 'error' && (
            <div style={styles.terminalBody}>
              <span style={{ color: '#f85149', fontWeight: 600, marginRight: 8 }}>ERROR</span>
              <span style={{ color: '#e6edf3', fontSize: 12 }}>{status?.error ?? 'Unknown error'}</span>
              {status?.duration_secs != null && (
                <span style={styles.resultDuration}>after {fmtDuration(status.duration_secs)}</span>
              )}
            </div>
          )}

          {/* Cancelled */}
          {st === 'cancelled' && (
            <div style={styles.terminalBody}>
              <span style={{ color: '#f0883e', fontWeight: 600, marginRight: 8 }}>CANCELLED</span>
              {status?.duration_secs != null && (
                <span style={{ color: '#8b949e', fontSize: 12 }}>after {fmtDuration(status.duration_secs)}</span>
              )}
            </div>
          )}

          {/* Timeout */}
          {st === 'timeout' && (
            <div style={styles.terminalBody}>
              <span style={{ color: '#f0883e', fontWeight: 600, marginRight: 8 }}>TIMEOUT</span>
              {status?.duration_secs != null && (
                <span style={{ color: '#8b949e', fontSize: 12 }}>after {fmtDuration(status.duration_secs)}</span>
              )}
              {status?.error && (
                <span style={{ color: '#8b949e', fontSize: 12, marginLeft: 8 }}>{status.error}</span>
              )}
            </div>
          )}

        </div>
      )}
    </div>
  )
}

// --- Style helpers ---

function statusBadgeStyle(st: string): React.CSSProperties {
  switch (st) {
    case 'running': return { background: '#1a3a2a', color: '#3fb950', borderColor: '#2d6a3f' }
    case 'done': return { background: '#1a2d40', color: '#58a6ff', borderColor: '#2a4060' }
    case 'error': return { background: '#3a1a1a', color: '#f85149', borderColor: '#5a2020' }
    case 'cancelled': return { background: '#2d2210', color: '#f0883e', borderColor: '#5a3a10' }
    case 'timeout': return { background: '#2d2210', color: '#f0883e', borderColor: '#5a3a10' }
    case 'idle': return { background: '#161b22', color: '#8b949e', borderColor: '#30363d' }
    default: return { background: '#161b22', color: '#8b949e', borderColor: '#30363d' }
  }
}

const styles: Record<string, React.CSSProperties> = {
  section: {
    background: '#0d1117',
    borderBottom: '1px solid #30363d',
  },
  header: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 16px', borderBottom: '1px solid #21262d',
    cursor: 'default',
  },
  collapseBtn: {
    background: 'none', border: 'none', cursor: 'pointer',
    color: '#8b949e', fontSize: 13, padding: '0 2px', lineHeight: 1,
  },
  title: { fontSize: 13, fontWeight: 600, color: '#e6edf3' },
  statusBadge: {
    fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
    padding: '2px 7px', borderRadius: 4, border: '1px solid',
  },
  elapsed: {
    fontSize: 11, color: '#8b949e',
  },
  refreshBtn: {
    background: 'none', border: '1px solid #30363d', borderRadius: 4,
    color: '#8b949e', fontSize: 14, cursor: 'pointer', padding: '1px 7px',
  },
  body: {
    padding: '12px 16px',
  },
  runBody: {
    display: 'flex', flexDirection: 'column', gap: 10,
  },
  terminalBody: {
    display: 'flex', alignItems: 'center', flexWrap: 'wrap' as const, gap: 6,
  },
  idleNote: {
    fontSize: 12, color: '#484f58',
  },
  progressGroup: {
    display: 'flex', flexDirection: 'column', gap: 4,
  },
  progressLabel: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
  },
  progressLabelMain: {
    fontSize: 12, color: '#e6edf3', fontWeight: 600,
  },
  progressLabelVal: {
    fontSize: 12, color: '#e6edf3', fontWeight: 600,
  },
  progressLabelSecondary: {
    fontSize: 11, color: '#8b949e',
  },
  progressLabelValSecondary: {
    fontSize: 11, color: '#8b949e',
  },
  barTrack: {
    width: '100%', height: 8,
    background: '#161b22', borderRadius: 4,
    overflow: 'hidden', border: '1px solid #21262d',
  },
  barFill: {
    height: '100%', borderRadius: 4, transition: 'width 0.4s ease',
  },
  barFillSymbol: {
    background: '#238636',
  },
  barFillDate: {
    background: '#1c4a7a',
    height: '100%',
  },
  liveEvents: {
    display: 'flex', alignItems: 'center', gap: 6,
    fontSize: 11,
  },
  liveEventsLabel: {
    color: '#8b949e',
  },
  cancelRow: {
    display: 'flex', alignItems: 'center', gap: 6, paddingTop: 2,
  },
  cancelBtn: {
    fontSize: 11, padding: '3px 10px', borderRadius: 4,
    background: '#3a1a1a', color: '#f85149',
    border: '1px solid #5a2020', cursor: 'pointer',
  },
  confirmText: {
    fontSize: 12, color: '#e6edf3',
  },
  confirmBtn: {
    fontSize: 11, padding: '3px 10px', borderRadius: 4,
    background: '#5a1a1a', color: '#f85149',
    border: '1px solid #7a2020', cursor: 'pointer', fontWeight: 600,
  },
  cancelConfirmBtn: {
    fontSize: 11, padding: '3px 10px', borderRadius: 4,
    background: '#21262d', color: '#8b949e',
    border: '1px solid #30363d', cursor: 'pointer',
  },
  resultRow: {
    display: 'flex', alignItems: 'center', flexWrap: 'wrap' as const, gap: 8,
  },
  resultItem: {
    display: 'flex', alignItems: 'baseline', gap: 5, fontSize: 12,
  },
  resultLabel: {
    color: '#8b949e', fontSize: 11,
  },
  ci: {
    color: '#484f58', fontSize: 10,
  },
  eventCount: {
    color: '#484f58', fontSize: 10,
  },
  resultSep: {
    color: '#484f58', fontSize: 11,
  },
  resultDuration: {
    color: '#484f58', fontSize: 11, marginLeft: 4,
  },
}
