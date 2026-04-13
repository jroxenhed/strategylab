import { useEffect, useState, useRef, useCallback } from 'react'
import { fetchJournal, type JournalTrade } from '../../api/trading'
import { fmtShortET } from '../../shared/utils/time'

const DEFAULT_HEIGHT = 300
const MIN_HEIGHT = 100
const MAX_HEIGHT = 800

export default function TradeJournal() {
  const [trades, setTrades] = useState<JournalTrade[]>([])
  const [filter, setFilter] = useState('')
  const [tableHeight, setTableHeight] = useState(DEFAULT_HEIGHT)
  const dragging = useRef(false)
  const startY = useRef(0)
  const startH = useRef(0)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    dragging.current = true
    startY.current = e.clientY
    startH.current = tableHeight
    e.preventDefault()
  }, [tableHeight])

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const newH = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, startH.current + (e.clientY - startY.current)))
      setTableHeight(newH)
    }
    const onMouseUp = () => { dragging.current = false }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => { window.removeEventListener('mousemove', onMouseMove); window.removeEventListener('mouseup', onMouseUp) }
  }, [])

  const reload = () => fetchJournal().then(setTrades).catch(() => {})

  useEffect(() => {
    reload()
    const id = setInterval(reload, 5000)
    return () => clearInterval(id)
  }, [])

  const filtered = filter
    ? trades.filter(t => t.symbol.toLowerCase().includes(filter.toLowerCase()))
    : trades

  // Pair exits with most-recent entry of the same (symbol, direction, source).
  // Mirrors bot state.total_pnl: only bot fills count, entries are consumed on exit
  // (so duplicate/phantom exits can't reuse stale entry prices), and long/short on the
  // same symbol don't clobber each other.
  const exitPnl = new Map<string, number>()        // trade id → pnl
  const exitEntryPrice = new Map<string, number>()  // trade id → entry price
  const lastEntry = new Map<string, { price: number; qty: number }>()
  for (const t of trades) {
    if (t.source !== 'bot') continue
    if (t.price == null || !t.qty) continue
    const isEntry = t.side === 'buy' || t.side === 'short'
    const dir = t.side === 'buy' || t.side === 'sell' ? 'long' : 'short'
    const key = `${t.symbol}:${dir}`
    if (isEntry) {
      lastEntry.set(key, { price: t.price, qty: t.qty })
    } else {
      const entry = lastEntry.get(key)
      if (entry != null) {
        const qty = Math.min(entry.qty, t.qty)
        const pnl = dir === 'short'
          ? (entry.price - t.price) * qty
          : (t.price - entry.price) * qty
        exitPnl.set(t.id, pnl)
        exitEntryPrice.set(t.id, entry.price)
        lastEntry.delete(key)
      }
    }
  }

  const summaryStats = (() => {
    let totalQty = 0
    let totalPnl = 0
    let gainPcts: number[] = []
    let slippages: number[] = []
    for (const t of filtered) {
      totalQty += t.qty || 0
      const pnl = exitPnl.get(t.id)
      if (pnl != null) {
        totalPnl += pnl
        const entryPx = exitEntryPrice.get(t.id)
        if (entryPx != null && t.qty) {
          gainPcts.push((pnl / (entryPx * t.qty)) * 100)
        }
      }
      if (t.expected_price != null && t.price != null) {
        slippages.push((t.price - t.expected_price) / t.expected_price * 100)
      }
    }
    return {
      totalQty,
      totalPnl,
      avgGainPct: gainPcts.length > 0 ? gainPcts.reduce((a, b) => a + b, 0) / gainPcts.length : null,
      avgSlippage: slippages.length > 0 ? slippages.reduce((a, b) => a + b, 0) / slippages.length : null,
    }
  })()

  const fmtTime = (s: string) => {
    try { return fmtShortET(s) }
    catch { return s }
  }

  const exportCsv = () => {
    const headers = ['Time', 'Symbol', 'Side', 'Qty', 'Expected', 'Price', 'P&L', 'Gain %', 'Slippage', 'Source', 'Reason']
    const rows = [...filtered].reverse().map(t => {
      const pnl = exitPnl.get(t.id)
      const entryPx = exitEntryPrice.get(t.id)
      const gainPct = (pnl != null && entryPx != null && t.qty)
        ? ((pnl / (entryPx * t.qty)) * 100).toFixed(2) + '%'
        : ''
      const slippage = (t.expected_price != null && t.price != null)
        ? ((t.price - t.expected_price) / t.expected_price * 100).toFixed(3) + '%'
        : ''
      return [
        fmtTime(t.timestamp),
        t.symbol,
        t.side.toUpperCase(),
        t.qty || '',
        t.expected_price != null ? t.expected_price.toFixed(2) : '',
        t.price != null ? t.price.toFixed(2) : '',
        pnl != null ? pnl.toFixed(2) : '',
        gainPct,
        slippage,
        t.source,
        t.reason || '',
      ].join(',')
    })
    const csv = [headers.join(','), ...rows].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const dateStr = new Date().toISOString().slice(0, 10)
    a.download = `trades-${filter || 'all'}-${dateStr}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div style={styles.section}>
      <div style={styles.header}>
        <span style={styles.title}>Trade Journal</span>
        <span style={styles.count}>{trades.length}</span>
        <button onClick={reload} style={styles.reload} title="Refresh">↻</button>
        <input
          style={styles.filter}
          placeholder="Filter symbol..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
        <button onClick={exportCsv} style={{ ...styles.reload, marginLeft: 'auto' }} title="Export CSV">⬇</button>
      </div>
      {filtered.length === 0 ? (
        <div style={styles.empty}>{filter ? 'No matching trades' : 'No trades logged yet'}</div>
      ) : (<>
        <div style={{ ...styles.table, maxHeight: tableHeight }}>
          <div style={styles.headRow}>
            {['Time', 'Symbol', 'Side', 'Qty', 'Expected', 'Price', 'P&L', 'Gain %', 'Slippage', 'Source', 'Reason'].map(h => (
              <span key={h} style={styles.headCell}>{h}</span>
            ))}
          </div>
          <div style={{ ...styles.row, borderBottom: '1px solid #21262d', background: '#0d1117' }}>
            <span style={styles.summaryCell} />  {/* Time */}
            <span style={styles.summaryCell} />  {/* Symbol */}
            <span style={styles.summaryCell} />  {/* Side */}
            <span style={styles.summaryCell}>{summaryStats.totalQty || ''}</span>
            <span style={styles.summaryCell} />  {/* Expected */}
            <span style={styles.summaryCell} />  {/* Price */}
            <span style={{ ...styles.summaryCell, color: summaryStats.totalPnl >= 0 ? '#26a641' : '#f85149' }}>
              {summaryStats.totalPnl !== 0 ? `${summaryStats.totalPnl >= 0 ? '+' : '-'}$${Math.abs(summaryStats.totalPnl).toFixed(2)}` : ''}
            </span>
            <span style={{ ...styles.summaryCell, color: summaryStats.avgGainPct != null ? (summaryStats.avgGainPct >= 0 ? '#26a641' : '#f85149') : '#8b949e' }}>
              {summaryStats.avgGainPct != null ? `${summaryStats.avgGainPct >= 0 ? '+' : ''}${summaryStats.avgGainPct.toFixed(2)}%` : ''}
            </span>
            <span style={{ ...styles.summaryCell, color: '#8b949e' }}>
              {summaryStats.avgSlippage != null ? `${summaryStats.avgSlippage.toFixed(3)}%` : ''}
            </span>
            <span style={styles.summaryCell} />  {/* Source */}
            <span style={styles.summaryCell} />  {/* Reason */}
          </div>
          {[...filtered].reverse().map(t => (
            <div key={t.id} style={{ ...styles.row, background: rowBackground(t, exitPnl) }}>
              <span style={styles.cell}>{fmtTime(t.timestamp)}</span>
              <span style={{ ...styles.cell, color: '#58a6ff', fontWeight: 600 }}>{t.symbol}</span>
              <span style={{ ...styles.cell, color: sideColor(t, exitPnl) }}>
                {t.side.toUpperCase()}
              </span>
              <span style={styles.cell}>{t.qty || '—'}</span>
              <span style={styles.cell}>
                {t.expected_price != null ? `$${t.expected_price.toFixed(2)}` : '—'}
              </span>
              <span style={styles.cell}>{t.price != null ? `$${t.price.toFixed(2)}` : '—'}</span>
              <span style={{ ...styles.cell, color: exitColor(t, exitPnl) }}>
                {(() => {
                  const pnl = exitPnl.get(t.id)
                  if (pnl == null) return '—'
                  const sign = pnl >= 0 ? '+' : '-'
                  return `${sign}$${Math.abs(pnl).toFixed(2)}`
                })()}
              </span>
              <span style={{ ...styles.cell, color: exitColor(t, exitPnl) }}>
                {(() => {
                  const pnl = exitPnl.get(t.id)
                  const entryPx = exitEntryPrice.get(t.id)
                  if (pnl == null || entryPx == null || !t.qty) return '—'
                  const pct = (pnl / (entryPx * t.qty)) * 100
                  const sign = pct >= 0 ? '+' : ''
                  return `${sign}${pct.toFixed(2)}%`
                })()}
              </span>
              <span style={{ ...styles.cell, color: slippageColor(t) }}>
                {t.expected_price != null && t.price != null
                  ? `${((t.price - t.expected_price) / t.expected_price * 100).toFixed(3)}%`
                  : '—'}
              </span>
              <span style={{ ...styles.cell, color: t.source === 'auto' ? '#e5c07b' : '#8b949e' }}>
                {t.source}
              </span>
              <span style={{ ...styles.cell, color: reasonColor(t.reason, exitPnl.get(t.id)) }}>
                {t.reason || '—'}
              </span>
            </div>
          ))}
        </div>
        <div onMouseDown={onMouseDown} style={styles.resizeHandle}>
          <div style={styles.resizeGrip} />
        </div>
      </>)}
    </div>
  )
}

const exitColor = (t: JournalTrade, pnlMap: Map<string, number>) => {
  const pnl = pnlMap.get(t.id)
  if (pnl != null) return pnl >= 0 ? '#26a641' : '#f85149'  // green win, red loss
  return '#8b949e'  // no entry found to compare
}

const sideColor = (t: JournalTrade, pnlMap: Map<string, number>) => {
  const isEntry = t.side === 'buy' || t.side === 'short'
  if (isEntry) return '#e5c07b'                    // orange — entry (matches chart markers)
  if (t.reason === 'manual') return '#8b949e'      // grey — manual action
  return exitColor(t, pnlMap)                      // green/red based on P&L
}

const rowBackground = (t: JournalTrade, pnlMap: Map<string, number>) => {
  const isEntry = t.side === 'buy' || t.side === 'short'
  if (isEntry) return 'rgba(229, 192, 123, 0.06)'             // orange tint — entry
  if (t.reason === 'manual') return 'rgba(139, 148, 158, 0.06)'  // grey tint
  const pnl = pnlMap.get(t.id)
  if (pnl != null && pnl >= 0) return 'rgba(38, 166, 65, 0.06)'   // green tint — win
  if (pnl != null && pnl < 0) return 'rgba(248, 81, 73, 0.06)'    // red tint — loss
  return 'transparent'
}

const slippageColor = (t: JournalTrade) => {
  if (t.expected_price == null || t.price == null) return '#8b949e'
  const diff = t.price - t.expected_price
  // For buys, positive slippage is bad (paid more). For sells, negative is bad (got less).
  const isBad = t.side === 'buy' ? diff > 0 : diff < 0
  if (Math.abs(diff) < 0.001) return '#8b949e'
  return isBad ? '#f85149' : '#26a641'
}

const reasonColor = (r: string | null, pnl?: number | null) => {
  if (!r) return '#8b949e'
  if (r === 'entry') return '#e5c07b'             // orange — matches Side column
  if (r === 'stop_loss') return '#f85149'          // red
  if (r === 'trailing_stop') return '#d29922'      // amber
  if (r === 'signal') {
    if (pnl != null) return pnl >= 0 ? '#26a641' : '#f85149'  // green win, red loss
    return '#8b949e'  // no P&L context
  }
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
  reload: {
    background: 'none', border: 'none', color: '#8b949e',
    cursor: 'pointer', fontSize: 14, padding: 0,
  },
  filter: {
    fontSize: 12, padding: '3px 8px', borderRadius: 4,
    background: '#161b22', color: '#e6edf3', border: '1px solid #30363d',
    outline: 'none', width: 120,
  },
  empty: { padding: '16px', color: '#484f58', fontSize: 12 },
  table: { overflowY: 'auto' },
  resizeHandle: {
    height: 6, cursor: 'row-resize', display: 'flex',
    alignItems: 'center', justifyContent: 'center',
    background: '#0d1117', borderTop: '1px solid #21262d',
  },
  resizeGrip: {
    width: 40, height: 3, borderRadius: 2, background: '#30363d',
  },
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
  summaryCell: {
    fontSize: 11, color: '#8b949e', width: 90, flexShrink: 0,
    fontStyle: 'italic' as const,
  },
}
