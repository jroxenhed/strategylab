import { useBroker } from '../../shared/hooks/useOHLCV'
import { useAccountQuery } from '../../shared/hooks/useTradingQueries'

export default function AccountBar() {
  const { broker, available, health, heartbeatWarmup, switchBroker, apiCallsPerMinute, dataCallsPerMinute, pollIntervalMs } = useBroker()
  const { data: account, isError, isLoading, error } = useAccountQuery()

  if (isError) return <div style={styles.bar}><span style={{ color: '#f85149' }}>Account error: {(error as Error)?.message ?? 'unknown'}</span></div>
  if (isLoading || !account) return <div style={styles.bar}><span style={{ color: '#8b949e' }}>Loading account...</span></div>

  const metrics = [
    { label: 'Equity', value: `$${account.equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}` },
    { label: 'Cash', value: `$${account.cash.toLocaleString(undefined, { minimumFractionDigits: 2 })}` },
    { label: 'Buying Power', value: `$${account.buying_power.toLocaleString(undefined, { minimumFractionDigits: 2 })}` },
    { label: 'Day Trades', value: account.day_trade_count },
  ]

  return (
    <div style={styles.bar}>
      {available.length > 1 && (
        <div style={styles.brokerSelector}>
          <span style={styles.brokerLabel}>Broker</span>
          <div style={styles.brokerToggle}>
            {available.map(b => {
              const h = health[b]
              const hasHealth = !!h
              const dotColor = heartbeatWarmup
                ? '#9ca3af'
                : h?.healthy === true
                  ? '#10b981'
                  : hasHealth
                    ? '#ef4444'
                    : null
              const title = heartbeatWarmup
                ? 'Broker heartbeat warming up…'
                : hasHealth
                  ? h.healthy
                    ? `${b.toUpperCase()} OK — last ping ${h.last_ok_ts ?? '—'}`
                    : `${b.toUpperCase()} disconnected — ${h.last_error ?? 'unknown error'}`
                  : undefined
              return (
                <button
                  key={b}
                  title={title}
                  onClick={() => switchBroker(b)}
                  style={{
                    padding: '3px 10px',
                    fontSize: 11,
                    fontWeight: 600,
                    background: broker === b ? '#30363d' : 'transparent',
                    color: broker === b ? '#e6edf3' : '#8b949e',
                    border: 'none',
                    borderRadius: 4,
                    cursor: 'pointer',
                    textTransform: 'uppercase' as const,
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                  }}
                >
                  {dotColor && (
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: dotColor }} />
                  )}
                  {b}
                </button>
              )
            })}
          </div>
        </div>
      )}
      {metrics.map(m => (
        <div key={m.label} style={styles.metric}>
          <span style={styles.label}>{m.label}</span>
          <span style={styles.value}>{m.value}</span>
        </div>
      ))}
      <div style={styles.metric}>
        <span style={styles.label}>API</span>
        <span style={{
          ...styles.value,
          fontSize: 13,
          color: apiCallsPerMinute > 190 ? '#ef5350' : apiCallsPerMinute > 150 ? '#f0883e' : '#3fb950',
        }}>
          T:{broker === 'ibkr' ? `${apiCallsPerMinute}/min` : `${apiCallsPerMinute}/200`}
          {' '}D:{dataCallsPerMinute}/min
          {pollIntervalMs != null ? ` @${pollIntervalMs}ms` : ''}
        </span>
      </div>
      {account.trading_blocked && <span style={{ color: '#f85149', fontSize: 12 }}>TRADING BLOCKED</span>}
      {account.pattern_day_trader && <span style={{ color: '#f0883e', fontSize: 12 }}>PDT</span>}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    display: 'flex', alignItems: 'center', gap: 24,
    padding: '10px 16px',
    background: '#161b22', borderBottom: '1px solid #30363d',
    flexShrink: 0,
  },
  metric: { display: 'flex', flexDirection: 'column', gap: 2 },
  label: { fontSize: 10, color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.05em' },
  value: { fontSize: 15, fontWeight: 600, color: '#e6edf3' },
  brokerSelector: { display: 'flex', flexDirection: 'column', gap: 2, marginRight: 8 },
  brokerLabel: { fontSize: 10, color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.05em' },
  brokerToggle: {
    display: 'flex', gap: 2,
    background: '#0d1117', borderRadius: 4, padding: 2,
  },
}
