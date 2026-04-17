import { useEffect, useState } from 'react'
import axios from 'axios'
import { fetchAccount, type Account } from '../../api/trading'
import { useBroker } from '../../shared/hooks/useOHLCV'

export default function AccountBar() {
  const [account, setAccount] = useState<Account | null>(null)
  const [error, setError] = useState<string | null>(null)
  const hasLoaded = useState({ value: false })[0]
  const { broker, available, health, heartbeatWarmup, switchBroker } = useBroker()

  useEffect(() => {
    const ctrl = new AbortController()
    const load = () => {
      if (document.hidden) return
      fetchAccount(ctrl.signal).then(a => { setAccount(a); setError(null); hasLoaded.value = true }).catch(e => {
        if (axios.isCancel(e)) return
        if (!hasLoaded.value) setError(e.message)
      })
    }
    load()
    const id = window.setInterval(load, 30_000)
    return () => { clearInterval(id); ctrl.abort() }
  }, [broker])

  if (error) return <div style={styles.bar}><span style={{ color: '#f85149' }}>Account error: {error}</span></div>
  if (!account) return <div style={styles.bar}><span style={{ color: '#8b949e' }}>Loading account...</span></div>

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
