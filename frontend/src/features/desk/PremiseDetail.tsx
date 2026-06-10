import React, { useState, useEffect, useRef, useCallback } from 'react'
import type {
  PremiseFull,
  PremiseListItem,
  PremiseSpec,
  RunStatusResponse,
  VerdictResponse,
  VerdictPayload,
  Disposition,
  AutopsyResponse,
  AutopsySuggestion,
} from '../../api/premises'
import {
  getPremise,
  submitPremise,
  saveSpec,
  triggerRun,
  getRunStatus,
  getVerdict,
  graduateToConfirm,
  deletePremise,
  setDisposition,
  duplicatePremise,
  getAutopsy,
  derivePremise,
} from '../../api/premises'

interface PremiseDetailProps {
  premiseId: string
  allPremises?: PremiseListItem[]
  onDeleted: () => void
  onStatusChange: () => void
  onSelectId?: (id: string) => void
}

// H6: narrow e.target.value / loadPremise casts against the known set
const _KNOWN_DISPOSITIONS: readonly Disposition[] = [
  'active', 'parked_needs_data', 'parked_sharpen', 'rejected', 'promising',
]
function isDisposition(x: unknown): x is Disposition {
  return _KNOWN_DISPOSITIONS.includes(x as Disposition)
}

// KTS-08: parse FastAPI-standard 422 detail (array of {loc, msg, type}) into
// a readable multi-line string. Falls back to stringified detail for non-array shapes.
function formatErrorDetail(detail: unknown): string {
  if (Array.isArray(detail)) {
    return detail
      .map((item: unknown) => {
        if (item && typeof item === 'object') {
          const d = item as { loc?: unknown[]; msg?: unknown; type?: unknown }
          const field = Array.isArray(d.loc) ? d.loc.filter(s => s !== 'body').join('.') : ''
          const msg = typeof d.msg === 'string' ? d.msg : String(d.msg ?? '')
          return field ? `${field}: ${msg}` : msg
        }
        return String(item)
      })
      .join('\n')
  }
  if (detail == null) return 'Unknown error'
  return String(detail)
}

// H2: render verdict dict's display-safe scalar fields (not raw object)
function VerdictDisplay({ payload }: { payload: VerdictPayload | null | undefined }) {
  if (!payload) return null
  const rows: Array<{ label: string; value: string | number | boolean | null | undefined }> = [
    { label: 'Decision', value: payload.explore_decision },
    { label: 'Valid events', value: payload.n_valid_events },
    { label: 'MDE Q5/Q1 (pp)', value: typeof payload.mde_q5q1_pp === 'number' ? payload.mde_q5q1_pp.toFixed(2) : payload.mde_q5q1_pp },
    { label: 'MDE gate passed', value: payload.mde_gate_passed != null ? String(payload.mde_gate_passed) : null },
    { label: 'Run type', value: payload.run_type },
    { label: 'Note', value: payload.note },
  ]
  // Summarise H1/H2 if they exist (can be complex objects)
  if (payload.H1) rows.push({ label: 'H1 summary', value: typeof payload.H1 === 'object' ? JSON.stringify(payload.H1).slice(0, 200) : String(payload.H1) })
  if (payload.H2) rows.push({ label: 'H2 summary', value: typeof payload.H2 === 'object' ? JSON.stringify(payload.H2).slice(0, 200) : String(payload.H2) })
  const visible = rows.filter(r => r.value != null && r.value !== '')
  if (!visible.length) return null
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginBottom: 8 }}>
      <tbody>
        {visible.map(r => (
          <tr key={r.label} style={{ borderBottom: '1px solid #21262d' }}>
            <td style={{ padding: '4px 8px', color: '#8b949e', whiteSpace: 'nowrap', width: 140 }}>{r.label}</td>
            <td style={{ padding: '4px 8px', color: '#e6edf3', wordBreak: 'break-word' }}>{String(r.value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------
// F419: LineageStrip — walks derived_from chain client-side
// ---------------------------------------------------------------------------

function LineageStrip({
  premiseId,
  allPremises,
  onSelectId,
}: {
  premiseId: string
  allPremises: PremiseListItem[]
  onSelectId?: (id: string) => void
}) {
  // Walk backward: collect ancestors (oldest first)
  // KTS-07: track missing ancestor id so we can render a placeholder pill
  const ancestors: PremiseListItem[] = []
  let missingAncestorId: string | null = null
  const visited = new Set<string>()
  visited.add(premiseId)
  let current = allPremises.find(p => p.premise_id === premiseId)
  while (current?.derived_from && !visited.has(current.derived_from)) {
    const parent = allPremises.find(p => p.premise_id === current!.derived_from)
    if (!parent) {
      missingAncestorId = current.derived_from
      break
    }
    visited.add(parent.premise_id)
    ancestors.unshift(parent)
    current = parent
  }

  // Walk forward: direct children only
  const children = allPremises.filter(p => p.derived_from === premiseId)

  // If isolated (no lineage at all), render nothing
  if (ancestors.length === 0 && children.length === 0) return null

  const self = allPremises.find(p => p.premise_id === premiseId)

  const pillStyle = (isSelf: boolean, status: string): React.CSSProperties => ({
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    padding: '2px 8px',
    borderRadius: 12,
    fontSize: 11,
    fontFamily: 'monospace',
    border: isSelf
      ? `2px solid ${statusColor(status)}`
      : `1px solid ${statusColor(status)}55`,
    background: statusColor(status) + '18',
    color: isSelf ? statusColor(status) : '#8b949e',
    cursor: isSelf ? 'default' : 'pointer',
    whiteSpace: 'nowrap',
  })

  const renderNode = (p: PremiseListItem, isSelf: boolean) => (
    <span
      key={p.premise_id}
      style={pillStyle(isSelf, p.status)}
      onClick={isSelf || !onSelectId ? undefined : () => onSelectId(p.premise_id)}
      title={p.premise_text_excerpt}
    >
      {p.premise_id.slice(0, 12)}
      {isSelf && <span style={{ fontWeight: 700 }}>★</span>}
      <span
        style={{
          width: 6, height: 6, borderRadius: '50%',
          background: statusColor(p.status),
          display: 'inline-block',
        }}
      />
    </span>
  )

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      flexWrap: 'wrap',
      gap: 4,
      padding: '6px 0',
      fontSize: 11,
      color: '#8b949e',
    }}>
      {/* KTS-07: if oldest known ancestor has a missing parent, show a placeholder */}
      {missingAncestorId && (
        <React.Fragment>
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            padding: '2px 8px',
            borderRadius: 12,
            fontSize: 11,
            fontFamily: 'monospace',
            border: '1px solid #484f5855',
            background: '#484f5818',
            color: '#484f58',
            whiteSpace: 'nowrap',
          }}>
            {missingAncestorId.slice(0, 12)} (not loaded)
          </span>
          <span style={{ color: '#484f58' }}>→</span>
        </React.Fragment>
      )}
      {ancestors.map(p => (
        <React.Fragment key={p.premise_id}>
          {renderNode(p, false)}
          <span style={{ color: '#484f58' }}>→</span>
        </React.Fragment>
      ))}
      {self && renderNode(self, true)}
      {children.length > 0 && <span style={{ color: '#484f58' }}>→</span>}
      {children.map((p, i) => (
        <React.Fragment key={p.premise_id}>
          {renderNode(p, false)}
          {i < children.length - 1 && <span style={{ color: '#484f58' }}>/</span>}
        </React.Fragment>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// F417: VerdictAutopsy — inline component using autopsy API (contracts.md shape)
// ---------------------------------------------------------------------------

function SuggestionCard({
  suggestion,
  premiseId,
  censusMde,
  onSelectId,
}: {
  suggestion: AutopsySuggestion
  premiseId: string
  censusMde: number | null
  onSelectId?: (id: string) => void
}) {
  const [userMde, setUserMde] = useState<string>(
    censusMde != null ? String(censusMde) : ''
  )
  const [deriving, setDeriving] = useState(false)
  const [deriveError, setDeriveError] = useState<string | null>(null)

  const parsedMde = parseFloat(userMde)
  const powered =
    suggestion.predicted.mde_pp != null && !isNaN(parsedMde) && parsedMde > 0
      ? suggestion.predicted.mde_pp <= parsedMde
      : null

  const handleDerive = async () => {
    if (isNaN(parsedMde) || parsedMde <= 0) {
      setDeriveError('Enter a valid MDE target (pp) before creating.')
      return
    }
    setDeriving(true)
    setDeriveError(null)
    try {
      const overrides = { ...suggestion.spec_overrides, design_mde_pp: parsedMde }
      const resp = await derivePremise(premiseId, { spec_overrides: overrides })
      if (onSelectId) onSelectId(resp.premise_id)
    } catch (e: unknown) {
      const rawDetail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      const msg = rawDetail != null
        ? formatErrorDetail(rawDetail)
        : 'Failed to create derived premise'
      setDeriveError(msg)
    } finally {
      setDeriving(false)
    }
  }

  return (
    <div style={{
      border: '1px solid #30363d',
      borderRadius: 6,
      padding: '10px 14px',
      marginBottom: 10,
      background: '#161b22',
    }}>
      <div style={{ color: '#e6edf3', fontSize: 13, marginBottom: 6 }}>
        {suggestion.rationale}
      </div>
      <div style={{ fontSize: 12, color: '#8b949e', marginBottom: 6 }}>
        {suggestion.predicted.n_events != null && (
          <span style={{ marginRight: 12 }}>Projected events: <strong style={{ color: '#e6edf3' }}>{suggestion.predicted.n_events}</strong></span>
        )}
        {suggestion.predicted.mde_pp != null && (
          <span style={{ marginRight: 12 }}>Projected MDE: <strong style={{ color: '#e6edf3' }}>{suggestion.predicted.mde_pp.toFixed(2)} pp</strong></span>
        )}
        {suggestion.predicted.observed_pp != null && (
          <span>Current observed: <strong style={{ color: '#e6edf3' }}>{suggestion.predicted.observed_pp.toFixed(2)} pp</strong></span>
        )}
      </div>
      {/* Powered badge — client-side, keyed on user input */}
      {powered != null && (
        <div style={{
          display: 'inline-block',
          fontSize: 11,
          fontWeight: 700,
          padding: '2px 8px',
          borderRadius: 4,
          marginBottom: 8,
          background: powered ? '#238636' : '#6e402a',
          color: powered ? '#e6edf3' : '#f0883e',
        }}>
          {powered ? 'Powered at your MDE target' : 'Under-powered at your MDE target'}
        </div>
      )}
      {/* C-02: predicted_testable badge */}
      {suggestion.predicted_testable != null && (
        <div style={{
          display: 'inline-block',
          fontSize: 11,
          fontWeight: 700,
          padding: '2px 8px',
          borderRadius: 4,
          marginBottom: 8,
          marginRight: 6,
          background: suggestion.predicted_testable ? '#1f6feb22' : '#8b949e22',
          color: suggestion.predicted_testable ? '#58a6ff' : '#8b949e',
          border: `1px solid ${suggestion.predicted_testable ? '#1f6feb44' : '#8b949e44'}`,
        }}>
          {suggestion.predicted_testable ? 'Expected: testable' : 'Expected: still borderline'}
        </div>
      )}
      {/* Caveat — always visible, never tooltip */}
      <div style={{
        fontSize: 11,
        color: '#f0883e',
        background: '#f0883e18',
        border: '1px solid #f0883e44',
        borderRadius: 4,
        padding: '4px 8px',
        marginBottom: 8,
      }}>
        {suggestion.caveat}
      </div>
      {/* MDE input — only shown for actionable suggestions */}
      {suggestion.actionable !== false && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <label style={{ fontSize: 12, color: '#8b949e', whiteSpace: 'nowrap' }}>
            Your MDE target (pp):
          </label>
          <input
            type="number"
            min={0}
            step={0.5}
            value={userMde}
            onChange={e => setUserMde(e.target.value)}
            style={{
              width: 80,
              padding: '3px 6px',
              fontSize: 12,
              background: '#0d1117',
              border: '1px solid #30363d',
              borderRadius: 4,
              color: '#e6edf3',
            }}
            placeholder="e.g. 8.0"
          />
          {censusMde != null && userMde === String(censusMde) && (
            <span style={{ fontSize: 10, color: '#8b949e' }}>pre-filled from current spec — adjust</span>
          )}
        </div>
      )}
      {deriveError && (
        <div style={{ fontSize: 12, color: '#f85149', marginBottom: 6, whiteSpace: 'pre-wrap' }}>{deriveError}</div>
      )}
      {/* actionable !== false: show Create button; otherwise advice-only card */}
      {suggestion.actionable !== false ? (
        <button
          onClick={handleDerive}
          disabled={deriving}
          style={{
            padding: '4px 12px',
            fontSize: 12,
            background: '#1f6feb',
            color: '#e6edf3',
            border: 'none',
            borderRadius: 4,
            cursor: deriving ? 'not-allowed' : 'pointer',
            opacity: deriving ? 0.7 : 1,
          }}
        >
          {deriving ? 'Creating…' : 'Create derived premise'}
        </button>
      ) : (
        <div style={{ fontSize: 11, color: '#484f58', fontStyle: 'italic' }}>
          Advice only — no derived premise can be created for this suggestion.
        </div>
      )}
    </div>
  )
}

function VerdictAutopsy({
  premiseId,
  onSelectId,
}: {
  premiseId: string
  onSelectId?: (id: string) => void
}) {
  const [autopsy, setAutopsy] = useState<AutopsyResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setAutopsy(null)
    getAutopsy(premiseId)
      .then(data => { if (!cancelled) { setAutopsy(data); setLoading(false) } })
      .catch((e: unknown) => {
        if (cancelled) return
        // 404 = autopsy not available yet — render nothing
        const status = (e as { response?: { status?: number } })?.response?.status
        if (status === 404) {
          setAutopsy(null)
          setLoading(false)
          return
        }
        const msg = (e as { response?: { data?: { detail?: string } }; message?: string })
          ?.response?.data?.detail ?? (e as { message?: string })?.message ?? 'Failed to load autopsy'
        setError(msg)
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [premiseId])

  if (loading) {
    return (
      <div style={{ padding: '8px 0', color: '#8b949e', fontSize: 12 }}>Loading autopsy…</div>
    )
  }
  if (error) {
    return (
      <div style={{ padding: '8px 0', color: '#f85149', fontSize: 12 }}>Autopsy error: {error}</div>
    )
  }
  if (!autopsy) return null

  const census = autopsy.census

  return (
    <div style={{
      border: '1px solid #21262d',
      borderRadius: 6,
      padding: '12px 16px',
      marginTop: 8,
      background: '#0d1117',
    }}>
      <div style={{ fontWeight: 700, fontSize: 13, color: '#f0883e', marginBottom: 10 }}>
        Autopsy — Why this test couldn't be answered
      </div>

      {/* Plain summary */}
      {autopsy.plain_summary && (
        <div style={{ fontSize: 13, color: '#e6edf3', marginBottom: 12, lineHeight: 1.5 }}>
          {autopsy.plain_summary}
        </div>
      )}

      {/* Why it failed */}
      {(autopsy.failed_gate || autopsy.failed_gate_detail) && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 600, fontSize: 12, color: '#8b949e', marginBottom: 6 }}>
            Why it failed
          </div>
          <div style={{
            background: '#161b22',
            border: '1px solid #30363d',
            borderRadius: 4,
            padding: '8px 12px',
            fontSize: 12,
          }}>
            {autopsy.failed_gate && (
              <span style={{ fontFamily: 'monospace', color: '#f0883e', marginRight: 8 }}>
                {autopsy.failed_gate}
              </span>
            )}
            {autopsy.failed_gate_detail && (
              <span style={{ color: '#e6edf3' }}>{autopsy.failed_gate_detail}</span>
            )}
          </div>
        </div>
      )}

      {/* Census numbers */}
      {census && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontWeight: 600, fontSize: 12, color: '#8b949e', marginBottom: 6 }}>
            What the numbers show
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <tbody>
              {[
                ['Events found', census.n_valid != null ? String(census.n_valid) : '—'],
                ['Scoreable events', census.n_score_defined != null ? String(census.n_score_defined) : '—'],
                ['Primary horizon (days)', census.primary_horizon != null ? String(census.primary_horizon) : '—'],
                ['Analysis form', census.analysis_form ?? '—'],
                ['Design MDE target (pp)', census.design_mde_pp != null ? `${census.design_mde_pp.toFixed(1)} pp` : '—'],
                ...(census.note ? [['Note', census.note]] : []),
              ].map(([label, value]) => (
                <tr key={label} style={{ borderBottom: '1px solid #21262d' }}>
                  <td style={{ padding: '4px 8px', color: '#8b949e', whiteSpace: 'nowrap', width: 180 }}>{label}</td>
                  <td style={{ padding: '4px 8px', color: '#e6edf3' }}>{value}</td>
                </tr>
              ))}
              {/* Per-horizon MDE table if available */}
              {census.horizons && Object.entries(census.horizons).map(([horizon, h]) => (
                <tr key={`h-${horizon}`} style={{ borderBottom: '1px solid #21262d' }}>
                  <td style={{ padding: '4px 8px', color: '#8b949e', whiteSpace: 'nowrap' }}>
                    {horizon}d MDE (1-samp)
                  </td>
                  <td style={{ padding: '4px 8px', color: '#e6edf3' }}>
                    {h.mde_1samp_pp != null ? `${h.mde_1samp_pp.toFixed(2)} pp` : '—'}
                    {h.arm_size != null ? ` (arm_size=${h.arm_size})` : ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Suggestions */}
      {autopsy.suggestions && autopsy.suggestions.length > 0 && (
        <div>
          <div style={{ fontWeight: 600, fontSize: 12, color: '#8b949e', marginBottom: 8 }}>
            Suggested next steps
          </div>
          {autopsy.suggestions.map((s, i) => (
            <SuggestionCard
              key={i}
              suggestion={s}
              premiseId={premiseId}
              censusMde={census?.design_mde_pp ?? null}
              onSelectId={onSelectId}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// End F417 / F419 helpers
// ---------------------------------------------------------------------------

const DEFAULT_SPEC_SKELETON = (premiseText: string): PremiseSpec => ({
  premise_text: premiseText,
  guided: null,
  plain_summary: null,
  stream: 'form4',
  event_filter: { transaction_codes: ['P'] },
  dose: 'r1_score',
  dose_params: {},
  horizons: [21, 63, 126],
  entry_lag_days: 1,
  dedup_same_ticker: true,
  dedup_window_days: 30,
  direction: 'long',
  floors: { min_price: 5.0, min_avg_volume: 500000 },
  min_peer_count: 8,
  fdr_q: 0.10,
  n_boot: 999,
})

function statusLabel(status: string): string {
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

function statusColor(status: string): string {
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

function formatTs(ts: string | null | undefined): string {
  if (!ts) return '—'
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return ts
  }
}

export default function PremiseDetail({ premiseId, allPremises = [], onDeleted, onStatusChange, onSelectId }: PremiseDetailProps) {
  const [premise, setPremise] = useState<PremiseFull | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Spec fold
  const [specFoldOpen, setSpecFoldOpen] = useState(false)
  const [specEditOpen, setSpecEditOpen] = useState(false)
  const [specEditJson, setSpecEditJson] = useState('')
  const [specEditError, setSpecEditError] = useState<string | null>(null)
  const [specSaving, setSpecSaving] = useState(false)

  // Submit (draft → awaiting_formalization)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  // Run
  const [runStatus, setRunStatus] = useState<RunStatusResponse | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [runningMode, setRunningMode] = useState<'preview' | 'explore' | null>(null)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Verdict
  const [verdict, setVerdict] = useState<VerdictResponse | null>(null)
  const [verdictError, setVerdictError] = useState<string | null>(null)

  // Graduate gate
  const [graduateConfirmPending, setGraduateConfirmPending] = useState(false)
  const [graduating, setGraduating] = useState(false)
  const [graduateError, setGraduateError] = useState<string | null>(null)

  // Delete gate
  const [deleteConfirmPending, setDeleteConfirmPending] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  // F397: disposition block
  const [dispositionValue, setDispositionValue] = useState<Disposition>('active')
  const [dispositionNote, setDispositionNote] = useState('')
  const [dispositionSaving, setDispositionSaving] = useState(false)
  const [dispositionError, setDispositionError] = useState<string | null>(null)
  const [dispositionSaved, setDispositionSaved] = useState(false)

  // F397: duplicate
  const [duplicating, setDuplicating] = useState(false)
  const [duplicateError, setDuplicateError] = useState<string | null>(null)

  const loadPremise = useCallback(async () => {
    try {
      const p = await getPremise(premiseId)
      setPremise(p)
      setError(null)
      // F397: sync disposition state from premise (H6: narrow via guard)
      setDispositionValue(isDisposition(p.disposition) ? p.disposition : 'active')
      setDispositionNote(p.disposition_note ?? '')
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } }; message?: string })
        ?.response?.data?.detail ?? (e as { message?: string })?.message ?? 'Failed to load premise'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [premiseId])

  // Initial load
  useEffect(() => {
    setLoading(true)
    setError(null)
    setSpecFoldOpen(false)
    setSpecEditOpen(false)
    setRunStatus(null)
    setVerdict(null)
    setVerdictError(null)
    setGraduateConfirmPending(false)
    setDeleteConfirmPending(false)
    setRunError(null)
    // F397: reset disposition state on id change
    setDispositionSaved(false)
    setDispositionError(null)
    setDuplicateError(null)
    loadPremise()
  }, [premiseId, loadPremise])

  // Load verdict when premise is in explored state or has run history
  // H11: surface errors instead of swallowing them
  useEffect(() => {
    if (!premise) return
    if (premise.status === 'explored' || premise.status === 'awaiting_confirm' || premise.status === 'confirmed') {
      setVerdictError(null)
      getVerdict(premiseId).then(setVerdict).catch((e: unknown) => {
        const msg = (e as { response?: { data?: { detail?: string } }; message?: string })
          ?.response?.data?.detail ?? (e as { message?: string })?.message ?? 'Failed to load verdict'
        setVerdictError(msg)
        console.error('Failed to load verdict for', premiseId, e)
      })
    }
  }, [premise?.status, premiseId])

  // Polling while exploring
  useEffect(() => {
    if (premise?.status === 'exploring') {
      pollIntervalRef.current = setInterval(async () => {
        try {
          const rs = await getRunStatus(premiseId)
          setRunStatus(rs)
          if (rs.status !== 'running') {
            if (pollIntervalRef.current) {
              clearInterval(pollIntervalRef.current)
              pollIntervalRef.current = null
            }
            setRunningMode(null)
            loadPremise()
          }
        } catch {
          // Network hiccup — keep polling
        }
      }, 3000)
    }
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current)
        pollIntervalRef.current = null
      }
    }
  }, [premise?.status, premiseId, loadPremise])

  // --------------------------------------------------
  // Handlers
  // --------------------------------------------------

  const handleSubmit = async () => {
    setSubmitting(true)
    setSubmitError(null)
    try {
      await submitPremise(premiseId)
      await loadPremise()
      onStatusChange()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to submit premise'
      setSubmitError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  const handleOpenSpecEdit = () => {
    const json = premise?.spec
      ? JSON.stringify(premise.spec, null, 2)
      : JSON.stringify(DEFAULT_SPEC_SKELETON(premise?.premise_text ?? ''), null, 2)
    setSpecEditJson(json)
    setSpecEditError(null)
    setSpecEditOpen(true)
  }

  const handleSaveSpec = async () => {
    setSpecEditError(null)
    let parsed: PremiseSpec
    try {
      parsed = JSON.parse(specEditJson) as PremiseSpec
    } catch {
      setSpecEditError('Invalid JSON — fix the syntax and try again.')
      return
    }
    setSpecSaving(true)
    try {
      await saveSpec(premiseId, parsed)
      setSpecEditOpen(false)
      await loadPremise()
      onStatusChange()
    } catch (e: unknown) {
      const rawDetail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      const detail = rawDetail != null
        ? formatErrorDetail(rawDetail)
        : 'Failed to save spec'
      setSpecEditError(detail)
    } finally {
      setSpecSaving(false)
    }
  }

  const handleRun = async (mode: 'preview' | 'explore') => {
    setRunError(null)
    setRunStatus(null)
    setRunningMode(mode)
    try {
      await triggerRun(premiseId, { mode })
      await loadPremise()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to trigger run'
      setRunError(msg)
      setRunningMode(null)
    }
  }

  const handleGraduate = async () => {
    setGraduating(true)
    setGraduateError(null)
    try {
      await graduateToConfirm(premiseId)
      setGraduateConfirmPending(false)
      await loadPremise()
      onStatusChange()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to graduate premise'
      setGraduateError(msg)
    } finally {
      setGraduating(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    setDeleteError(null)
    try {
      await deletePremise(premiseId)
      onDeleted()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to delete premise'
      setDeleteError(msg)
    } finally {
      setDeleting(false)
    }
  }

  // F397: disposition save
  const handleSaveDisposition = async () => {
    setDispositionSaving(true)
    setDispositionError(null)
    setDispositionSaved(false)
    try {
      await setDisposition(premiseId, { disposition: dispositionValue, note: dispositionNote })
      setDispositionSaved(true)
      await loadPremise()
      onStatusChange()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to save disposition'
      setDispositionError(msg)
    } finally {
      setDispositionSaving(false)
    }
  }

  // F397: duplicate premise
  const handleDuplicate = async () => {
    setDuplicating(true)
    setDuplicateError(null)
    try {
      const resp = await duplicatePremise(premiseId)
      onStatusChange()
      if (onSelectId) {
        onSelectId(resp.premise_id)
      }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        ?? 'Failed to duplicate premise'
      setDuplicateError(msg)
    } finally {
      setDuplicating(false)
    }
  }

  // --------------------------------------------------
  // Render helpers
  // --------------------------------------------------

  if (loading) {
    return <div style={styles.loadingState}>Loading…</div>
  }

  if (error) {
    return (
      <div style={styles.errorState}>
        <span style={{ color: '#f85149' }}>{error}</span>
        <button onClick={loadPremise} style={styles.refreshBtn}>↻ Retry</button>
      </div>
    )
  }

  if (!premise) return null

  const status = premise.status
  const canRun = status === 'spec_ready' || status === 'explored'
  const isFrozen = status === 'awaiting_confirm' || status === 'confirmed'
  const isRunning = status === 'exploring'
  const canEditSpec = !isFrozen && !isRunning
  const canDelete = status !== 'confirmed' && !isRunning
  const canGraduate = status === 'explored'

  const runDisabledReason: string | null = (() => {
    if (!premise.spec) return 'No spec yet'
    if (status === 'draft' || status === 'awaiting_formalization') return 'Waiting for AI to formalize spec'
    if (isRunning) return 'Run in progress…'
    if (isFrozen) return 'Spec frozen'
    return null
  })()

  // Latest run verdict — only RunHistoryEntry items have a verdict field
  // run_history may contain confirm_request entries (no verdict) — filter them out
  const latestRun = (() => {
    const entries = (premise.run_history ?? []).filter(
      r => typeof (r as { run_type?: unknown }).run_type === 'string' && 'verdict' in r
    )
    return entries.length ? entries[entries.length - 1] as (typeof entries[number] & { verdict?: VerdictPayload | null; verdict_valid?: boolean }) : null
  })()

  return (
    <div style={styles.container}>
      {/* Refresh */}
      <div style={styles.refreshRow}>
        <button onClick={loadPremise} style={styles.refreshBtn} title="Reload premise">↻ Refresh</button>
      </div>

      {/* Section 1 — Header */}
      <div style={styles.section}>
        {/* F397: derived_from backlink */}
        {premise.derived_from && (
          <div style={styles.derivedFromBanner}>
            ↳ sharper version of{' '}
            <button
              style={{ ...styles.derivedFromLink, ...(onSelectId ? {} : { opacity: 0.4, cursor: 'default' }) }}
              disabled={!onSelectId}
              onClick={() => onSelectId && onSelectId(premise.derived_from!)}
            >
              {premise.derived_from}
            </button>
          </div>
        )}
        {/* F419: lineage strip — shows full ancestor/descendant chain */}
        {allPremises.length > 0 && (
          <LineageStrip
            premiseId={premiseId}
            allPremises={allPremises}
            onSelectId={onSelectId}
          />
        )}
        <div style={styles.headerRow}>
          <span
            style={{
              ...styles.statusChip,
              background: statusColor(status) + '22',
              color: statusColor(status),
              border: `1px solid ${statusColor(status)}55`,
            }}
          >
            {isRunning && <span style={styles.pulsingDot} />}
            {statusLabel(status)}
          </span>
          <span style={styles.premiseIdLabel}>{premise.premise_id}</span>
        </div>
        <div style={styles.premiseText}>{premise.premise_text}</div>
        <div style={styles.timestamps}>
          Created: {formatTs(premise.created_at)} · Updated: {formatTs(premise.updated_at)}
        </div>
        {premise.error_note && premise.error_note !== 'soft-deleted' && (
          <div style={styles.errorBanner}>Last run failed: {premise.error_note}</div>
        )}
      </div>

      {/* Submit button (draft only) */}
      {status === 'draft' && (
        <div style={styles.section}>
          <div style={styles.sectionTitle}>Submit for formalization</div>
          <div style={styles.mutedNote}>
            Send this idea to the agent-operator queue. The AI will read it and produce a testable spec.
          </div>
          {submitError && <div style={styles.inlineError}>{submitError}</div>}
          <button
            onClick={handleSubmit}
            disabled={submitting}
            style={styles.primaryBtn}
          >
            {submitting ? 'Submitting…' : 'Submit for formalization'}
          </button>
        </div>
      )}

      {/* Awaiting formalization note */}
      {status === 'awaiting_formalization' && (
        <div style={styles.section}>
          <div style={styles.mutedNote}>
            Waiting for the agent-operator to formalize this premise into a testable spec.
            You can also enter the spec manually below.
          </div>
        </div>
      )}

      {/* Section 2 — Plain-English readback */}
      <div style={styles.section}>
        <div style={styles.sectionTitle}>What this test will measure</div>
        {premise.spec?.plain_summary ? (
          <div style={styles.readback}>{premise.spec.plain_summary}</div>
        ) : (
          <div style={styles.mutedNote}>[No readback yet — AI will fill this during formalization]</div>
        )}
        {/* F419: analysis_form / design_mde_pp — one_sample only */}
        {premise.spec?.analysis_form === 'one_sample' && (
          <div style={{ ...styles.timestamps, marginTop: 6, display: 'flex', gap: 16 }}>
            <span>
              <span style={{ color: '#8b949e' }}>Analysis form: </span>
              one-sample direction test
            </span>
            <span>
              <span style={{ color: '#8b949e' }}>MDE target: </span>
              {premise.spec.design_mde_pp != null
                ? `${premise.spec.design_mde_pp.toFixed(1)} pp`
                : '—'}
            </span>
          </div>
        )}
      </div>

      {/* Section 3 — Spec fold */}
      <div style={styles.section}>
        <button
          onClick={() => setSpecFoldOpen(o => !o)}
          style={styles.foldToggle}
        >
          {specFoldOpen ? '▾ Hide runnable details' : '▸ Show runnable details'}
        </button>
        {specFoldOpen && (
          <div style={styles.specFoldContent}>
            {premise.spec ? (
              <pre style={styles.specPre}>{JSON.stringify(premise.spec, null, 2)}</pre>
            ) : (
              <div style={styles.mutedNote}>No spec attached yet.</div>
            )}
            {canEditSpec && !specEditOpen && (
              <button onClick={handleOpenSpecEdit} style={styles.secondaryBtn}>
                Edit spec manually
              </button>
            )}
            {specEditOpen && (
              <div style={styles.specEditor}>
                <div style={styles.specEditorLabel}>
                  Paste a full PremiseSpec JSON. The backend validates all fields.
                </div>
                <textarea
                  style={styles.specTextarea}
                  value={specEditJson}
                  onChange={e => setSpecEditJson(e.target.value)}
                  rows={20}
                  spellCheck={false}
                />
                {specEditError && <div style={styles.inlineError}>{specEditError}</div>}
                <div style={styles.specEditorActions}>
                  <button
                    onClick={handleSaveSpec}
                    disabled={specSaving}
                    style={styles.primaryBtn}
                  >
                    {specSaving ? 'Saving…' : 'Save spec'}
                  </button>
                  <button
                    onClick={() => { setSpecEditOpen(false); setSpecEditError(null) }}
                    style={styles.secondaryBtn}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Section 4 — Run controls */}
      <div style={styles.section}>
        <div style={styles.sectionTitle}>Run</div>
        {runError && <div style={styles.inlineError}>{runError}</div>}
        <div style={styles.runButtons}>
          <div style={styles.runButtonGroup}>
            <button
              onClick={() => handleRun('preview')}
              disabled={!canRun || !!runDisabledReason}
              style={canRun && !runDisabledReason ? styles.primaryBtn : styles.disabledBtn}
              title={runDisabledReason ?? undefined}
            >
              Run fast preview
            </button>
            <div style={styles.runModeLabel}>(preview — not a verdict)</div>
          </div>
          <div style={styles.runButtonGroup}>
            <button
              onClick={() => handleRun('explore')}
              disabled={!canRun || !!runDisabledReason}
              style={canRun && !runDisabledReason ? styles.primaryBtn : styles.disabledBtn}
              title={runDisabledReason ?? undefined}
            >
              Run full explore
            </button>
            <div style={styles.runModeLabel}>(full explore — dispatches to research worker)</div>
          </div>
        </div>
        {/* H9: show disabled reason whenever it is set, not only when !canRun */}
        {runDisabledReason && (
          <div style={styles.disabledReason}>{runDisabledReason}</div>
        )}
      </div>

      {/* Section 5 — Run status */}
      {(isRunning || runStatus !== null) && (
        <div style={styles.section}>
          <div style={styles.sectionTitle}>Run status</div>
          {isRunning && runStatus === null && (
            <div style={runStatusBadge('running')}>RUNNING{runningMode ? ` (${runningMode})` : ''}</div>
          )}
          {runStatus && (
            <div>
              <span style={runStatusBadge(runStatus.status)}>
                {runStatus.status.toUpperCase()}
                {runStatus.run_type ? ` (${runStatus.run_type})` : ''}
              </span>
              {runStatus.error && (
                <div style={styles.inlineError}>Error: {runStatus.error}</div>
              )}
              {runStatus.finished_at && (
                <div style={styles.timestamps}>Finished: {formatTs(runStatus.finished_at)}</div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Section 6 — Verdict */}
      {(verdict?.verdict || latestRun?.verdict) && (
        <div style={styles.section}>
          <div style={styles.sectionTitle}>What the test found</div>
          {/* H8: deduplicated preview warning — show once based on most specific source */}
          {(verdict ? !verdict.verdict_valid : latestRun && !latestRun.verdict_valid) && (
            <span style={styles.previewWarning}>Preview result — not a formal verdict</span>
          )}
          {/* H11: surface verdict load errors */}
          {verdictError && (
            <div style={styles.inlineError}>Could not load verdict: {verdictError}</div>
          )}
          {/* H2: render VerdictPayload fields readably (not raw "[object Object]") */}
          <VerdictDisplay payload={verdict?.verdict ?? latestRun?.verdict ?? null} />
          <div style={styles.mutedNote}>
            Explore result — hypothesis only, not confirmed. Re-run with updated spec to iterate.
          </div>
          {((premise.run_history ?? []).filter(r => 'run_type' in r).length) > 1 && (
            <div style={styles.mutedNote}>
              {(premise.run_history ?? []).filter(r => 'run_type' in r).length} total runs in history.
            </div>
          )}
        </div>
      )}

      {/* Section 6b — Verdict Autopsy (F417): shown when latest explore run is UNTESTABLE or NOT-SUPPORTED */}
      {(() => {
        const exploreDecision =
          verdict?.verdict?.explore_decision ?? latestRun?.verdict?.explore_decision
        const showAutopsy = typeof exploreDecision === 'string' &&
          (exploreDecision.includes('UNTESTABLE') || exploreDecision === 'NOT-SUPPORTED' || exploreDecision.includes('WEAKENED'))
        return showAutopsy ? (
          <div style={styles.section}>
            <VerdictAutopsy premiseId={premiseId} onSelectId={onSelectId} />
          </div>
        ) : null
      })()}

      {/* Section 7 — Graduate-to-confirm gate */}
      {canGraduate && (
        <div style={{ ...styles.section, ...styles.graduateSection }}>
          <div style={styles.sectionTitle}>Promote to Confirmation Track</div>
          <div style={styles.graduateWarning}>
            This freezes the spec permanently and runs a power check. Once frozen, the spec
            cannot be changed. This cannot be undone.
            <br /><br />
            This does <strong>not</strong> run the out-of-sample confirmation.
            Freezing queues the premise for OOS confirm — the real confirm run (which writes
            the FDR multiplicity ledger) is a separate gated step (F393).
          </div>
          {graduateError && <div style={styles.inlineError}>{graduateError}</div>}
          {!graduateConfirmPending ? (
            <button
              onClick={() => setGraduateConfirmPending(true)}
              style={styles.warningBtn}
            >
              Promote to confirm track
            </button>
          ) : (
            <div style={styles.confirmGate}>
              <div style={{ color: '#f0883e', fontSize: 12, marginBottom: 8 }}>
                Are you sure? This freezes the spec forever.
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={handleGraduate}
                  disabled={graduating}
                  style={styles.dangerBtn}
                >
                  {graduating ? 'Freezing…' : 'Yes, freeze and queue'}
                </button>
                <button
                  onClick={() => setGraduateConfirmPending(false)}
                  style={styles.secondaryBtn}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Section 8 — Disposition (F397) */}
      <div style={styles.section}>
        <div style={styles.sectionTitle}>Disposition</div>
        <div style={styles.dispositionRow}>
          <select
            value={dispositionValue}
            onChange={e => { if (isDisposition(e.target.value)) { setDispositionValue(e.target.value); setDispositionSaved(false) } }}
            style={styles.dispositionSelect}
          >
            <option value="active">Active</option>
            <option value="parked_needs_data">Parked — needs data</option>
            <option value="parked_sharpen">Parked — sharpen</option>
            <option value="rejected">Rejected</option>
            <option value="promising">Promising</option>
          </select>
        </div>
        <textarea
          style={styles.dispositionTextarea}
          placeholder="Optional note (why parked, what to try next, …)"
          value={dispositionNote}
          onChange={e => { setDispositionNote(e.target.value); setDispositionSaved(false) }}
          rows={3}
        />
        {dispositionError && <div style={styles.inlineError}>{dispositionError}</div>}
        <div style={styles.dispositionActions}>
          <button
            onClick={handleSaveDisposition}
            disabled={dispositionSaving}
            style={styles.primaryBtn}
          >
            {dispositionSaving ? 'Saving…' : 'Save disposition'}
          </button>
          {dispositionSaved && <span style={styles.savedNote}>Saved</span>}
        </div>
      </div>

      {/* Section 9 — Duplicate (F397) */}
      <div style={styles.section}>
        <div style={styles.sectionTitle}>Duplicate</div>
        <div style={styles.mutedNote}>
          Creates a new draft copying this premise text and spec. Use to sharpen the idea with a new test.
        </div>
        {duplicateError && <div style={styles.inlineError}>{duplicateError}</div>}
        <button
          onClick={handleDuplicate}
          disabled={duplicating}
          style={styles.secondaryBtn}
        >
          {duplicating ? 'Duplicating…' : 'Duplicate as new premise'}
        </button>
      </div>

      {/* Section 10 — Delete */}
      {canDelete && (
        <div style={styles.section}>
          {deleteError && <div style={styles.inlineError}>{deleteError}</div>}
          {!deleteConfirmPending ? (
            <button
              onClick={() => setDeleteConfirmPending(true)}
              style={styles.deleteBtn}
            >
              Delete premise
            </button>
          ) : (
            <div style={styles.confirmGate}>
              <div style={{ color: '#f85149', fontSize: 12, marginBottom: 8 }}>
                Delete this premise? (spec and history will be cleared)
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  style={styles.dangerBtn}
                >
                  {deleting ? 'Deleting…' : 'Yes, delete'}
                </button>
                <button
                  onClick={() => setDeleteConfirmPending(false)}
                  style={styles.secondaryBtn}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

function runStatusBadge(status: string): React.CSSProperties {
  const colors: Record<string, string> = {
    running: '#3fb950',
    done: '#58a6ff',
    error: '#f85149',
    // H1: 'failed' is written by the backend in 5 failure paths
    failed: '#f85149',
    not_found: '#484f58',
    unknown: '#f0883e',
  }
  const c = colors[status] ?? '#8b949e'
  return {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    fontSize: 11,
    fontWeight: 700,
    padding: '2px 8px',
    borderRadius: 4,
    background: c + '22',
    color: c,
    border: `1px solid ${c}55`,
    letterSpacing: '0.04em',
  }
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflowY: 'auto',
    padding: '0 0 32px',
    color: '#e6edf3',
    fontSize: 13,
  },
  refreshRow: {
    display: 'flex',
    justifyContent: 'flex-end',
    padding: '8px 16px 0',
  },
  section: {
    padding: '16px 16px',
    borderBottom: '1px solid #21262d',
  },
  sectionTitle: {
    fontSize: 11,
    fontWeight: 700,
    color: '#8b949e',
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
    marginBottom: 8,
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginBottom: 10,
  },
  statusChip: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 11,
    fontWeight: 700,
    padding: '3px 10px',
    borderRadius: 100,
    letterSpacing: '0.04em',
  },
  pulsingDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: '#3fb950',
    animation: 'pulse 1.4s ease-in-out infinite',
    flexShrink: 0,
  },
  premiseIdLabel: {
    fontSize: 11,
    color: '#484f58',
    fontFamily: 'monospace',
  },
  premiseText: {
    fontSize: 14,
    color: '#e6edf3',
    lineHeight: 1.6,
    marginBottom: 8,
    whiteSpace: 'pre-wrap',
  },
  timestamps: {
    fontSize: 11,
    color: '#484f58',
    marginTop: 4,
  },
  errorBanner: {
    marginTop: 8,
    padding: '6px 10px',
    background: '#f8514922',
    border: '1px solid #f8514944',
    borderRadius: 4,
    color: '#f85149',
    fontSize: 12,
  },
  readback: {
    fontSize: 13,
    color: '#e6edf3',
    lineHeight: 1.7,
    padding: '8px 12px',
    background: '#161b22',
    border: '1px solid #21262d',
    borderRadius: 6,
  },
  mutedNote: {
    fontSize: 12,
    color: '#484f58',
    lineHeight: 1.6,
  },
  foldToggle: {
    background: 'none',
    border: 'none',
    color: '#58a6ff',
    fontSize: 12,
    cursor: 'pointer',
    padding: 0,
    fontWeight: 600,
  },
  specFoldContent: {
    marginTop: 10,
  },
  specPre: {
    background: '#0d1117',
    border: '1px solid #21262d',
    borderRadius: 6,
    padding: '10px 12px',
    fontSize: 11,
    color: '#8b949e',
    overflowX: 'auto',
    fontFamily: 'monospace',
    lineHeight: 1.5,
    maxHeight: 300,
    overflowY: 'auto',
  },
  specEditor: {
    marginTop: 10,
  },
  specEditorLabel: {
    fontSize: 11,
    color: '#484f58',
    marginBottom: 6,
  },
  specTextarea: {
    width: '100%',
    background: '#0d1117',
    border: '1px solid #30363d',
    borderRadius: 6,
    color: '#e6edf3',
    fontFamily: 'monospace',
    fontSize: 11,
    padding: '8px 10px',
    lineHeight: 1.5,
    resize: 'vertical',
    boxSizing: 'border-box',
  },
  specEditorActions: {
    display: 'flex',
    gap: 8,
    marginTop: 8,
  },
  runButtons: {
    display: 'flex',
    gap: 16,
    flexWrap: 'wrap',
  },
  runButtonGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  runModeLabel: {
    fontSize: 11,
    color: '#484f58',
  },
  disabledReason: {
    marginTop: 8,
    fontSize: 12,
    color: '#484f58',
  },
  previewWarning: {
    display: 'inline-block',
    marginBottom: 8,
    fontSize: 11,
    fontWeight: 600,
    padding: '2px 8px',
    borderRadius: 4,
    background: '#f0883e22',
    color: '#f0883e',
    border: '1px solid #f0883e44',
  },
  verdictText: {
    fontSize: 13,
    color: '#e6edf3',
    lineHeight: 1.7,
    padding: '8px 12px',
    background: '#161b22',
    border: '1px solid #21262d',
    borderRadius: 6,
    marginBottom: 8,
    whiteSpace: 'pre-wrap',
  },
  graduateSection: {
    background: '#161b22',
  },
  graduateWarning: {
    fontSize: 12,
    color: '#8b949e',
    lineHeight: 1.7,
    marginBottom: 12,
  },
  confirmGate: {
    padding: '10px 12px',
    background: '#21262d',
    border: '1px solid #30363d',
    borderRadius: 6,
  },
  primaryBtn: {
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 14px',
    background: '#1f6feb',
    color: '#e6edf3',
    border: 'none',
    borderRadius: 6,
    cursor: 'pointer',
  },
  secondaryBtn: {
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 14px',
    background: '#21262d',
    color: '#8b949e',
    border: '1px solid #30363d',
    borderRadius: 6,
    cursor: 'pointer',
  },
  disabledBtn: {
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 14px',
    background: '#21262d',
    color: '#484f58',
    border: '1px solid #21262d',
    borderRadius: 6,
    cursor: 'not-allowed',
    opacity: 0.5,
  },
  warningBtn: {
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 14px',
    background: '#f0883e22',
    color: '#f0883e',
    border: '1px solid #f0883e44',
    borderRadius: 6,
    cursor: 'pointer',
  },
  dangerBtn: {
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 14px',
    background: '#f8514922',
    color: '#f85149',
    border: '1px solid #f8514944',
    borderRadius: 6,
    cursor: 'pointer',
  },
  deleteBtn: {
    fontSize: 11,
    fontWeight: 500,
    padding: '4px 10px',
    background: 'none',
    color: '#484f58',
    border: '1px solid #21262d',
    borderRadius: 4,
    cursor: 'pointer',
  },
  loadingState: {
    padding: 32,
    color: '#484f58',
    fontSize: 12,
  },
  errorState: {
    padding: 32,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    fontSize: 12,
  },
  inlineError: {
    marginTop: 6,
    marginBottom: 6,
    padding: '6px 10px',
    background: '#f8514922',
    border: '1px solid #f8514944',
    borderRadius: 4,
    color: '#f85149',
    fontSize: 12,
    whiteSpace: 'pre-wrap' as const,
  },
  refreshBtn: {
    fontSize: 11,
    padding: '3px 10px',
    borderRadius: 4,
    background: '#21262d',
    color: '#8b949e',
    border: '1px solid #30363d',
    cursor: 'pointer',
  },
  // F397: disposition block styles
  derivedFromBanner: {
    fontSize: 11,
    color: '#8b949e',
    marginBottom: 8,
  },
  derivedFromLink: {
    background: 'none',
    border: 'none',
    color: '#58a6ff',
    fontSize: 11,
    cursor: 'pointer',
    padding: 0,
    fontFamily: 'monospace',
    textDecoration: 'underline',
  },
  dispositionRow: {
    marginBottom: 8,
  },
  dispositionSelect: {
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 6,
    color: '#e6edf3',
    fontSize: 12,
    padding: '5px 10px',
    cursor: 'pointer',
    width: '100%',
  },
  dispositionTextarea: {
    width: '100%',
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: 6,
    color: '#e6edf3',
    fontSize: 12,
    padding: '8px 10px',
    lineHeight: 1.5,
    resize: 'vertical' as const,
    boxSizing: 'border-box' as const,
    fontFamily: 'inherit',
    marginBottom: 8,
  },
  dispositionActions: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  savedNote: {
    fontSize: 11,
    color: '#3fb950',
  },
}
