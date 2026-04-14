import { useEffect, useState } from 'react'
import { fetchPositions, placeSell, fetchJournal, type Position, type JournalTrade, type BrokerHealth } from '../../api/trading'
import { fmtShortET } from '../../shared/utils/time'
import { BrokerTag } from './BrokerTag'

interface Props {
  brokerFilter: string
  onBrokerFilterChange: (v: string) => void
  availableBrokers: string[]
  health: Record<string, BrokerHealth>
  heartbeatWarmup: boolean
  onStale: (list: string[]) => void
}

export default function PositionsTable({ brokerFilter, onBrokerFilterChange, availableBrokers, health, heartbeatWarmup, onStale }: Props) {
  const [positions, setPositions] = useState<Position[]>([])
  const [journal, setJournal] = useState<JournalTrade[]>([])
  const [closing, setClosing] = useState<string | null>(null)

  const load = () => {
    fetchPositions(brokerFilter)
      .then(r => { setPositions(r.rows); onStale(r.stale_brokers) })
      .catch(() => {})
    fetchJournal(undefined, brokerFilter).then(setJournal).catch(() => {})
  }

  useEffect(() => {
    load()
    const id = window.setInterval(load, 5_000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brokerFilter])

  const entryTimeMap = new Map<string, string>()
  for (const t of journal) {
    if (t.source !== 'bot') continue
    const isEntry = t.side === 'buy' || t.side === 'short'
    if (!isEntry) continue
    const side = t.side === 'short' ? 'short' : 'long'
    entryTimeMap.set(`${t.symbol}|${t.broker ?? ''}|${side}`, t.timestamp)
  }

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
        <select
          value={brokerFilter}
          onChange={e => onBrokerFilterChange(e.target.value)}
          style={styles.filter}
        >
          <option value="all">All brokers</option>
          {availableBrokers.map(b => (
            <option key={b} value={b}>{b.toUpperCase()}</option>
          ))}
        </select>
      </div>
      {positions.length === 0 ? (
        <div style={styles.empty}>No open positions</div>
      ) : (
        <div style={styles.table}>
          <div style={styles.headRow}>
            {['Opened', 'Symbol', 'Broker', 'Side', 'Qty', 'Avg Entry', 'Current', 'Mkt Value', 'P&L', 'P&L %', ''].map(h => (
              <span key={h} style={styles.headCell}>{h}</span>
            ))}
          </div>
          {positions.map(p => {
            const plColor = p.unrealized_pl >= 0 ? '#26a641' : '#f85149'
            const entryKey = `${p.symbol}|${p.broker ?? ''}|${p.side}`
            return (
              <div key={entryKey} style={styles.row}>
                <span style={styles.cell}>
                  {entryTimeMap.get(entryKey) ? fmtShortET(entryTimeMap.get(entryKey)!) : '—'}
                </span>
                <span style={{ ...styles.cell, color: '#58a6ff', fontWeight: 600 }}>{p.symbol}</span>
                <span style={styles.cell}>
                  <BrokerTag name={p.broker} health={health[p.broker]} warmingUp={heartbeatWarmup} />
                </span>
                <span style={{ ...styles.cell, color: p.side === 'short' ? '#ef5350' : '#26a69a', textTransform: 'uppercase' as const, fontSize: 10, fontWeight: 700 }}>{p.side}</span>
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
  filter: {
    marginLeft: 'auto', fontSize: 11, padding: '2px 6px',
    background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d',
    borderRadius: 4,
  },
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
