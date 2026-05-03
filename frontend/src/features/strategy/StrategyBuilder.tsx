import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { Plus, Play } from 'lucide-react'
import type { Rule, StrategyRequest, BacktestResult, DataSource, TrailingStopConfig, DynamicSizingConfig, SkipAfterStopConfig, TradingHoursConfig, SavedStrategy, RegimeConfig } from '../../shared/types'
import RuleRow, { emptyRule, validateRules } from './RuleRow'
import { api } from '../../api/client'
import { useSlippage } from '../../shared/hooks/useSlippage'
import { apiErrorDetail } from '../../shared/utils/errors'

import { migrateRule, loadSavedStrategies, SAVED_STRATEGIES_KEY as _SAVED_KEY } from './savedStrategies'

interface Props {
  ticker: string
  start: string
  end: string
  interval: string
  onResult: (r: BacktestResult | null, req?: StrategyRequest) => void
  dataSource: DataSource
  settingsPortalId?: string
  extendedHours?: boolean
}

const STRATEGY_STORAGE_KEY = 'strategylab-strategy'

function loadStrategy() {
  try {
    const raw = localStorage.getItem(STRATEGY_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (parsed.buyRules) parsed.buyRules = parsed.buyRules.map(migrateRule)
    if (parsed.sellRules) parsed.sellRules = parsed.sellRules.map(migrateRule)
    return parsed
  } catch { return null }
}

function persistSavedStrategies(strategies: SavedStrategy[]) {
  localStorage.setItem(_SAVED_KEY, JSON.stringify(strategies))
}

export default function StrategyBuilder({ ticker, start, end, interval, onResult, dataSource, settingsPortalId, extendedHours }: Props) {
  const saved = useState(() => loadStrategy())[0]

  useEffect(() => {
    const NOTIFY_KEY = 'commission_migration_notified'
    if (localStorage.getItem(NOTIFY_KEY)) return
    const legacy = saved && (saved.commission !== undefined) && saved.perShareRate === undefined
    if (!legacy) return
    alert(
      'Commission model updated — defaults to commission-free (Alpaca US equities). ' +
      'For IBKR Fixed, set per-share to 0.0035 and min to 0.35 in Settings.'
    )
    localStorage.setItem(NOTIFY_KEY, '1')
  }, [saved])

  const [buyRules, setBuyRules] = useState<Rule[]>(saved?.buyRules ?? [{ indicator: 'macd', condition: 'crossover_up' }])
  const [sellRules, setSellRules] = useState<Rule[]>(saved?.sellRules ?? [{ indicator: 'macd', condition: 'crossover_down' }])
  const [buyLogic, setBuyLogic] = useState<'AND' | 'OR'>(saved?.buyLogic ?? 'AND')
  const [sellLogic, setSellLogic] = useState<'AND' | 'OR'>(saved?.sellLogic ?? 'AND')
  const [capital, setCapital] = useState(saved?.capital ?? 10000)
  const [posSize, setPosSize] = useState(saved?.posSize ?? 100)
  const [stopLoss, setStopLoss] = useState<number | ''>(saved?.stopLoss ?? '')
  const [maxBarsHeld, setMaxBarsHeld] = useState<number | ''>(saved?.maxBarsHeld ?? '')
  const [trailingEnabled, setTrailingEnabled] = useState<boolean>(saved?.trailingEnabled ?? false)
  const [trailingConfig, setTrailingConfig] = useState<TrailingStopConfig>(saved?.trailingConfig ?? { type: 'pct', value: 5, source: 'high', activate_on_profit: false, activate_pct: 0 })
  const [dynamicSizing, setDynamicSizing] = useState<DynamicSizingConfig>(saved?.dynamicSizing ?? { enabled: false, consec_sls: 2, reduced_pct: 25, trigger: 'sl' })
  const [skipAfterStop, setSkipAfterStop] = useState<SkipAfterStopConfig>(saved?.skipAfterStop ?? { enabled: false, count: 1, trigger: 'sl' })
  const [tradingHours, setTradingHours] = useState<TradingHoursConfig>(() => {
    const th = saved?.tradingHours
    if (!th) return { enabled: false, start_time: '08:30', end_time: '16:00', skip_ranges: [] }
    // Migrate old formats
    const start = typeof th.start_hour === 'number' ? `${String(th.start_hour).padStart(2,'0')}:00` : (th.start_time ?? '08:30')
    const end = typeof th.end_hour === 'number' ? `${String(th.end_hour).padStart(2,'0')}:00` : (th.end_time ?? '16:00')
    const ranges = th.skip_ranges ?? (th.skip_hours ? (th.skip_hours as number[]).map((h: number) => `${String(h).padStart(2,'0')}:00-${String(h+1).padStart(2,'0')}:00`) : [])
    return { enabled: th.enabled, start_time: start, end_time: end, skip_ranges: ranges }
  })
  const [slippageBps, setSlippageBps] = useState<number | ''>(saved?.slippageBps ?? '')
  const [commission, setCommission] = useState<number | ''>(saved?.commission ?? '')
  const [perShareRate, setPerShareRate] = useState<number>(saved?.perShareRate ?? 0)
  const [minPerOrder, setMinPerOrder] = useState<number>(saved?.minPerOrder ?? 0)
  const [borrowRateAnnual, setBorrowRateAnnual] = useState<number>(saved?.borrowRateAnnual ?? 0.5)
  const [slippageSource, setSlippageSource] = useState<'empirical' | 'default' | 'manual'>('default')
  const { data: slipInfo } = useSlippage(ticker)
  const [direction, setDirection] = useState<'long' | 'short'>(saved?.direction ?? 'long')
  const [regimeEnabled, setRegimeEnabled] = useState(saved?.regime?.enabled ?? false)
  const [regimeConfig, setRegimeConfig] = useState<RegimeConfig>(saved?.regime ?? {
    enabled: false, timeframe: '1d', indicator: 'ma',
    indicator_params: { period: 200, type: 'sma' }, condition: 'above', min_bars: 3,
    on_flip: 'close_only',
  })
  const [debug, setDebug] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [savedStrategies, setSavedStrategies] = useState<SavedStrategy[]>(loadSavedStrategies)
  const [activeStrategyName, setActiveStrategyName] = useState<string | null>(null)
  const [showSaveAs, setShowSaveAs] = useState(false)
  const [saveAsName, setSaveAsName] = useState('')
  const [renamingStrategy, setRenamingStrategy] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')

  useEffect(() => {
    if (slippageSource === 'manual') return
    if (slipInfo) {
      setSlippageBps(slipInfo.modeled_bps)
      setSlippageSource(slipInfo.source)
    }
  }, [slipInfo?.modeled_bps, slipInfo?.source, slippageSource])

  function currentSnapshot(name: string): SavedStrategy {
    return {
      name, savedAt: new Date().toISOString(),
      ticker, interval,
      buyRules, sellRules, buyLogic, sellLogic,
      capital, posSize, stopLoss, maxBarsHeld,
      trailingEnabled, trailingConfig, dynamicSizing, skipAfterStop, tradingHours,
      slippageBps, commission, direction,
      perShareRate, minPerOrder, borrowRateAnnual,
      regime: regimeEnabled ? { ...regimeConfig, enabled: true } : undefined,
    }
  }

  function saveStrategy(name: string) {
    const snap = currentSnapshot(name)
    const existing = savedStrategies.find(s => s.name === name)
    if (existing?.pinned) snap.pinned = true
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
    setCapital(s.capital); setPosSize(s.posSize); setStopLoss(s.stopLoss); setMaxBarsHeld(s.maxBarsHeld ?? '')
    setTrailingEnabled(s.trailingEnabled); setTrailingConfig(s.trailingConfig)
    setDynamicSizing(s.dynamicSizing ?? { enabled: false, consec_sls: 2, reduced_pct: 25, trigger: 'sl' })
    setSkipAfterStop(s.skipAfterStop ?? { enabled: false, count: 1, trigger: 'sl' })
    setTradingHours(s.tradingHours)
    setSlippageBps(s.slippageBps); setCommission(s.commission)
    setPerShareRate(s.perShareRate ?? 0)
    setMinPerOrder(s.minPerOrder ?? 0)
    setBorrowRateAnnual(s.borrowRateAnnual ?? 0.5)
    setSlippageSource('manual')
    setDirection(s.direction ?? 'long')
    if (s.regime) {
      setRegimeEnabled(s.regime.enabled)
      setRegimeConfig(s.regime)
    } else {
      setRegimeEnabled(false)
    }
    setActiveStrategyName(s.name)
  }

  function deleteStrategy(name: string) {
    const updated = savedStrategies.filter(s => s.name !== name)
    setSavedStrategies(updated)
    persistSavedStrategies(updated)
    if (activeStrategyName === name) setActiveStrategyName(null)
  }

  function renameStrategy(oldName: string, newName: string) {
    const trimmed = newName.trim()
    if (!trimmed || trimmed === oldName) { setRenamingStrategy(null); return }
    if (savedStrategies.some(s => s.name === trimmed)) { alert(`"${trimmed}" already exists.`); return }
    const updated = savedStrategies.map(s => s.name === oldName ? { ...s, name: trimmed } : s)
    setSavedStrategies(updated)
    persistSavedStrategies(updated)
    if (activeStrategyName === oldName) setActiveStrategyName(trimmed)
    setRenamingStrategy(null)
  }

  function togglePin(name: string) {
    const updated = savedStrategies.map(s => s.name === name ? { ...s, pinned: !s.pinned } : s)
    setSavedStrategies(updated)
    persistSavedStrategies(updated)
  }

  const sortedStrategies = [...savedStrategies].sort((a, b) => {
    if (a.pinned && !b.pinned) return -1
    if (!a.pinned && b.pinned) return 1
    return 0
  })

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
      buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss, maxBarsHeld,
      trailingEnabled, trailingConfig, dynamicSizing, skipAfterStop, tradingHours, slippageBps, commission, direction,
      perShareRate, minPerOrder, borrowRateAnnual,
    }))
  }, [buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss, maxBarsHeld, trailingEnabled, trailingConfig, dynamicSizing, skipAfterStop, tradingHours, slippageBps, commission, direction,
      perShareRate, minPerOrder, borrowRateAnnual])

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
        max_bars_held: maxBarsHeld !== '' && maxBarsHeld > 0 ? maxBarsHeld : undefined,
        trailing_stop: trailingEnabled ? trailingConfig : undefined,
        dynamic_sizing: dynamicSizing.enabled ? dynamicSizing : undefined,
        skip_after_stop: skipAfterStop.enabled ? skipAfterStop : undefined,
        trading_hours: tradingHours.enabled ? tradingHours : undefined,
        slippage_bps: slippageBps !== '' ? slippageBps : undefined,
        per_share_rate: perShareRate,
        min_per_order: minPerOrder,
        borrow_rate_annual: (direction === 'short' || (regimeEnabled && regimeConfig.on_flip === 'close_and_reverse')) ? borrowRateAnnual : 0,
        source: dataSource, debug, direction,
        extended_hours: extendedHours,
        regime: regimeEnabled ? { ...regimeConfig, enabled: true } : undefined,
      }
      const { data } = await api.post('/api/backtest', req)
      onResult(data, req)
    } catch (e) {
      setError(apiErrorDetail(e, 'Backtest failed'))
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
            <label style={styles.settingsLabel}>Slippage (bps)</label>
            <input
              type="number"
              value={slippageBps}
              step={0.5}
              min={0}
              placeholder="2"
              onChange={e => {
                const v = e.target.value
                if (v === '') {
                  setSlippageSource(slipInfo?.source ?? 'default')
                  setSlippageBps(slipInfo?.modeled_bps ?? 2)
                } else {
                  setSlippageBps(Math.max(0, +v))
                  setSlippageSource('manual')
                }
              }}
              style={styles.settingsInput}
            />
            <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 6 }}>
              {slippageSource === 'empirical' && slipInfo
                ? `empirical: ${slipInfo.fill_count} fills`
                : slippageSource === 'default'
                ? 'default: 2 bps'
                : 'manual'}
            </span>
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Commission preset</label>
            <select
              value={
                perShareRate === 0 && minPerOrder === 0 ? 'alpaca'
                : perShareRate === 0.0035 && minPerOrder === 0.35 ? 'ibkr'
                : 'custom'
              }
              onChange={e => {
                const v = e.target.value
                if (v === 'alpaca') { setPerShareRate(0); setMinPerOrder(0) }
                else if (v === 'ibkr') { setPerShareRate(0.0035); setMinPerOrder(0.35) }
              }}
              style={styles.settingsInput}
            >
              <option value="alpaca">Alpaca (commission-free)</option>
              <option value="ibkr">IBKR Fixed ($0.0035 / $0.35)</option>
              <option value="custom">Custom</option>
            </select>
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Rate per share ($)</label>
            <input type="number" value={perShareRate} step={0.0005} min={0} onChange={e => setPerShareRate(+e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Min per order ($)</label>
            <input type="number" value={minPerOrder} step={0.05} min={0} onChange={e => setMinPerOrder(+e.target.value)} style={styles.settingsInput} />
          </div>

          {direction === 'short' && (
            <>
              <div style={{ ...styles.groupTitle, marginTop: 12 }}>Short Costs</div>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Borrow rate (%/yr)</label>
                <input type="number" value={borrowRateAnnual} step={0.1} min={0} onChange={e => setBorrowRateAnnual(+e.target.value)} style={styles.settingsInput} />
              </div>
            </>
          )}
        </div>

        {/* Column 2: Risk Management */}
        <div style={styles.settingsGroup}>
          <div style={styles.groupTitle}>Risk Management</div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Stop Loss (%)</label>
            <input type="number" value={stopLoss} step={0.5} min={0} placeholder="Off" onChange={e => setStopLoss(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
          </div>
          <div style={styles.settingsRow}>
            <label style={styles.settingsLabel}>Time Stop (bars)</label>
            <input type="number" value={maxBarsHeld} step={1} min={1} placeholder="Off" onChange={e => setMaxBarsHeld(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
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
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Trigger</label>
                <select
                  value={dynamicSizing.trigger ?? 'sl'}
                  onChange={e => setDynamicSizing(c => ({ ...c, trigger: e.target.value as 'sl' | 'tsl' | 'both' }))}
                  style={{ ...styles.settingsInput, width: 80 }}
                >
                  <option value="sl">Hard SL</option>
                  <option value="tsl">Trailing</option>
                  <option value="both">Both</option>
                </select>
              </div>
            </div>
          )}
          <div style={{ ...styles.settingsRow, marginTop: 4 }}>
            <label style={{ ...styles.settingsLabel, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
              <input type="checkbox" checked={skipAfterStop.enabled} onChange={e => setSkipAfterStop(c => ({ ...c, enabled: e.target.checked }))} />
              Skip After Stop
            </label>
          </div>
          {skipAfterStop.enabled && (
            <div style={{ paddingLeft: 12, borderLeft: '2px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 8 }}>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Skip</label>
                <input type="number" value={skipAfterStop.count} step={1} min={1} max={20} onChange={e => setSkipAfterStop(c => ({ ...c, count: +e.target.value }))} style={{ ...styles.settingsInput, width: 40 }} />
                <span style={{ fontSize: 11, color: '#8b949e' }}>entries</span>
              </div>
              <div style={styles.settingsRow}>
                <label style={styles.settingsLabel}>Trigger</label>
                <select
                  value={skipAfterStop.trigger}
                  onChange={e => setSkipAfterStop(c => ({ ...c, trigger: e.target.value as 'sl' | 'tsl' | 'both' }))}
                  style={{ ...styles.settingsInput, width: 80 }}
                >
                  <option value="sl">Hard SL</option>
                  <option value="tsl">Trailing</option>
                  <option value="both">Both</option>
                </select>
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
            {sortedStrategies.map(s => (
              <option key={s.name} value={s.name}>{s.pinned ? '★ ' : ''}{s.name}</option>
            ))}
          </select>
          {activeStrategyName && (
            <button onClick={() => saveStrategy(activeStrategyName)} style={styles.strategyBtn}>Save</button>
          )}
          <button onClick={() => { setShowSaveAs(true); setSaveAsName(activeStrategyName ?? '') }} style={styles.strategyBtn}>Save As</button>
          {activeStrategyName && (
            <>
              <button
                onClick={() => { setRenamingStrategy(activeStrategyName); setRenameValue(activeStrategyName) }}
                style={styles.strategyBtn}
              >Rename</button>
              <button
                onClick={() => togglePin(activeStrategyName)}
                style={{ ...styles.strategyBtn, color: savedStrategies.find(s => s.name === activeStrategyName)?.pinned ? 'var(--accent-primary)' : undefined }}
              >{savedStrategies.find(s => s.name === activeStrategyName)?.pinned ? '★ Unpin' : '☆ Pin'}</button>
              <button onClick={() => { if (confirm(`Delete "${activeStrategyName}"?`)) deleteStrategy(activeStrategyName) }} style={{ ...styles.strategyBtn, color: '#f85149' }}>Delete</button>
            </>
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
          {renamingStrategy && (
            <div style={styles.saveAsRow}>
              <input
                autoFocus
                value={renameValue}
                onChange={e => setRenameValue(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') renameStrategy(renamingStrategy, renameValue); if (e.key === 'Escape') setRenamingStrategy(null) }}
                placeholder="New name"
                style={styles.saveAsInput}
              />
              <button onClick={() => renameStrategy(renamingStrategy, renameValue)} style={styles.strategyBtn}>OK</button>
              <button onClick={() => setRenamingStrategy(null)} style={styles.strategyBtn}>Cancel</button>
            </div>
          )}
        </div>
        {!(regimeEnabled && regimeConfig.on_flip && regimeConfig.on_flip !== 'hold') && (
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
        )}
        {regimeEnabled && regimeConfig.on_flip && regimeConfig.on_flip !== 'hold' && (
          <div style={{ padding: '0 16px 6px', fontSize: 11, color: '#8b949e' }}>
            Direction: <span style={{ color: '#58a6ff' }}>{direction}</span> entry · flips to <span style={{ color: '#8b949e' }}>{direction === 'long' ? 'short' : 'long'}</span> on regime flip
          </div>
        )}

        {/* Regime filter */}
        <div style={{ padding: '6px 16px 4px', borderBottom: '1px solid #21262d' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: regimeEnabled ? 8 : 0 }}>
            <button
              onClick={() => setRegimeEnabled((v: boolean) => !v)}
              style={{
                fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4, border: 'none',
                cursor: 'pointer', textTransform: 'uppercase',
                background: regimeEnabled ? '#1a2a3a' : '#161b22',
                color: regimeEnabled ? '#58a6ff' : '#555',
              }}
            >
              Regime
            </button>
            {regimeEnabled && (
              <span style={{ fontSize: 11, color: '#8b949e' }}>
                {regimeConfig.indicator.toUpperCase()}({(regimeConfig.indicator_params as Record<string, unknown>).period as number}) {regimeConfig.condition} · {regimeConfig.timeframe} · {regimeConfig.min_bars}b · {regimeConfig.on_flip ?? 'close_only'}
              </span>
            )}
          </div>
          {regimeEnabled && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, paddingBottom: 6 }}>
              <select
                value={regimeConfig.timeframe}
                onChange={e => setRegimeConfig(c => ({ ...c, timeframe: e.target.value }))}
                style={{ fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}
              >
                {['1d', '1W', '1M'].map(tf => <option key={tf} value={tf}>{tf}</option>)}
              </select>
              <select
                value={(regimeConfig.indicator_params as Record<string, unknown>).type as string ?? 'sma'}
                onChange={e => setRegimeConfig(c => ({ ...c, indicator_params: { ...(c.indicator_params as Record<string, unknown>), type: e.target.value } }))}
                style={{ fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}
              >
                {['sma', 'ema', 'rma'].map(t => <option key={t} value={t}>{t.toUpperCase()}</option>)}
              </select>
              <input
                type="number" min={1} step={1}
                value={(regimeConfig.indicator_params as Record<string, unknown>).period as number ?? 200}
                onChange={e => setRegimeConfig(c => ({ ...c, indicator_params: { ...(c.indicator_params as Record<string, unknown>), period: +e.target.value } }))}
                style={{ width: 52, fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}
                placeholder="period"
              />
              <select
                value={regimeConfig.condition}
                onChange={e => setRegimeConfig(c => ({ ...c, condition: e.target.value as RegimeConfig['condition'] }))}
                style={{ fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}
              >
                <option value="above">Price above</option>
                <option value="below">Price below</option>
                <option value="rising">Rising</option>
                <option value="falling">Falling</option>
              </select>
              <label style={{ fontSize: 11, color: '#8b949e', display: 'flex', alignItems: 'center', gap: 4 }}>
                On flip
                <select
                  value={regimeConfig.on_flip ?? 'close_only'}
                  onChange={e => setRegimeConfig(c => ({ ...c, on_flip: e.target.value as 'close_only' | 'close_and_reverse' | 'hold' }))}
                  style={{ fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}
                >
                  <option value="close_only">Close only</option>
                  <option value="close_and_reverse">Close &amp; reverse</option>
                  <option value="hold">Hold</option>
                </select>
              </label>
              <label style={{ fontSize: 11, color: '#8b949e', display: 'flex', alignItems: 'center', gap: 4 }}>
                Min bars
                <input
                  type="number" min={1} max={20} step={1}
                  value={regimeConfig.min_bars}
                  onChange={e => setRegimeConfig(c => ({ ...c, min_bars: +e.target.value }))}
                  style={{ width: 38, fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}
                />
              </label>
              {!stopLoss && direction === 'long' && (
                <span style={{ fontSize: 10, color: '#f0883e', alignSelf: 'center' }}>⚠ Add a stop-loss to limit open-position risk during flat periods</span>
              )}
            </div>
          )}
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
