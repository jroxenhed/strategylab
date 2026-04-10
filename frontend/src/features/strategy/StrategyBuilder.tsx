import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Plus, Play } from 'lucide-react'
import type { Rule, StrategyRequest, BacktestResult, DataSource, TrailingStopConfig, DynamicSizingConfig, TradingHoursConfig, SavedStrategy } from '../../shared/types'
import RuleRow, { emptyRule, validateRules } from './RuleRow'
import { api } from '../../api/client'

interface Props {
  ticker: string
  start: string
  end: string
  interval: string
  onResult: (r: BacktestResult | null) => void
  dataSource: DataSource
  settingsPortalId?: string
}

const STRATEGY_STORAGE_KEY = 'strategylab-strategy'
const SAVED_STRATEGIES_KEY = 'strategylab-saved-strategies'

function loadStrategy() {
  try {
    const raw = localStorage.getItem(STRATEGY_STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function loadSavedStrategies(): SavedStrategy[] {
  try {
    const raw = localStorage.getItem(SAVED_STRATEGIES_KEY)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

function persistSavedStrategies(strategies: SavedStrategy[]) {
  localStorage.setItem(SAVED_STRATEGIES_KEY, JSON.stringify(strategies))
}

export default function StrategyBuilder({ ticker, start, end, interval, onResult, dataSource, settingsPortalId }: Props) {
  const saved = useState(() => loadStrategy())[0]
  const [buyRules, setBuyRules] = useState<Rule[]>(saved?.buyRules ?? [{ indicator: 'macd', condition: 'crossover_up' }])
  const [sellRules, setSellRules] = useState<Rule[]>(saved?.sellRules ?? [{ indicator: 'macd', condition: 'crossover_down' }])
  const [buyLogic, setBuyLogic] = useState<'AND' | 'OR'>(saved?.buyLogic ?? 'AND')
  const [sellLogic, setSellLogic] = useState<'AND' | 'OR'>(saved?.sellLogic ?? 'AND')
  const [capital, setCapital] = useState(saved?.capital ?? 10000)
  const [posSize, setPosSize] = useState(saved?.posSize ?? 100)
  const [stopLoss, setStopLoss] = useState<number | ''>(saved?.stopLoss ?? '')
  const [trailingEnabled, setTrailingEnabled] = useState<boolean>(saved?.trailingEnabled ?? false)
  const [trailingConfig, setTrailingConfig] = useState<TrailingStopConfig>(saved?.trailingConfig ?? { type: 'pct', value: 5, source: 'high', activate_on_profit: false, activate_pct: 0 })
  const [dynamicSizing, setDynamicSizing] = useState<DynamicSizingConfig>(saved?.dynamicSizing ?? { enabled: false, consec_sls: 2, reduced_pct: 25 })
  const [tradingHours, setTradingHours] = useState<TradingHoursConfig>(() => {
    const th = saved?.tradingHours
    if (!th) return { enabled: false, start_time: '08:30', end_time: '16:00', skip_ranges: [] }
    // Migrate old formats
    const start = typeof th.start_hour === 'number' ? `${String(th.start_hour).padStart(2,'0')}:00` : (th.start_time ?? '08:30')
    const end = typeof th.end_hour === 'number' ? `${String(th.end_hour).padStart(2,'0')}:00` : (th.end_time ?? '16:00')
    const ranges = th.skip_ranges ?? (th.skip_hours ? (th.skip_hours as number[]).map((h: number) => `${String(h).padStart(2,'0')}:00-${String(h+1).padStart(2,'0')}:00`) : [])
    return { enabled: th.enabled, start_time: start, end_time: end, skip_ranges: ranges }
  })
  const [slippage, setSlippage] = useState<number | ''>(saved?.slippage ?? '')
  const [commission, setCommission] = useState<number | ''>(saved?.commission ?? '')
  const [direction, setDirection] = useState<'long' | 'short'>(saved?.direction ?? 'long')
  const [debug, setDebug] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [savedStrategies, setSavedStrategies] = useState<SavedStrategy[]>(loadSavedStrategies)
  const [activeStrategyName, setActiveStrategyName] = useState<string | null>(null)
  const [showSaveAs, setShowSaveAs] = useState(false)
  const [saveAsName, setSaveAsName] = useState('')

  function currentSnapshot(name: string): SavedStrategy {
    return {
      name, savedAt: new Date().toISOString(),
      ticker, interval,
      buyRules, sellRules, buyLogic, sellLogic,
      capital, posSize, stopLoss,
      trailingEnabled, trailingConfig, dynamicSizing, tradingHours,
      slippage, commission, direction,
    }
  }

  function saveStrategy(name: string) {
    const snap = currentSnapshot(name)
    const updated = savedStrategies.filter(s => s.name !== name).concat(snap)
    setSavedStrategies(updated)
    persistSavedStrategies(updated)
    setActiveStrategyName(name)
    setShowSaveAs(false)
    setSaveAsName('')
  }

  function loadSavedStrategy(s: SavedStrategy) {
    setBuyRules(s.buyRules); setSellRules(s.sellRules)
    setBuyLogic(s.buyLogic); setSellLogic(s.sellLogic)
    setCapital(s.capital); setPosSize(s.posSize); setStopLoss(s.stopLoss)
    setTrailingEnabled(s.trailingEnabled); setTrailingConfig(s.trailingConfig)
    setDynamicSizing(s.dynamicSizing); setTradingHours(s.tradingHours)
    setSlippage(s.slippage); setCommission(s.commission)
    setDirection(s.direction ?? 'long')
    setActiveStrategyName(s.name)
  }

  function deleteStrategy(name: string) {
    const updated = savedStrategies.filter(s => s.name !== name)
    setSavedStrategies(updated)
    persistSavedStrategies(updated)
    if (activeStrategyName === name) setActiveStrategyName(null)
  }

  // Portal target must be found after first DOM commit, not during render
  const [settingsTarget, setSettingsTarget] = useState<HTMLElement | null>(null)
  useEffect(() => {
    if (settingsPortalId) {
      setSettingsTarget(document.getElementById(settingsPortalId))
    } else {
      setSettingsTarget(null)
    }
  }, [settingsPortalId])

  useEffect(() => {
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({
      buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss,
      trailingEnabled, trailingConfig, dynamicSizing, tradingHours, slippage, commission, direction,
    }))
  }, [buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss, trailingEnabled, trailingConfig, dynamicSizing, tradingHours, slippage, commission, direction])

  async function runBacktest() {
    setLoading(true)
    setError('')
    onResult(null)
    const validationError = validateRules(buyRules, 'BUY') || validateRules(sellRules, 'SELL')
    if (validationError) { setError(validationError); setLoading(false); return }
    try {
      const req: StrategyRequest = {
        ticker, start, end, interval,
        buy_rules: buyRules, sell_rules: sellRules,
        buy_logic: buyLogic, sell_logic: sellLogic,
        initial_capital: capital, position_size: posSize / 100,
        stop_loss_pct: stopLoss !== '' && stopLoss > 0 ? stopLoss : undefined,
        trailing_stop: trailingEnabled ? trailingConfig : undefined,
        dynamic_sizing: dynamicSizing.enabled ? dynamicSizing : undefined,
        trading_hours: tradingHours.enabled ? tradingHours : undefined,
        slippage_pct: slippage !== '' && slippage > 0 ? slippage : undefined,
        commission_pct: commission !== '' && commission > 0 ? commission : undefined,
        source: dataSource, debug, direction,
      }
      const { data } = await api.post('/api/backtest', req)
      onResult(data)
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Backtest failed')
    } finally {
      setLoading(false)
    }
  }

  // ─── Settings JSX (portaled into right panel or rendered inline) ────────────
  const settingsJSX = (
    <div style={styles.settingsPanelInner}>
      <div style={styles.settingsTitle}>Settings</div>
      <div style={styles.settingsGroupsWrapper}>

        {/* Column 1: Capital & Fees */}
        <div style={styles.settingsGroup}>
          <div style={styles.groupTitle}>Capital &amp; Fees</div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Capital ($)</label>
            <input type="number" value={capital} onChange={e => setCapital(+e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>% of Capital</label>
            <input type="number" value={posSize} step={1} min={1} max={100} onChange={e => setPosSize(+e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Slippage (%)</label>
            <input type="number" value={slippage} step={0.05} min={0} placeholder="0" onChange={e => setSlippage(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Commission (%)</label>
            <input type="number" value={commission} step={0.05} min={0} placeholder="0" onChange={e => setCommission(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
          </div>
        </div>

        {/* Column 2: Risk Management */}
        <div style={styles.settingsGroup}>
          <div style={styles.groupTitle}>Risk Management</div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Stop Loss (%)</label>
            <input type="number" value={stopLoss} step={0.5} min={0} placeholder="Off" onChange={e => setStopLoss(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={{ ...styles.settingsRow, marginTop: 4 }}>
            <label style={{ ...styles.settingsLabel, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={trailingEnabled} onChange={e => setTrailingEnabled(e.target.checked)} />
              Trailing Stop
            </label>
          </div>
          {trailingEnabled && (
            <div style={{ paddingLeft: 12, borderLeft: '2px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 8 }}>
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
                  Activate after
                </label>
                <input type="number" value={trailingConfig.activate_pct} step={0.5} min={0} disabled={!trailingConfig.activate_on_profit} onChange={e => setTrailingConfig(c => ({ ...c, activate_pct: +e.target.value }))} style={{ ...styles.settingsInput, width: 48, opacity: trailingConfig.activate_on_profit ? 1 : 0.35 }} />
                <span style={{ fontSize: 11, color: '#8b949e' }}>% profit</span>
              </div>
            </div>
          )}
          <div style={{ ...styles.settingsRow, marginTop: 4 }}>
            <label style={{ ...styles.settingsLabel, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={dynamicSizing.enabled} onChange={e => setDynamicSizing(c => ({ ...c, enabled: e.target.checked }))} />
              Dynamic Sizing
            </label>
          </div>
          {dynamicSizing.enabled && (
            <div style={{ paddingLeft: 12, borderLeft: '2px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>After</label>
                <input type="number" value={dynamicSizing.consec_sls} step={1} min={1} max={10} onChange={e => setDynamicSizing(c => ({ ...c, consec_sls: +e.target.value }))} style={{ ...styles.settingsInput, width: 40 }} />
                <span style={{ fontSize: 11, color: '#8b949e' }}>consec SLs</span>
              </div>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Reduce to</label>
                <input type="number" value={dynamicSizing.reduced_pct} step={5} min={5} max={100} onChange={e => setDynamicSizing(c => ({ ...c, reduced_pct: +e.target.value }))} style={{ ...styles.settingsInput, width: 48 }} />
                <span style={{ fontSize: 11, color: '#8b949e' }}>% size</span>
              </div>
            </div>
          )}
        </div>

        {/* Column 3: Execution */}
        <div style={styles.settingsGroup}>
          <div style={styles.groupTitle}>Execution</div>
          <div style={styles.settingsRow}>
            <label style={{ ...styles.settingsLabel, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={tradingHours.enabled} onChange={e => setTradingHours(c => ({ ...c, enabled: e.target.checked }))} />
              Trading Hours
            </label>
          </div>
          {tradingHours.enabled && (
            <div style={{ paddingLeft: 12, borderLeft: '2px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Window</label>
                <input type="text" value={`${tradingHours.start_time}-${tradingHours.end_time}`} placeholder="08:30-16:00" onChange={e => {
                  const parts = e.target.value.split('-', 2)
                  if (parts.length === 2) {
                    setTradingHours(c => ({ ...c, start_time: parts[0].trim(), end_time: parts[1].trim() }))
                  }
                }} style={{ ...styles.settingsInput, width: 100 }} />
                <span style={{ fontSize: 11, color: '#8b949e' }}>ET</span>
              </div>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Skip</label>
                <input type="text" value={tradingHours.skip_ranges.join(', ')} placeholder="e.g. 12:00-13:00, 15:45-16:00" onChange={e => {
                  const ranges = e.target.value.split(',').map(s => s.trim()).filter(s => s.length > 0)
                  setTradingHours(c => ({ ...c, skip_ranges: ranges }))
                }} style={{ ...styles.settingsInput, width: 180 }} />
              </div>
            </div>
          )}
        </div>

      </div>
    </div>
  )

  // ─── Main render ───────────────────────────────────────────────────────────
  return (
    <>
      {/* Settings: portaled to right panel or inline fallback */}
      {settingsTarget
        ? createPortal(settingsJSX, settingsTarget)
        : <div style={{ ...styles.panel, ...styles.settingsPanelInline }}>{settingsJSX}</div>
      }

      {/* BUY / SELL rules + Run button */}
      <div style={styles.container}>
        {/* Strategy save/load bar */}
        <div style={styles.strategyBar}>
          <select
            value={activeStrategyName ?? ''}
            onChange={e => {
              const name = e.target.value
              if (!name) { setActiveStrategyName(null); return }
              const s = savedStrategies.find(s => s.name === name)
              if (s) loadSavedStrategy(s)
            }}
            style={styles.strategySelect}
          >
            <option value="">Strategy: unsaved</option>
            {savedStrategies.map(s => (
              <option key={s.name} value={s.name}>{s.name}</option>
            ))}
          </select>
          {activeStrategyName && (
            <button onClick={() => saveStrategy(activeStrategyName)} style={styles.strategyBtn}>Save</button>
          )}
          <button onClick={() => { setShowSaveAs(true); setSaveAsName(activeStrategyName ?? '') }} style={styles.strategyBtn}>Save As</button>
          {activeStrategyName && (
            <button onClick={() => { if (confirm(`Delete "${activeStrategyName}"?`)) deleteStrategy(activeStrategyName) }} style={{ ...styles.strategyBtn, color: '#f85149' }}>Delete</button>
          )}
          {showSaveAs && (
            <div style={styles.saveAsRow}>
              <input
                autoFocus
                value={saveAsName}
                onChange={e => setSaveAsName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && saveAsName.trim()) saveStrategy(saveAsName.trim()); if (e.key === 'Escape') setShowSaveAs(false) }}
                placeholder="Strategy name"
                style={styles.saveAsInput}
              />
              <button onClick={() => { if (saveAsName.trim()) saveStrategy(saveAsName.trim()) }} style={styles.strategyBtn}>OK</button>
              <button onClick={() => setShowSaveAs(false)} style={styles.strategyBtn}>Cancel</button>
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 4, marginBottom: 8, paddingLeft: 16 }}>
          {(['long', 'short'] as const).map(d => (
            <button
              key={d}
              onClick={() => setDirection(d)}
              style={{
                padding: '4px 12px', fontSize: 12, borderRadius: 4, border: 'none',
                cursor: 'pointer', textTransform: 'uppercase', fontWeight: 600,
                background: direction === d
                  ? (d === 'long' ? '#1a3a2a' : '#3a1a1a')
                  : '#161b22',
                color: direction === d
                  ? (d === 'long' ? '#26a69a' : '#ef5350')
                  : '#666',
              }}
            >
              {d}
            </button>
          ))}
        </div>
        <div style={styles.panels}>
          {/* BUY */}
          <div style={styles.panel}>
            <div style={styles.panelHeader}>
              <span style={{ color: 'var(--accent-green)', fontWeight: 600 }}>{direction === 'short' ? 'Entry Rules' : 'BUY'} when</span>
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
              <span style={{ color: 'var(--accent-red)', fontWeight: 600 }}>{direction === 'short' ? 'Exit Rules' : 'SELL'} when</span>
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

        <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '8px 16px' }}>
          <button onClick={runBacktest} disabled={loading} style={styles.runBtn}>
            <Play size={14} fill="currentColor" /> {loading ? 'Running...' : 'Run Backtest'}
          </button>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text-secondary)', cursor: 'pointer' }}>
            <input type="checkbox" checked={debug} onChange={e => setDebug(e.target.checked)} />
            Signal Trace
          </label>
          {error && <span style={{ color: 'var(--accent-red)', fontSize: 13, fontWeight: 500 }}>{error}</span>}
        </div>
      </div>
    </>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: { background: 'var(--bg-main)', borderTop: '1px solid var(--border-light)', paddingTop: 12, paddingBottom: 8, display: 'flex', flexDirection: 'column', gap: 8, flexShrink: 0 },
  strategyBar: { display: 'flex', alignItems: 'center', gap: 6, padding: '0 16px', flexWrap: 'wrap' as const },
  strategySelect: { fontSize: 12, padding: '4px 8px', background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-light)', borderRadius: 4, minWidth: 160 },
  strategyBtn: { fontSize: 11, padding: '3px 10px', background: 'var(--bg-input)', color: 'var(--text-secondary)', border: '1px solid var(--border-light)', borderRadius: 4, cursor: 'pointer' },
  saveAsRow: { display: 'flex', alignItems: 'center', gap: 4 },
  saveAsInput: { fontSize: 12, padding: '4px 8px', background: 'var(--bg-input)', color: 'var(--text-primary)', border: '1px solid var(--border-light)', borderRadius: 4, width: 150 },
  panels: { display: 'flex', gap: 16, overflowX: 'auto', paddingBottom: 4, paddingLeft: 16, paddingRight: 16, alignItems: 'flex-start' },
  panel: { minWidth: 260, padding: '12px 14px', background: 'var(--bg-panel)', border: '1px solid var(--border-light)', borderRadius: 'var(--radius-md)' },
  settingsPanelInner: { display: 'flex', flexDirection: 'column', padding: 16, height: '100%', overflowY: 'auto' },
  settingsPanelInline: { minWidth: 440, flex: 1 },
  settingsTitle: { fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 16, paddingBottom: 8, borderBottom: '1px solid var(--border-light)' },
  panelHeader: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, fontSize: 13 },
  logicToggle: { display: 'flex', borderRadius: 'var(--radius-md)', overflow: 'hidden', border: '1px solid var(--border-light)', background: 'var(--bg-input)', padding: 2 },
  logicBtn: { padding: '4px 12px', fontSize: 12, background: 'transparent', color: 'var(--text-secondary)', borderRadius: 'var(--radius-sm)', fontWeight: 600, border: 'none' },
  logicBtnActive: { background: 'var(--bg-panel-hover)', color: 'var(--text-primary)', boxShadow: 'var(--shadow-sm)' },
  addBtn: { display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: 'var(--accent-primary)', padding: '4px 10px', borderRadius: 'var(--radius-md)', background: 'var(--bg-input)', fontWeight: 600, border: '1px solid transparent', transition: 'border-color 0.2s', cursor: 'pointer' },
  settingsGroupsWrapper: { display: 'flex', gap: 40, alignItems: 'flex-start', flexWrap: 'wrap' },
  settingsGroup: { display: 'flex', flexDirection: 'column', gap: 12, minWidth: 180 },
  groupTitle: { fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4, borderBottom: '1px solid var(--border-light)', paddingBottom: 6 },
  settingsRow: { display: 'flex', alignItems: 'center', gap: 8 },
  settingsLabel: { fontSize: 12, color: 'var(--text-secondary)', width: 100, flexShrink: 0 },
  settingsInput: { width: 90, fontSize: 12, padding: '4px 8px' },
  runBtn: { display: 'flex', alignItems: 'center', gap: 8, background: 'linear-gradient(135deg, var(--accent-green), #059669)', color: '#fff', padding: '10px 24px', borderRadius: 'var(--radius-md)', fontSize: 14, fontWeight: 600, cursor: 'pointer', boxShadow: 'rgba(16, 185, 129, 0.2) 0px 4px 12px', transition: 'all 0.2s ease', border: 'none' },
}
