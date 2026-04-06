import { useState, useEffect } from 'react'
import { Plus, Play } from 'lucide-react'
import type { Rule, StrategyRequest, BacktestResult, DataSource, TrailingStopConfig } from '../../shared/types'
import RuleRow, { emptyRule, validateRules } from './RuleRow'
import axios from 'axios'

interface Props {
  ticker: string
  start: string
  end: string
  interval: string
  onResult: (r: BacktestResult | null) => void
  dataSource: DataSource
}

const STRATEGY_STORAGE_KEY = 'strategylab-strategy'

function loadStrategy() {
  try {
    const raw = localStorage.getItem(STRATEGY_STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

export default function StrategyBuilder({ ticker, start, end, interval, onResult, dataSource }: Props) {
  const saved = useState(() => loadStrategy())[0]
  const [buyRules, setBuyRules] = useState<Rule[]>(saved?.buyRules ?? [{ indicator: 'macd', condition: 'crossover_up' }])
  const [sellRules, setSellRules] = useState<Rule[]>(saved?.sellRules ?? [{ indicator: 'macd', condition: 'crossover_down' }])
  const [buyLogic, setBuyLogic] = useState<'AND' | 'OR'>(saved?.buyLogic ?? 'AND')
  const [sellLogic, setSellLogic] = useState<'AND' | 'OR'>(saved?.sellLogic ?? 'AND')
  const [capital, setCapital] = useState(saved?.capital ?? 10000)
  const [posSize, setPosSize] = useState(saved?.posSize ?? 100)
  const [stopLoss, setStopLoss] = useState<number | ''>(saved?.stopLoss ?? '')
  const [trailingEnabled, setTrailingEnabled] = useState<boolean>(saved?.trailingEnabled ?? false)
  const [trailingConfig, setTrailingConfig] = useState<TrailingStopConfig>(saved?.trailingConfig ?? { type: 'pct', value: 5, source: 'high', activate_on_profit: false })
  const [slippage, setSlippage] = useState<number | ''>(saved?.slippage ?? '')
  const [commission, setCommission] = useState<number | ''>(saved?.commission ?? '')
  const [debug, setDebug] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({
      buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss, trailingEnabled, trailingConfig, slippage, commission,
    }))
  }, [buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss, trailingEnabled, trailingConfig, slippage, commission])

  async function runBacktest() {
    setLoading(true)
    setError('')
    onResult(null)

    const validationError = validateRules(buyRules, 'BUY') || validateRules(sellRules, 'SELL')
    if (validationError) {
      setError(validationError)
      setLoading(false)
      return
    }

    try {
      const req: StrategyRequest = {
        ticker, start, end, interval,
        buy_rules: buyRules, sell_rules: sellRules,
        buy_logic: buyLogic, sell_logic: sellLogic,
        initial_capital: capital, position_size: posSize / 100,
        stop_loss_pct: stopLoss !== '' && stopLoss > 0 ? stopLoss : undefined,
        trailing_stop: trailingEnabled ? trailingConfig : undefined,
        slippage_pct: slippage !== '' && slippage > 0 ? slippage : undefined,
        commission_pct: commission !== '' && commission > 0 ? commission : undefined,
        source: dataSource,
        debug,
      }
      const { data } = await axios.post('http://localhost:8000/api/backtest', req)
      onResult(data)
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Backtest failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={styles.container}>
      <div style={styles.panels}>
        {/* BUY */}
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <span style={{ color: '#26a641', fontWeight: 600 }}>BUY when</span>
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

        {/* SELL */}
        <div style={styles.panel}>
          <div style={styles.panelHeader}>
            <span style={{ color: '#f85149', fontWeight: 600 }}>SELL when</span>
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

        {/* Settings */}
        <div style={styles.panel}>
          <div style={styles.panelHeader}><span style={{ fontWeight: 600, color: '#8b949e' }}>Settings</span></div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Capital ($)</label>
            <input type="number" value={capital} onChange={e => setCapital(+e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>% of Capital</label>
            <input type="number" value={posSize} step={1} min={1} max={100} onChange={e => setPosSize(+e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Stop Loss (%)</label>
            <input type="number" value={stopLoss} step={0.5} min={0} placeholder="Off" onChange={e => setStopLoss(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={{ ...styles.settingsLabel, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={trailingEnabled} onChange={e => setTrailingEnabled(e.target.checked)} />
              Trailing Stop
            </label>
          </div>
          {trailingEnabled && (<>
            <div style={styles.settingsRow}>
              <label style={styles.settingsLabel}>Type</label>
              <select value={trailingConfig.type} onChange={e => setTrailingConfig(c => ({ ...c, type: e.target.value as 'pct' | 'atr' }))} style={styles.settingsInput}>
                <option value="pct">%</option>
                <option value="atr">ATR</option>
              </select>
            </div>
            <div style={styles.settingsRow}>
              <label style={styles.settingsLabel}>Value</label>
              <input type="number" value={trailingConfig.value} step={0.5} min={0.1} onChange={e => setTrailingConfig(c => ({ ...c, value: +e.target.value }))} style={styles.settingsInput} />
            </div>
            <div style={styles.settingsRow}>
              <label style={styles.settingsLabel}>Source</label>
              <select value={trailingConfig.source} onChange={e => setTrailingConfig(c => ({ ...c, source: e.target.value as 'high' | 'close' }))} style={styles.settingsInput}>
                <option value="high">High</option>
                <option value="close">Close</option>
              </select>
            </div>
            <div style={styles.settingsRow}>
              <label style={{ ...styles.settingsLabel, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer', width: 'auto' }}>
                <input type="checkbox" checked={trailingConfig.activate_on_profit} onChange={e => setTrailingConfig(c => ({ ...c, activate_on_profit: e.target.checked }))} />
                Activate on profit only
              </label>
            </div>
          </>)}
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Slippage (%)</label>
            <input type="number" value={slippage} step={0.05} min={0} placeholder="0" onChange={e => setSlippage(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Commission (%)</label>
            <input type="number" value={commission} step={0.05} min={0} placeholder="0" onChange={e => setCommission(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingLeft: 12 }}>
        <button onClick={runBacktest} disabled={loading} style={styles.runBtn}>
          <Play size={14} /> {loading ? 'Running...' : 'Run Backtest'}
        </button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: '#8b949e', cursor: 'pointer' }}>
          <input type="checkbox" checked={debug} onChange={e => setDebug(e.target.checked)} />
          Signal Trace
        </label>
        {error && <span style={{ color: '#f85149', fontSize: 12 }}>{error}</span>}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: { background: '#161b22', borderTop: '1px solid #30363d', padding: '10px 0', display: 'flex', flexDirection: 'column', gap: 8 },
  panels: { display: 'flex', gap: 0, overflowX: 'auto', paddingBottom: 4 },
  panel: { minWidth: 280, padding: '0 12px', borderRight: '1px solid #21262d' },
  panelHeader: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: 12 },
  logicToggle: { display: 'flex', borderRadius: 4, overflow: 'hidden', border: '1px solid #30363d' },
  logicBtn: { padding: '2px 8px', fontSize: 11, background: '#0d1117', color: '#8b949e' },
  logicBtnActive: { background: '#58a6ff', color: '#000' },
  addBtn: { display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, color: '#58a6ff', padding: '2px 6px', border: '1px solid #30363d', borderRadius: 4, background: '#0d1117' },
  settingsRow: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 },
  settingsLabel: { fontSize: 12, color: '#8b949e', width: 100 },
  settingsInput: { width: 80, fontSize: 12, padding: '3px 6px' },
  runBtn: { display: 'flex', alignItems: 'center', gap: 6, background: '#238636', color: '#fff', padding: '7px 16px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer' },
}
