import { useEffect, useState } from 'react'
import { fetchJournal, type JournalTrade } from '../../api/trading'
import { fmtShortET } from '../../shared/utils/time'

export default function TradeJournal() {
  const [trades, setTrades] = useState<JournalTrade[]>([])
  const [filter, setFilter] = useState('')

  useEffect(() => {
    fetchJournal().then(setTrades).catch(() => {})
  }, [])

  const filtered = filter
    ? trades.filter(t => t.symbol.toLowerCase().includes(filter.toLowerCase()))
    : trades

  const fmtTime = (s: string) => {
    try { return fmtShortET(s) }
    catch { return s }
  }

  return (
    <div style={styles.section}>
      <div style={styles.header}>
        <span style={styles.title}>Trade Journal</span>
        <span style={styles.count}>{trades.length}</span>
        <input
          style={styles.filter}
          placeholder="Filter symbol..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
      </div>
      {filtered.length === 0 ? (
        <div style={styles.empty}>{filter ? 'No matching trades' : 'No trades logged yet'}</div>
      ) : (
        <div style={styles.table}>
          <div style={styles.headRow}>
            {['Time', 'Symbol', 'Side', 'Qty', 'Price', 'Stop Loss', 'Source', 'Reason'].map(h => (
              <span key={h} style={styles.headCell}>{h}</span>
            ))}
          </div>
          {[...filtered].reverse().map(t => (
            <div key={t.id} style={styles.row}>
              <span style={styles.cell}>{fmtTime(t.timestamp)}</span>
              <span style={{ ...styles.cell, color: '#58a6ff', fontWeight: 600 }}>{t.symbol}</span>
              <span style={{ ...styles.cell, color: t.side === 'buy' ? '#26a641' : '#f85149' }}>
                {t.side.toUpperCase()}
              </span>
              <span style={styles.cell}>{t.qty || '—'}</span>
              <span style={styles.cell}>{t.price != null ? `$${t.price.toFixed(2)}` : '—'}</span>
              <span style={styles.cell}>{t.stop_loss_price != null ? `$${t.stop_loss_price.toFixed(2)}` : '—'}</span>
              <span style={{ ...styles.cell, color: t.source === 'auto' ? '#e5c07b' : '#8b949e' }}>
                {t.source}
              </span>
              <span style={{ ...styles.cell, color: reasonColor(t.reason) }}>
                {t.reason || '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const reasonColor = (r: string | null) => {
  if (!r) return '#8b949e'
  if (r === 'stop_loss') return '#f85149'
  if (r === 'trailing_stop') return '#d29922'
  if (r === 'signal') return '#58a6ff'
  if (r === 'entry') return '#26a641'
  if (r === 'manual') return '#8b949e'
  return '#8b949e'
}

const styles: Record<string, React.CSSProperties> = {
  section: { background: '#0d1117', borderBottom: '1px solid #30363d' },
  header: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 16px', borderBottom: '1px solid #21262d', flexShrink: 0,
  },
  title: { fontSize: 13, fontWeight: 600, color: '#e6edf3' },
  count: {
    fontSize: 11, color: '#8b949e', background: '#21262d',
    padding: '1px 6px', borderRadius: 10,
  },
  filter: {
    fontSize: 12, padding: '3px 8px', borderRadius: 4,
    background: '#161b22', color: '#e6edf3', border: '1px solid #30363d',
    outline: 'none', width: 120, marginLeft: 'auto',
  },
  empty: { padding: '16px', color: '#484f58', fontSize: 12 },
  table: { overflowY: 'auto', maxHeight: 200 },
  headRow: {
    display: 'flex', gap: 4, padding: '4px 16px',
    borderBottom: '1px solid #21262d', position: 'sticky' as const, top: 0,
    background: '#0d1117',
  },
  headCell: {
    fontSize: 10, color: '#8b949e', width: 90, flexShrink: 0,
    textTransform: 'uppercase' as const, letterSpacing: '0.03em',
  },
  row: {
    display: 'flex', gap: 4, padding: '5px 16px',
    borderBottom: '1px solid #161b22',
  },
  cell: { fontSize: 12, color: '#e6edf3', width: 90, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
}
