import { useState, useEffect } from 'react'
import { Plus } from 'lucide-react'
import { fetchWatchlist, saveWatchlist, scanSignals, placeBuy, placeSell, type SignalResult } from '../../api/trading'
import type { Rule } from '../../shared/types'
import RuleRow, { emptyRule } from '../strategy/RuleRow'

const SCANNER_STORAGE_KEY = 'strategylab-scanner'

function loadScannerSettings() {
  try {
    const raw = localStorage.getItem(SCANNER_STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

export default function SignalScanner() {
  const saved = useState(() => loadScannerSettings())[0]
  const [symbols, setSymbols] = useState('')
  const [interval, setInterval] = useState('15m')
  const [buyRules, setBuyRules] = useState<Rule[]>(saved?.buyRules ?? [{ indicator: 'rsi', condition: 'turns_up_below', value: 30 }])
  const [sellRules, setSellRules] = useState<Rule[]>(saved?.sellRules ?? [{ indicator: 'rsi', condition: 'turns_down_above', value: 70 }])
  const [buyLogic, setBuyLogic] = useState<'AND' | 'OR'>(saved?.buyLogic ?? 'AND')
  const [sellLogic, setSellLogic] = useState<'AND' | 'OR'>(saved?.sellLogic ?? 'AND')
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [results, setResults] = useState<SignalResult[]>([])
  const [scannedAt, setScannedAt] = useState<string | null>(null)
  const [scanError, setScanError] = useState<string | null>(null)
  const [positionSize, setPositionSize] = useState(saved?.positionSize ?? 5000)
  const [stopLossPct, setStopLossPct] = useState<number | ''>(saved?.stopLossPct ?? 2)
  const [executing, setExecuting] = useState<string | null>(null)
  const [executeMsg, setExecuteMsg] = useState<string | null>(null)

  useEffect(() => {
    localStorage.setItem(SCANNER_STORAGE_KEY, JSON.stringify({ buyRules, sellRules, buyLogic, sellLogic, positionSize, stopLossPct }))
  }, [buyRules, sellRules, buyLogic, sellLogic, positionSize, stopLossPct])

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

  const handleSave = async () => {
    setSaving(true)
    const list = symbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    await saveWatchlist(list).catch(() => {})
    setSaving(false)
  }

  const handleScan = async () => {
    setScanning(true)
    setScanError(null)
    const list = symbols.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    try {
      const res = await scanSignals({
        symbols: list,
        interval,
        buy_rules: buyRules,
        sell_rules: sellRules,
        buy_logic: buyLogic,
        sell_logic: sellLogic,
      })
      setResults(res.signals)
      setScannedAt(res.scanned_at)
    } catch (e: any) {
      setScanError(e?.response?.data?.detail ?? e.message ?? 'Scan failed')
    }
    setScanning(false)
  }

  const handleExecute = async (symbol: string, signal: 'BUY' | 'SELL', price: number) => {
    setExecuting(symbol)
    setExecuteMsg(null)
    try {
      if (signal === 'BUY') {
        const qty = Math.floor(positionSize / price)
        if (qty < 1) { setExecuteMsg(`Position size too small for ${symbol}`); setExecuting(null); return }
        const slp = typeof stopLossPct === 'number' && stopLossPct > 0 ? stopLossPct : undefined
        await placeBuy(symbol, qty, slp)
        setExecuteMsg(`Bought ${qty} ${symbol}`)
      } else {
        await placeSell(symbol)
        setExecuteMsg(`Sold ${symbol}`)
      }
    } catch (e: any) {
      setExecuteMsg(e?.response?.data?.detail ?? `Failed to ${signal.toLowerCase()} ${symbol}`)
    }
    setExecuting(null)
  }

  return (
    <div style={styles.section}>
      <div style={styles.header}>
        <span style={styles.title}>Signal Scanner</span>
      </div>

      <div style={styles.controls}>
        <div style={styles.field}>
          <label style={styles.label}>Watchlist</label>
          <input
            style={styles.input}
            value={symbols}
            onChange={e => setSymbols(e.target.value)}
            placeholder="AAPL, ENPH, TSLA..."
          />
        </div>

        <div style={styles.field}>
          <label style={styles.label}>Interval</label>
          <select
            style={styles.select}
            value={interval}
            onChange={e => setInterval(e.target.value)}
          >
            <option value="5m">5m</option>
            <option value="15m">15m</option>
            <option value="1h">1h</option>
          </select>
        </div>

        <div style={styles.field}>
          <label style={styles.label}>Position ($)</label>
          <input
            type="number"
            style={{ ...styles.select, width: 80 }}
            value={positionSize}
            onChange={e => setPositionSize(+e.target.value)}
          />
        </div>

        <div style={styles.field}>
          <label style={styles.label}>Stop Loss %</label>
          <input
            type="number"
            style={{ ...styles.select, width: 60 }}
            value={stopLossPct}
            step={0.5}
            min={0}
            placeholder="Off"
            onChange={e => setStopLossPct(e.target.value === '' ? '' : +e.target.value)}
          />
        </div>

        <button style={styles.saveBtn} onClick={handleSave} disabled={saving || !loaded}>
          {saving ? '...' : 'Save'}
        </button>

        <button style={styles.scanBtn} onClick={handleScan} disabled={scanning || !loaded}>
          {scanning ? 'Scanning...' : 'Scan Now'}
        </button>
      </div>

      {/* Rule editors */}
      <div style={styles.rulesArea}>
        <div style={styles.rulePanel}>
          <div style={styles.rulePanelHeader}>
            <span style={{ color: '#26a641', fontWeight: 600, fontSize: 12 }}>BUY when</span>
            <div style={styles.logicToggle}>
              {(['AND', 'OR'] as const).map(l => (
                <button key={l} onClick={() => setBuyLogic(l)} style={{ ...styles.logicBtn, ...(buyLogic === l ? styles.logicBtnActive : {}) }}>{l}</button>
              ))}
            </div>
            <button onClick={() => setBuyRules(r => [...r, emptyRule()])} style={styles.addBtn}><Plus size={13} /> Add</button>
          </div>
          {buyRules.map((r, i) => (
            <RuleRow key={i} rule={r}
              onChange={nr => setBuyRules(rules => rules.map((x, j) => j === i ? nr : x))}
              onDelete={() => setBuyRules(rules => rules.filter((_, j) => j !== i))} />
          ))}
        </div>
        <div style={styles.rulePanel}>
          <div style={styles.rulePanelHeader}>
            <span style={{ color: '#f85149', fontWeight: 600, fontSize: 12 }}>SELL when</span>
            <div style={styles.logicToggle}>
              {(['AND', 'OR'] as const).map(l => (
                <button key={l} onClick={() => setSellLogic(l)} style={{ ...styles.logicBtn, ...(sellLogic === l ? styles.logicBtnActive : {}) }}>{l}</button>
              ))}
            </div>
            <button onClick={() => setSellRules(r => [...r, emptyRule()])} style={styles.addBtn}><Plus size={13} /> Add</button>
          </div>
          {sellRules.map((r, i) => (
            <RuleRow key={i} rule={r}
              onChange={nr => setSellRules(rules => rules.map((x, j) => j === i ? nr : x))}
              onDelete={() => setSellRules(rules => rules.filter((_, j) => j !== i))} />
          ))}
        </div>
      </div>

      {scanError && (
        <div style={{ padding: '8px 16px', color: '#f85149', fontSize: 12 }}>{scanError}</div>
      )}

      {results.length > 0 && (
        <div style={styles.results}>
          {scannedAt && (
            <div style={styles.scannedAt}>
              Scanned at {new Date(scannedAt).toLocaleTimeString()}
            </div>
          )}
          {executeMsg && (
            <div style={{ padding: '6px 16px', fontSize: 11, color: '#8b949e' }}>{executeMsg}</div>
          )}
          <div style={styles.headRow}>
            {['Symbol', 'Signal', 'Price', 'RSI', 'EMA50', 'Last Bar', ''].map(h => (
              <span key={h} style={styles.headCell}>{h}</span>
            ))}
          </div>
          {results.map(r => {
            const signalColor = r.signal === 'BUY' ? '#26a641'
              : r.signal === 'SELL' ? '#f85149'
              : r.signal === 'ERROR' ? '#f0883e'
              : '#8b949e'
            const canExecute = r.signal === 'BUY' || r.signal === 'SELL'
            return (
              <div key={r.symbol} style={styles.row}>
                <span style={{ ...styles.cell, color: '#58a6ff', fontWeight: 600 }}>{r.symbol}</span>
                <span style={{ ...styles.cell, color: signalColor, fontWeight: 700 }}>{r.signal}</span>
                <span style={styles.cell}>{r.price != null ? `$${r.price.toFixed(2)}` : '—'}</span>
                <span style={styles.cell}>{r.rsi != null ? r.rsi.toFixed(1) : '—'}</span>
                <span style={styles.cell}>{r.ema50 != null ? `$${r.ema50.toFixed(2)}` : '—'}</span>
                <span style={styles.cell}>{r.error ?? (r.last_bar ? new Date(r.last_bar).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—')}</span>
                <span style={styles.cell}>
                  {canExecute && (
                    <button
                      onClick={() => handleExecute(r.symbol, r.signal as 'BUY' | 'SELL', r.price!)}
                      disabled={executing === r.symbol}
                      style={{
                        ...styles.execBtn,
                        background: r.signal === 'BUY' ? '#238636' : '#da3633',
                      }}
                    >
                      {executing === r.symbol ? '...' : r.signal === 'BUY' ? 'Buy' : 'Sell'}
                    </button>
                  )}
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
    display: 'flex', alignItems: 'flex-end', gap: 12,
    padding: '12px 16px',
  },
  field: { display: 'flex', flexDirection: 'column', gap: 4 },
  label: { fontSize: 10, color: '#8b949e', textTransform: 'uppercase' as const, letterSpacing: '0.05em' },
  input: {
    fontSize: 12, padding: '5px 8px', borderRadius: 4,
    background: '#161b22', color: '#e6edf3', border: '1px solid #30363d',
    outline: 'none', width: 260,
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
    fontSize: 12, padding: '5px 14px', borderRadius: 6,
    background: '#238636', color: '#fff', border: 'none',
    cursor: 'pointer', fontWeight: 600, whiteSpace: 'nowrap' as const,
  },
  rulesArea: { display: 'flex', gap: 0, padding: '0 0 8px', borderBottom: '1px solid #21262d' },
  rulePanel: { minWidth: 280, padding: '0 16px', borderRight: '1px solid #21262d' },
  rulePanelHeader: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, paddingTop: 8 },
  logicToggle: { display: 'flex', borderRadius: 4, overflow: 'hidden', border: '1px solid #30363d' },
  logicBtn: { padding: '2px 8px', fontSize: 11, background: '#0d1117', color: '#8b949e', border: 'none', cursor: 'pointer' },
  logicBtnActive: { background: '#58a6ff', color: '#000' },
  addBtn: { display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, color: '#58a6ff', padding: '2px 6px', border: '1px solid #30363d', borderRadius: 4, background: '#0d1117', cursor: 'pointer' },
  results: { overflowX: 'auto' },
  scannedAt: { padding: '6px 16px', fontSize: 10, color: '#484f58' },
  headRow: {
    display: 'flex', gap: 4, padding: '4px 16px',
    borderBottom: '1px solid #21262d',
  },
  headCell: {
    fontSize: 10, color: '#8b949e', width: 100, flexShrink: 0,
    textTransform: 'uppercase' as const, letterSpacing: '0.03em',
  },
  row: {
    display: 'flex', gap: 4, padding: '5px 16px',
    borderBottom: '1px solid #161b22',
  },
  cell: { fontSize: 12, color: '#e6edf3', width: 100, flexShrink: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
  execBtn: {
    fontSize: 11, padding: '2px 10px', borderRadius: 4,
    color: '#fff', border: 'none', cursor: 'pointer', fontWeight: 600,
  },
}
