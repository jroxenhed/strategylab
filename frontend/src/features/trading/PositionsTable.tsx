import { useEffect, useState } from 'react'
import { fetchPositions, placeSell, type Position } from '../../api/trading'

export default function PositionsTable() {
  const [positions, setPositions] = useState<Position[]>([])
  const [closing, setClosing] = useState<string | null>(null)

  const load = () => { fetchPositions().then(setPositions).catch(() => {}) }

  useEffect(() => {
    load()
    const id = window.setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [])

  const handleClose = async (symbol: string) => {
    setClosing(symbol)
    try {
      await placeSell(symbol)
      load()
    } catch { /* ignore */ }
    setClosing(null)
  }

  return (
    <div style={styles.section}>
      <div style={styles.header}>
        <span style={styles.title}>Open Positions</span>
        <span style={styles.count}>{positions.length}</span>
      </div>
      {positions.length === 0 ? (
        <div style={styles.empty}>No open positions</div>
      ) : (
        <div style={styles.table}>
          <div style={styles.headRow}>
            {['Symbol', 'Qty', 'Avg Entry', 'Current', 'Mkt Value', 'P&L', 'P&L %', ''].map(h => (
              <span key={h} style={styles.headCell}>{h}</span>
            ))}
          </div>
          {positions.map(p => {
            const plColor = p.unrealized_pl >= 0 ? '#26a641' : '#f85149'
            return (
              <div key={p.symbol} style={styles.row}>
                <span style={{ ...styles.cell, color: '#58a6ff', fontWeight: 600 }}>{p.symbol}</span>
                <span style={styles.cell}>{p.qty}</span>
                <span style={styles.cell}>${p.avg_entry.toFixed(2)}</span>
                <span style={styles.cell}>${p.current_price.toFixed(2)}</span>
                <span style={styles.cell}>${p.market_value.toFixed(2)}</span>
                <span style={{ ...styles.cell, color: plColor }}>
                  {p.unrealized_pl >= 0 ? '+' : ''}${p.unrealized_pl.toFixed(2)}
                </span>
                <span style={{ ...styles.cell, color: plColor }}>
                  {p.unrealized_pl_pct >= 0 ? '+' : ''}{p.unrealized_pl_pct.toFixed(2)}%
                </span>
                <span style={styles.cell}>
                  <button
                    onClick={() => handleClose(p.symbol)}
                    disabled={closing === p.symbol}
                    style={styles.closeBtn}
                  >
                    {closing === p.symbol ? '...' : 'Close'}
                  </button>
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  section: { background: '#0d1117', borderBottom: '1px solid #30363d' },
  header: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 16px', borderBottom: '1px solid #21262d',
  },
  title: { fontSize: 13, fontWeight: 600, color: '#e6edf3' },
  count: {
    fontSize: 11, color: '#8b949e', background: '#21262d',
    padding: '1px 6px', borderRadius: 10,
  },
  empty: { padding: '16px', color: '#484f58', fontSize: 12 },
  table: { overflowX: 'auto' },
  headRow: {
    display: 'flex', gap: 4, padding: '4px 16px',
    borderBottom: '1px solid #21262d',
  },
  headCell: {
    fontSize: 10, color: '#8b949e', width: 90, flexShrink: 0,
    textTransform: 'uppercase' as const, letterSpacing: '0.03em',
  },
  row: {
    display: 'flex', gap: 4, padding: '5px 16px',
    borderBottom: '1px solid #161b22',
  },
  cell: { fontSize: 12, color: '#e6edf3', width: 90, flexShrink: 0 },
  closeBtn: {
    fontSize: 11, padding: '2px 8px', borderRadius: 4,
    background: '#21262d', color: '#f85149', border: '1px solid #30363d',
    cursor: 'pointer',
  },
}
