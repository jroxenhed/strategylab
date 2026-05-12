import { useState, useEffect, useMemo } from 'react'
import { fetchWatchlist, saveWatchlist, batchQuickBacktest } from '../../api/trading'
import type { QuickBacktestResult } from '../../api/trading'
import type { SavedStrategy } from '../../shared/types'
import { apiErrorDetail } from '../../shared/utils/errors'

const SAVED_KEY = 'strategylab-saved-strategies'

const LOOKBACK_OPTIONS: { label: string; days: number }[] = [
  { label: '30d', days: 30 },
  { label: '60d', days: 60 },
  { label: '90d', days: 90 },
  { label: '180d', days: 180 },
  { label: '1Y', days: 365 },
]

type SortKey = 'symbol' | 'signal' | 'return_pct' | 'sharpe' | 'win_rate_pct' | 'num_trades' | 'max_drawdown_pct'

function loadStrategies(): SavedStrategy[] {
  try {
    const raw = localStorage.getItem(SAVED_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

export default function SignalScanner({ onSpawnBot }: { onSpawnBot?: (symbol: string, strategyName: string) => void }) {
  const [symbols, setSymbols] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [strategies, setStrategies] = useState<SavedStrategy[]>(loadStrategies)
  const [selectedIdx, setSelectedIdx] = useState(-1)
  const [lookback, setLookback] = useState(90)
  const [scanning, setScanning] = useState(false)
  const [results, setResults] = useState<QuickBacktestResult[]>([])
  const [scanError, setScanError] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('return_pct')
  const [sortAsc, setSortAsc] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    fetchWatchlist().then(list => {
      if (list.length > 0) setSymbols(list.join(', '))
      else setSymbols('AAPL, ENPH, TSLA, NVDA, AMD')
      setLoaded(true)
    }).catch(() => {
      setSymbols('AAPL, ENPH, TSLA, NVDA, AMD')
      setLoaded(true)
    })
  }, [])

  useEffect(() => {
    const refresh = () => setStrategies(loadStrategies())
    window.addEventListener('focus', refresh)
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      window.removeEventListener('storage', refresh)
    }
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    const list = symbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    try {
      await saveWatchlist(list)
    } catch (e) {
      setSaveError(apiErrorDetail(e, 'Failed to save watchlist'))
    }
    setSaving(false)
  }

  const handleScan = async () => {
    if (selectedIdx < 0) { setScanError('Select a strategy first'); return }
    const strategy = strategies[selectedIdx]
    const list = symbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    if (list.length === 0) { setScanError('No symbols in watchlist'); return }

    setScanning(true)
    setScanError(null)
    try {
      const res = await batchQuickBacktest({
        symbols: list,
        interval: strategy.interval ?? '1d',
        lookback_days: lookback,
        buy_rules: strategy.buyRules,
        sell_rules: strategy.sellRules,
        buy_logic: strategy.buyLogic,
        sell_logic: strategy.sellLogic,
        direction: strategy.direction ?? 'long',
      })
      setResults(res)
    } catch (e) {
      setScanError(apiErrorDetail(e, 'Scan failed'))
    }
    setScanning(false)
  }

  const selectedStrategy = selectedIdx >= 0 ? strategies[selectedIdx] : null

  const sorted = useMemo(() => {
    if (results.length === 0) return results
    return [...results].sort((a, b) => {
      let av: number | string | null = null
      let bv: number | string | null = null
      if (sortKey === 'symbol') { av = a.ticker; bv = b.ticker }
      else if (sortKey === 'signal') { av = a.signal_now ? 1 : 0; bv = b.signal_now ? 1 : 0 }
      else { av = a[sortKey]; bv = b[sortKey] }

      if (av === null && bv === null) return 0
      if (av === null) return 1
      if (bv === null) return -1
      if (typeof av === 'string' && typeof bv === 'string') {
        return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av)
      }
      return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number)
    })
  }, [results, sortKey, sortAsc])

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(a => !a)
    else { setSortKey(key); setSortAsc(false) }
  }

  const colHeaders: { key: SortKey; label: string }[] = [
    { key: 'symbol', label: 'Symbol' },
    { key: 'signal', label: 'Signal' },
    { key: 'return_pct', label: 'Return %' },
    { key: 'sharpe', label: 'Sharpe' },
    { key: 'win_rate_pct', label: 'Win Rate' },
    { key: 'num_trades', label: 'Trades' },
    { key: 'max_drawdown_pct', label: 'Max DD' },
  ]

  return (
    <div style={styles.section}>
      <div style={styles.header}>
        <span style={styles.title}>Strategy Scanner</span>
      </div>

      {/* Controls row */}
      <div style={styles.controls}>
        <div style={styles.field}>
          <label style={styles.label}>Watchlist</label>
          <input
            style={styles.input}
            value={symbols}
            onChange={e => setSymbols(e.target.value)}
            placeholder="AAPL, TSLA, NVDA..."
          />
        </div>

        <div style={styles.field}>
          <label style={styles.label}>Strategy</label>
          <select
            style={{ ...styles.select, width: 180 }}
            value={selectedIdx}
            onChange={e => setSelectedIdx(+e.target.value)}
          >
            <option value={-1}>— select strategy —</option>
            {strategies.map((s, i) => (
              <option key={i} value={i}>{s.name}</option>
            ))}
          </select>
        </div>

        <div style={styles.field}>
          <label style={styles.label}>Lookback</label>
          <select
            style={styles.select}
            value={lookback}
            onChange={e => setLookback(+e.target.value)}
          >
            {LOOKBACK_OPTIONS.map(o => (
              <option key={o.days} value={o.days}>{o.label}</option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <button style={styles.saveBtn} onClick={handleSave} disabled={saving || !loaded}>
            {saving ? '...' : 'Save Watchlist'}
          </button>
          {saveError && <span style={{ color: '#f85149', fontSize: 11 }}>{saveError}</span>}
        </div>

        <button
          style={{ ...styles.scanBtn, opacity: !loaded || scanning ? 0.6 : 1 }}
          onClick={handleScan}
          disabled={!loaded || scanning}
        >
          {scanning ? 'Scanning...' : 'Scan'}
        </button>
      </div>

      {/* Selected strategy rules read-only */}
      {selectedStrategy && (
        <div style={styles.rulesPreview}>
          <div style={styles.ruleGroup}>
            <span style={styles.ruleGroupLabel}>BUY ({selectedStrategy.buyLogic})</span>
            <div style={styles.rulePills}>
              {selectedStrategy.buyRules.map((r, i) => (
                <span key={i} style={{ ...styles.rulePill, background: '#1a2d1a', color: '#3fb950' }}>
                  {r.negated ? <span style={{ color: '#f0883e', marginRight: 3 }}>NOT</span> : null}
                  {r.indicator} {r.condition} {r.value ?? ''}
                </span>
              ))}
            </div>
          </div>
          <div style={styles.ruleGroup}>
            <span style={styles.ruleGroupLabel}>SELL ({selectedStrategy.sellLogic})</span>
            <div style={styles.rulePills}>
              {selectedStrategy.sellRules.map((r, i) => (
                <span key={i} style={{ ...styles.rulePill, background: '#2d1a1a', color: '#f85149' }}>
                  {r.negated ? <span style={{ color: '#f0883e', marginRight: 3 }}>NOT</span> : null}
                  {r.indicator} {r.condition} {r.value ?? ''}
                </span>
              ))}
            </div>
          </div>
          <span style={styles.ruleGroupLabel}>
            Interval: {selectedStrategy.interval ?? '1d'} &nbsp;|&nbsp; Direction: {selectedStrategy.direction ?? 'long'}
          </span>
        </div>
      )}

      {scanError && (
        <div style={styles.errorBar}>{scanError}</div>
      )}

      {/* Results table */}
      {sorted.length > 0 && (
        <div style={styles.results}>
          <div style={styles.headRow}>
            {colHeaders.map(h => (
              <button
                key={h.key}
                style={{ ...styles.headCell, cursor: 'pointer', background: 'none', border: 'none', padding: 0, textAlign: 'left' as const }}
                onClick={() => toggleSort(h.key)}
              >
                {h.label}
                {sortKey === h.key ? (sortAsc ? ' ↑' : ' ↓') : ''}
              </button>
            ))}
            <span style={styles.headCell}>Action</span>
          </div>

          {sorted.map(r => {
            const hasError = !!r.error
            const ret = r.return_pct
            const retColor = ret === null ? '#8b949e' : ret > 0 ? '#3fb950' : ret < 0 ? '#f85149' : '#8b949e'
            const dd = r.max_drawdown_pct
            const ddColor = dd === null ? '#8b949e' : dd < -20 ? '#f85149' : dd < -10 ? '#f0883e' : '#8b949e'

            return (
              <div key={r.ticker} style={styles.row}>
                <span style={{ ...styles.cell, color: '#58a6ff', fontWeight: 600 }}>{r.ticker}</span>
                <span style={styles.cell}>
                  {hasError ? (
                    <span style={{ color: '#f0883e', fontSize: 11 }}>ERR</span>
                  ) : r.signal_now ? (
                    <span style={{ color: '#3fb950', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#3fb950', display: 'inline-block' }} />
                      BUY
                    </span>
                  ) : (
                    <span style={{ color: '#484f58' }}>—</span>
                  )}
                </span>
                <span style={{ ...styles.cell, color: retColor }}>
                  {ret !== null ? `${ret > 0 ? '+' : ''}${ret.toFixed(1)}%` : '—'}
                </span>
                <span style={styles.cell}>
                  {r.sharpe !== null ? r.sharpe.toFixed(2) : '—'}
                </span>
                <span style={styles.cell}>
                  {r.win_rate_pct !== null ? `${r.win_rate_pct.toFixed(0)}%` : '—'}
                </span>
                <span style={styles.cell}>
                  {r.num_trades !== null ? r.num_trades : '—'}
                </span>
                <span style={{ ...styles.cell, color: ddColor }}>
                  {dd !== null ? `${dd.toFixed(1)}%` : '—'}
                </span>
                <span style={styles.cell}>
                  <button
                    style={{
                      ...styles.spawnBtn,
                      opacity: selectedStrategy ? 1 : 0.4,
                      cursor: selectedStrategy ? 'pointer' : 'default',
                    }}
                    disabled={!selectedStrategy}
                    onClick={() => selectedStrategy && onSpawnBot?.(r.ticker, selectedStrategy.name)}
                  >
                    Spawn Bot
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
  controls: {
    display: 'flex', alignItems: 'flex-end', gap: 12, flexWrap: 'wrap' as const,
    padding: '12px 16px',
  },
  field: { display: 'flex', flexDirection: 'column', gap: 4 },
  label: { fontSize: 10, color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.05em' },
  input: {
    fontSize: 12, padding: '5px 8px', borderRadius: 4,
    background: '#161b22', color: '#e6edf3', border: '1px solid #30363d',
    outline: 'none', width: 280,
  },
  select: {
    fontSize: 12, padding: '5px 8px', borderRadius: 4,
    background: '#161b22', color: '#e6edf3', border: '1px solid #30363d',
    outline: 'none',
  },
  saveBtn: {
    fontSize: 12, padding: '5px 10px', borderRadius: 6,
    background: '#21262d', color: '#8b949e', border: '1px solid #30363d',
    cursor: 'pointer', fontWeight: 500, whiteSpace: 'nowrap' as const,
  },
  scanBtn: {
    fontSize: 12, padding: '5px 16px', borderRadius: 6,
    background: '#238636', color: '#fff', border: 'none',
    cursor: 'pointer', fontWeight: 600, whiteSpace: 'nowrap' as const,
  },
  rulesPreview: {
    display: 'flex', gap: 16, flexWrap: 'wrap' as const, alignItems: 'center',
    padding: '8px 16px', borderBottom: '1px solid #21262d',
    background: '#0d1117',
  },
  ruleGroup: { display: 'flex', alignItems: 'center', gap: 6 },
  ruleGroupLabel: { fontSize: 10, color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.04em', whiteSpace: 'nowrap' as const },
  rulePills: { display: 'flex', gap: 4, flexWrap: 'wrap' as const },
  rulePill: {
    fontSize: 11, padding: '2px 7px', borderRadius: 4,
    border: '1px solid #30363d', whiteSpace: 'nowrap' as const,
  },
  errorBar: { padding: '8px 16px', color: '#f85149', fontSize: 12 },
  results: { overflowX: 'auto' as const },
  headRow: {
    display: 'flex', gap: 0, padding: '6px 16px',
    borderBottom: '1px solid #21262d', background: '#0d1117',
  },
  headCell: {
    fontSize: 10, color: '#8b949e', width: 90, flexShrink: 0,
    textTransform: 'uppercase' as const, letterSpacing: '0.03em',
  },
  row: {
    display: 'flex', gap: 0, padding: '6px 16px',
    borderBottom: '1px solid #161b22',
  },
  cell: {
    fontSize: 12, color: '#e6edf3', width: 90, flexShrink: 0,
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const,
    display: 'flex', alignItems: 'center',
  },
  spawnBtn: {
    fontSize: 11, padding: '2px 8px', borderRadius: 4,
    background: '#1c2d40', color: '#58a6ff',
    border: '1px solid #2a4060',
    fontWeight: 500,
  },
}
