import { useState, useEffect } from 'react'
import { Plus, Trash2, Play } from 'lucide-react'
import type { Rule, StrategyRequest, BacktestResult, DataSource } from '../../shared/types'
import axios from 'axios'

interface Props {
  ticker: string
  start: string
  end: string
  interval: string
  onResult: (r: BacktestResult | null) => void
  dataSource: DataSource
}

const INDICATORS = ['macd', 'rsi', 'price', 'ema20', 'ema50', 'ema200'] as const
const CONDITIONS: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down', 'crosses_above', 'crosses_below', 'above', 'below'],
  rsi: ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above'],
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ema20: ['above', 'below', 'rising', 'falling', 'rising_over', 'falling_over'],
  ema50: ['above', 'below', 'rising', 'falling', 'rising_over', 'falling_over'],
  ema200: ['above', 'below', 'rising', 'falling', 'rising_over', 'falling_over'],
}
const CONDITION_LABELS: Record<string, string> = {
  crossover_up: 'Crosses above signal',
  crossover_down: 'Crosses below signal',
  crosses_above: 'Crosses above value',
  crosses_below: 'Crosses below value',
  above: 'Is above',
  below: 'Is below',
  turns_up_below: 'Turns up from below',
  turns_down_above: 'Turns down from above',
  rising: 'Is rising',
  falling: 'Is falling',
  rising_over: 'Rising over N bars',
  falling_over: 'Falling over N bars',
}
const NEEDS_VALUE = ['above', 'below', 'crosses_above', 'crosses_below', 'turns_up_below', 'turns_down_above', 'rising_over', 'falling_over']
const NEEDS_PARAM: Record<string, string[]> = {
  macd: ['crossover_up', 'crossover_down'],
}
// Indicators that can compare against another indicator instead of a fixed value
const CAN_USE_PARAM: Record<string, string[]> = {
  price: ['above', 'below', 'crosses_above', 'crosses_below'],
  ema20: ['above', 'below', 'crosses_above', 'crosses_below'],
  ema50: ['above', 'below', 'crosses_above', 'crosses_below'],
  ema200: ['above', 'below', 'crosses_above', 'crosses_below'],
}
const PARAM_OPTIONS = [
  { value: 'ema20', label: 'EMA 20' },
  { value: 'ema50', label: 'EMA 50' },
  { value: 'ema200', label: 'EMA 200' },
  { value: 'close', label: 'Price' },
]

function RuleRow({ rule, onChange, onDelete }: { rule: Rule; onChange: (r: Rule) => void; onDelete: () => void }) {
  const conditions = CONDITIONS[rule.indicator] || []
  const canParam = CAN_USE_PARAM[rule.indicator]?.includes(rule.condition)
  const hasParam = canParam && !!rule.param
  const forcedParam = NEEDS_PARAM[rule.indicator]?.includes(rule.condition)
  const needsValue = NEEDS_VALUE.includes(rule.condition) && !forcedParam && !hasParam

  // Filter out self-references (e.g. don't show "EMA50" as param when indicator is already ema50)
  const paramOptions = PARAM_OPTIONS.filter(p => {
    if (rule.indicator === 'price' && p.value === 'close') return false
    if (rule.indicator === p.value) return false
    return true
  })

  return (
    <div style={styles.ruleRow}>
      <select value={rule.indicator} onChange={e => onChange({ ...rule, indicator: e.target.value as Rule['indicator'], condition: CONDITIONS[e.target.value][0] as any, value: undefined, param: undefined })} style={styles.ruleSelect}>
        {INDICATORS.map(i => <option key={i} value={i}>{i.toUpperCase()}</option>)}
      </select>
      <select value={rule.condition} onChange={e => onChange({ ...rule, condition: e.target.value as any, param: undefined })} style={styles.ruleSelect}>
        {conditions.map(c => <option key={c} value={c}>{CONDITION_LABELS[c] || c}</option>)}
      </select>
      {canParam && (
        <select
          value={rule.param ?? '_value'}
          onChange={e => {
            const v = e.target.value
            if (v === '_value') onChange({ ...rule, param: undefined })
            else onChange({ ...rule, param: v, value: undefined })
          }}
          style={styles.ruleSelect}
        >
          <option value="_value">Value</option>
          {paramOptions.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
        </select>
      )}
      {needsValue && (
        <input
          type="number"
          value={rule.value ?? ''}
          onChange={e => onChange({ ...rule, value: parseFloat(e.target.value) })}
          placeholder="Value"
          style={{ ...styles.ruleSelect, width: 70 }}
        />
      )}
      <button onClick={onDelete} style={{ color: '#f85149', padding: '4px 6px' }}><Trash2 size={13} /></button>
    </div>
  )
}

const emptyRule = (): Rule => ({ indicator: 'macd', condition: 'crossover_up' })

function validateRules(rules: Rule[], label: string): string | null {
  for (const rule of rules) {
    const hasParam = !!rule.param || NEEDS_PARAM[rule.indicator]?.includes(rule.condition)
    const needsValue = NEEDS_VALUE.includes(rule.condition) && !hasParam
    if (needsValue && (typeof rule.value !== 'number' || isNaN(rule.value))) {
      return `${label} rule "${rule.indicator.toUpperCase()} ${CONDITION_LABELS[rule.condition]}" is missing a value`
    }
  }
  return null
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
  const [slippage, setSlippage] = useState<number | ''>(saved?.slippage ?? '')
  const [commission, setCommission] = useState<number | ''>(saved?.commission ?? '')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    localStorage.setItem(STRATEGY_STORAGE_KEY, JSON.stringify({
      buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss, slippage, commission,
    }))
  }, [buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss, slippage, commission])

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
        slippage_pct: slippage !== '' && slippage > 0 ? slippage : undefined,
        commission_pct: commission !== '' && commission > 0 ? commission : undefined,
        source: dataSource,
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
  ruleRow: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 },
  ruleSelect: { fontSize: 12, padding: '3px 6px', background: '#0d1117', border: '1px solid #30363d', borderRadius: 4, color: '#e6edf3' },
  logicToggle: { display: 'flex', borderRadius: 4, overflow: 'hidden', border: '1px solid #30363d' },
  logicBtn: { padding: '2px 8px', fontSize: 11, background: '#0d1117', color: '#8b949e' },
  logicBtnActive: { background: '#58a6ff', color: '#000' },
  addBtn: { display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, color: '#58a6ff', padding: '2px 6px', border: '1px solid #30363d', borderRadius: 4, background: '#0d1117' },
  settingsRow: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 },
  settingsLabel: { fontSize: 12, color: '#8b949e', width: 100 },
  settingsInput: { width: 80, fontSize: 12, padding: '3px 6px' },
  runBtn: { display: 'flex', alignItems: 'center', gap: 6, background: '#238636', color: '#fff', padding: '7px 16px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer' },
}
