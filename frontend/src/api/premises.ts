import { api } from './client'

// ---------------------------------------------------------------------------
// Status union
// ---------------------------------------------------------------------------

export type PremiseStatus =
  | 'draft'
  | 'awaiting_formalization'
  | 'spec_ready'
  | 'exploring'
  | 'explored'
  | 'awaiting_confirm'
  | 'confirmed'

// ---------------------------------------------------------------------------
// Verdict payload (H2) — matches _extract_verdict in premise_run.py
// ---------------------------------------------------------------------------

export interface VerdictPayload {
  // Core analysis fields
  explore_decision?: string | null
  n_valid_events?: number | null
  mde_q5q1_pp?: number | null
  mde_gate_passed?: boolean | null
  // Hypothesis results
  H1?: Record<string, unknown> | null
  H1b?: Record<string, unknown> | null
  H2?: Record<string, unknown> | null
  fdr_report?: Record<string, unknown> | null
  // Lens results
  era_lens?: Record<string, unknown> | null
  peer_lens?: Record<string, unknown> | null
  regime_lens?: Record<string, unknown> | null
  perturbation_band?: Record<string, unknown> | null
  // Harness meta
  n_events_harness?: number | null
  n_explore_harness?: number | null
  n_confirm_harness?: number | null
  sic_coverage?: Record<string, unknown> | null
  regime_breakdown?: Record<string, unknown> | null
  // Run meta (appended by run service)
  run_type?: 'preview' | 'explore' | null
  verdict_valid?: boolean | null
  note?: string | null
}

// ---------------------------------------------------------------------------
// Request shapes
// ---------------------------------------------------------------------------

export interface CreatePremiseRequest {
  premise_text: string
}

export interface RunPremiseRequest {
  mode: 'preview' | 'explore'
}

// PremiseSpec (for PUT /spec body) — matches backend PremiseSpec Pydantic model
export interface GuidedAnswers {
  trigger?: string | null
  stronger_when?: string | null
  hold_length?: string | null
  direction?: string | null
}

export interface UniverseFloors {
  min_price: number
  min_avg_volume: number
}

export interface PremiseSpec {
  premise_text: string
  guided?: GuidedAnswers | null
  plain_summary?: string | null
  stream: string
  event_filter: Record<string, unknown>
  dose: string
  dose_params: Record<string, unknown>
  horizons: number[]
  entry_lag_days: number
  dedup_same_ticker: boolean
  dedup_window_days: number
  direction: 'long' | 'short'
  floors: UniverseFloors
  min_peer_count: number
  fdr_q: number
  n_boot: number
  spec_hash?: string | null
}

// ---------------------------------------------------------------------------
// Response shapes
// ---------------------------------------------------------------------------

export interface PremiseListItem {
  premise_id: string
  status: PremiseStatus
  premise_text_excerpt: string
  last_updated: string | null
  created_at: string | null
}

export interface CreatePremiseResponse {
  premise_id: string
  status: 'draft'
}

export interface RunHistoryEntry {
  run_type: 'preview' | 'explore'
  started_at?: string | null
  finished_at?: string | null
  verdict?: VerdictPayload | null
  verdict_valid?: boolean
  study_name?: string | null
  error?: string | null
}

// Note: run_history may also contain confirm_request entries (type='confirm_request')
// that are NOT RunHistoryEntry-shaped. These are appended by graduate_to_confirm.
export type RunHistoryItem = RunHistoryEntry | Record<string, unknown>

export interface PremiseFull {
  premise_id?: string
  status: PremiseStatus
  premise_text: string
  spec: PremiseSpec | null
  run_history: RunHistoryItem[]
  error_note?: string | null
  created_at?: string | null
  updated_at?: string | null
  // H4: spec versioning fields returned by GET /{id}
  spec_version?: number | null
  spec_history?: Array<{ version: number; spec: PremiseSpec; at: string }> | null
}

export interface SaveSpecResponse {
  premise_id: string
  status: PremiseStatus
}

export interface SubmitPremiseResponse {
  premise_id: string
  status: PremiseStatus
}

export interface TriggerRunResponse {
  premise_id: string
  // H5: backend returns req.mode ('preview'|'explore'), not literal 'running'
  status: 'preview' | 'explore'
  run_type: 'preview' | 'explore'
}

export interface RunStatusResponse {
  // H1: 'failed' added — backend writes this status in 5 failure paths
  status: 'running' | 'done' | 'error' | 'failed' | 'not_found' | 'unknown'
  run_type?: 'preview' | 'explore' | null
  started_at?: string | null
  finished_at?: string | null
  error?: string | null
  // H2: verdict is a rich dict, not a string
  verdict?: VerdictPayload | null
  note?: string
}

export interface VerdictResponse {
  premise_id: string
  run_type?: 'preview' | 'explore' | null
  verdict_valid?: boolean
  // H2: verdict is a rich dict from _extract_verdict, not a string
  verdict?: VerdictPayload | null
  study_name?: string | null
  note?: string
}

export interface GraduateResponse {
  premise_id: string
  status: PremiseStatus
  spec_hash?: string | null
  // H3: backend returns 'message' (not 'note') + confirm_request dict
  message?: string
  confirm_request?: Record<string, unknown>
}

export interface DeletePremiseResponse {
  premise_id: string
  status: 'draft'
  note: string
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export async function listPremises(status?: PremiseStatus): Promise<PremiseListItem[]> {
  // H6: optional status filter — lets agent poll its queue (e.g. 'awaiting_formalization')
  const params = status ? { status } : undefined
  const res = await api.get('/api/premises', { params })
  return res.data
}

export async function createPremise(req: CreatePremiseRequest): Promise<CreatePremiseResponse> {
  const res = await api.post('/api/premises', req)
  return res.data
}

export async function getPremise(id: string): Promise<PremiseFull> {
  const res = await api.get(`/api/premises/${id}`)
  return res.data
}

export async function submitPremise(id: string): Promise<SubmitPremiseResponse> {
  const res = await api.post(`/api/premises/${id}/submit`)
  return res.data
}

export async function saveSpec(id: string, spec: PremiseSpec): Promise<SaveSpecResponse> {
  const res = await api.put(`/api/premises/${id}/spec`, spec)
  return res.data
}

export async function triggerRun(id: string, req: RunPremiseRequest): Promise<TriggerRunResponse> {
  const res = await api.post(`/api/premises/${id}/run`, req)
  return res.data
}

export async function getRunStatus(id: string): Promise<RunStatusResponse> {
  const res = await api.get(`/api/premises/${id}/run-status`)
  return res.data
}

export async function getVerdict(id: string): Promise<VerdictResponse> {
  const res = await api.get(`/api/premises/${id}/verdict`)
  return res.data
}

export async function graduateToConfirm(id: string): Promise<GraduateResponse> {
  const res = await api.post(`/api/premises/${id}/graduate-to-confirm`)
  return res.data
}

export async function deletePremise(id: string): Promise<DeletePremiseResponse> {
  const res = await api.delete(`/api/premises/${id}`)
  return res.data
}
