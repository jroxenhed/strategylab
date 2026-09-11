import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getGatewayStatus, sendGatewayCommand, type GatewayCommand, type GatewayState } from '../../api/gateway'
import { apiErrorDetail } from '../../shared/utils/errors'

const STATE_META: Record<GatewayState, { label: string; color: string; pulse?: boolean }> = {
  logged_in: { label: 'Logged in', color: '#10b981' },
  awaiting_2fa: { label: 'Approve on IBKR Mobile', color: '#f0b74e', pulse: true },
  relogin_required: { label: 'Re-login required', color: '#f0b74e' },
  locked_out: { label: 'Locked out', color: '#ef4444' },
  bad_credentials: { label: 'Bad credentials', color: '#ef4444' },
  restarting: { label: 'Restarting', color: '#8b949e' },
  logging_in: { label: 'Logging in', color: '#8b949e' },
  down: { label: 'Down', color: '#ef4444' },
  unknown: { label: 'Unknown', color: '#8b949e' },
}

function relativeTime(iso: string | null): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const diffMs = Date.now() - then
  const s = Math.floor(diffMs / 1000)
  if (s < 5) return 'just now'
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  return `${d}d ago`
}

export default function GatewayPanel() {
  const qc = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const [confirmRestart, setConfirmRestart] = useState(false)
  const [pendingCmd, setPendingCmd] = useState<GatewayCommand | null>(null)
  const [error, setError] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['gateway-status'],
    queryFn: getGatewayStatus,
    refetchInterval: 15_000,
    retry: false,
  })

  const is404 = (query.error as { response?: { status?: number } } | null)?.response?.status === 404
  if (is404) return null
  if (query.isLoading) return null
  if (query.isError) {
    return (
      <div style={styles.bar}>
        <div style={styles.row}>
          <span style={styles.title}>IB Gateway</span>
          <span style={styles.error}>Gateway status unavailable</span>
        </div>
      </div>
    )
  }
  if (!query.data) return null

  const status = query.data
  const meta = STATE_META[status.state] ?? STATE_META.unknown
  const reachable = status.command_port.reachable

  const runCommand = async (cmd: GatewayCommand) => {
    setError(null)
    setPendingCmd(cmd)
    try {
      await sendGatewayCommand(cmd)
      qc.invalidateQueries({ queryKey: ['gateway-status'] })
    } catch (e) {
      setError(apiErrorDetail(e, `Failed to send ${cmd}`))
    } finally {
      setPendingCmd(null)
      setConfirmRestart(false)
    }
  }

  return (
    <div style={styles.bar}>
      <div style={styles.row}>
        <span style={styles.title}>IB Gateway</span>
        <span
          style={{
            ...styles.chip,
            color: meta.color,
            borderColor: meta.color,
            animation: meta.pulse ? 'gateway-pulse 1.5s ease-in-out infinite' : undefined,
          }}
        >
          {meta.label}
        </span>
        <span style={styles.since}>since {relativeTime(status.since)}</span>
        <span
          title={status.api_connected ? 'Broker API connected' : 'Broker API not connected'}
          style={{
            ...styles.dot,
            background: status.api_connected ? '#10b981' : '#ef4444',
          }}
        />

        <div style={styles.spacer} />

        <button
          style={styles.btn}
          disabled={!reachable || pendingCmd !== null}
          title={reachable ? undefined : 'IBC command server unreachable'}
          onClick={() => runCommand('RECONNECTACCOUNT')}
        >
          {pendingCmd === 'RECONNECTACCOUNT' ? 'Reconnecting…' : 'Reconnect account'}
        </button>
        <button
          style={styles.btn}
          disabled={!reachable || pendingCmd !== null}
          title={reachable ? undefined : 'IBC command server unreachable'}
          onClick={() => runCommand('RECONNECTDATA')}
        >
          {pendingCmd === 'RECONNECTDATA' ? 'Reconnecting…' : 'Reconnect data'}
        </button>

        {!confirmRestart ? (
          <button
            style={styles.btn}
            disabled={!reachable || pendingCmd !== null}
            title={reachable ? undefined : 'IBC command server unreachable'}
            onClick={() => setConfirmRestart(true)}
          >
            Restart
          </button>
        ) : (
          <span style={styles.confirmWrap}>
            <span style={styles.confirmText}>Restart the Gateway? Bots on IBKR pause ~1 min.</span>
            <button style={{ ...styles.btn, color: '#ef4444', borderColor: '#ef4444' }} onClick={() => runCommand('RESTART')}>
              Yes
            </button>
            <button style={styles.btn} onClick={() => setConfirmRestart(false)}>
              No
            </button>
          </span>
        )}

        <a
          href="/vnc/vnc.html?autoconnect=1&resize=scale&path=websockify"
          target="_blank"
          rel="noreferrer"
          style={{
            ...styles.btn,
            textDecoration: 'none',
            display: 'inline-block',
            pointerEvents: reachable ? 'auto' : 'none',
            opacity: reachable ? 1 : 0.5,
          }}
          title={reachable ? undefined : 'IBC command server unreachable'}
          aria-disabled={!reachable}
        >
          Open screen
        </a>

        <button style={styles.btn} onClick={() => setExpanded(e => !e)}>
          {expanded ? 'Hide log' : 'Show log'}
        </button>
      </div>

      {error && <div style={styles.error}>{error}</div>}

      {expanded && (
        <pre style={styles.log}>
          {status.last_lines.length > 0 ? status.last_lines.join('\n') : 'No recent log lines.'}
        </pre>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    display: 'flex', flexDirection: 'column', gap: 4,
    padding: '8px 16px',
    background: '#161b22', borderBottom: '1px solid #30363d',
    flexShrink: 0,
  },
  row: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  title: { fontSize: 12, fontWeight: 600, color: '#e6edf3', textTransform: 'uppercase' as const, letterSpacing: '0.05em' },
  chip: {
    fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 10,
    border: '1px solid', background: 'transparent',
  },
  since: { fontSize: 11, color: '#8b949e' },
  dot: { width: 7, height: 7, borderRadius: '50%', flexShrink: 0 },
  spacer: { flex: 1 },
  btn: {
    background: 'transparent', border: '1px solid #30363d', color: '#8b949e',
    fontSize: 11, padding: '3px 10px', borderRadius: 4, cursor: 'pointer',
  },
  confirmWrap: { display: 'flex', alignItems: 'center', gap: 6 },
  confirmText: { fontSize: 11, color: '#f0b74e' },
  error: { fontSize: 11, color: '#ef4444' },
  log: {
    fontFamily: 'monospace', fontSize: 11, color: '#8b949e',
    background: '#0d1117', border: '1px solid #30363d', borderRadius: 4,
    padding: 8, margin: 0, maxHeight: 200, overflowY: 'auto', whiteSpace: 'pre-wrap',
  },
}
