import { useEffect, useState } from 'react'
import { fetchOrders, type Order, type BrokerHealth } from '../../api/trading'
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

export default function OrderHistory({ brokerFilter, onBrokerFilterChange, availableBrokers, health, heartbeatWarmup, onStale }: Props) {
  const [orders, setOrders] = useState<Order[]>([])
  const [filter, setFilter] = useState('')

  const load = () => {
    fetchOrders(brokerFilter)
      .then(r => { setOrders(r.rows); onStale(r.stale_brokers) })
      .catch(() => {})
  }

  useEffect(() => {
    load()
    const id = window.setInterval(load, 30_000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brokerFilter])

  const filtered = filter
    ? orders.filter(o => o.symbol.toLowerCase().includes(filter.toLowerCase()))
    : orders

  const statusColor = (s: string) => {
    if (s === 'filled') return '#26a641'
    if (s === 'canceled' || s === 'expired') return '#484f58'
    if (s === 'rejected') return '#f85149'
    return '#e5c07b'
  }

  const fmtTime = (s: string) => {
    try { return fmtShortET(s) }
    catch { return s }
  }

  return (
    <div style={styles.section}>
      <div style={styles.header}>
        <span style={styles.title}>Recent Orders</span>
        <select
          value={brokerFilter}
          onChange={e => onBrokerFilterChange(e.target.value)}
          style={styles.brokerFilter}
        >
          <option value="all">All brokers</option>
          {availableBrokers.map(b => (
            <option key={b} value={b}>{b.toUpperCase()}</option>
          ))}
        </select>
        <input
          style={styles.filter}
          placeholder="Filter symbol..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
      </div>
      {filtered.length === 0 ? (
        <div style={styles.empty}>{filter ? 'No matching orders' : 'No orders yet'}</div>
      ) : (
        <div style={styles.table}>
          <div style={styles.headRow}>
            {['Symbol', 'Broker', 'Side', 'Qty', 'Type', 'Status', 'Fill Price', 'Submitted', 'Filled'].map(h => (
              <span key={h} style={styles.headCell}>{h}</span>
            ))}
          </div>
          {filtered.map(o => (
            <div key={o.id} style={styles.row}>
              <span style={{ ...styles.cell, color: '#58a6ff', fontWeight: 600 }}>{o.symbol}</span>
              <span style={styles.cell}>
                <BrokerTag name={o.broker} health={health[o.broker]} warmingUp={heartbeatWarmup} />
              </span>
              <span style={{ ...styles.cell, color: o.side === 'buy' ? '#26a641' : '#f85149' }}>
                {o.side.toUpperCase()}
              </span>
              <span style={styles.cell}>{o.qty}</span>
              <span style={styles.cell}>{o.type}</span>
              <span style={{ ...styles.cell, color: statusColor(o.status) }}>{o.status}</span>
              <span style={styles.cell}>{o.filled_avg_price ? `$${o.filled_avg_price}` : '—'}</span>
              <span style={styles.cell}>{fmtTime(o.submitted_at)}</span>
              <span style={styles.cell}>{o.filled_at ? fmtTime(o.filled_at) : '—'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  section: { background: '#0d1117', borderBottom: '1px solid #30363d', flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 },
  header: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 16px', borderBottom: '1px solid #21262d', flexShrink: 0,
  },
  title: { fontSize: 13, fontWeight: 600, color: '#e6edf3' },
  brokerFilter: {
    fontSize: 11, padding: '2px 6px', borderRadius: 4,
    background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d',
  },
  filter: {
    fontSize: 12, padding: '3px 8px', borderRadius: 4,
    background: '#161b22', color: '#e6edf3', border: '1px solid #30363d',
    outline: 'none', width: 120,
  },
  empty: { padding: '16px', color: '#484f58', fontSize: 12 },
  table: { overflowY: 'auto', flex: 1 },
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
