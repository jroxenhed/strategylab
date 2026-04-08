import { useEffect, useState } from 'react'
import { fetchAccount, type Account } from '../../api/trading'

export default function AccountBar() {
  const [account, setAccount] = useState<Account | null>(null)
  const [error, setError] = useState<string | null>(null)
  const hasLoaded = useState({ value: false })[0]

  useEffect(() => {
    const load = () => {
      fetchAccount().then(a => { setAccount(a); setError(null); hasLoaded.value = true }).catch(e => {
        // Only show error if we've never loaded successfully
        if (!hasLoaded.value) setError(e.message)
      })
    }
    load()
    const id = window.setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [])

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
}
