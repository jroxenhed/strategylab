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
  onSweep?: (path: string, centerVal: number) => void
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

export default function StrategyBuilder({ ticker, start, end, interval, onResult, onSweep, dataSource, settingsPortalId, extendedHours }: Props) {
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
  // B23: dual rule sets for regime long/short modes
  const [longBuyRules, setLongBuyRules] = useState<Rule[]>(saved?.longBuyRules ?? [])
  const [longSellRules, setLongSellRules] = useState<Rule[]>(saved?.longSellRules ?? [])
  const [longBuyLogic, setLongBuyLogic] = useState<'AND' | 'OR'>(saved?.longBuyLogic ?? 'AND')
  const [longSellLogic, setLongSellLogic] = useState<'AND' | 'OR'>(saved?.longSellLogic ?? 'AND')
  const [shortBuyRules, setShortBuyRules] = useState<Rule[]>(saved?.shortBuyRules ?? [])
  const [shortSellRules, setShortSellRules] = useState<Rule[]>(saved?.shortSellRules ?? [])
  const [shortBuyLogic, setShortBuyLogic] = useState<'AND' | 'OR'>(saved?.shortBuyLogic ?? 'AND')
  const [shortSellLogic, setShortSellLogic] = useState<'AND' | 'OR'>(saved?.shortSellLogic ?? 'AND')
  // B28: regime rule set state
  const [regimeBuyRules, setRegimeBuyRules] = useState<Rule[]>(saved?.regime?.rules ?? [])
  const [regimeLogic, setRegimeLogic] = useState<'AND' | 'OR'>(saved?.regime?.logic ?? 'AND')
  const [activeRuleTab, setActiveRuleTab] = useState<'long' | 'short' | 'regime'>(
    saved?.regime?.enabled ? 'regime' : 'long'
  )
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
  const [slippageSource, setSlippageSource] = useState<'empirical' | 'default' | 'spread-derived' | 'manual'>('default')
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
  const [importingTab, setImportingTab] = useState<'regime' | 'long' | 'short' | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  // B25: per-direction settings (regime mode only)
  const [longStopLoss, setLongStopLoss] = useState<number | ''>(saved?.longStopLoss ?? '')
  const [shortStopLoss, setShortStopLoss] = useState<number | ''>(saved?.shortStopLoss ?? '')
  const [longTrailingEnabled, setLongTrailingEnabled] = useState<boolean>(saved?.longTrailingEnabled ?? false)
  const [longTrailingConfig, setLongTrailingConfig] = useState<TrailingStopConfig>(saved?.longTrailingConfig ?? trailingConfig)
  const [shortTrailingEnabled, setShortTrailingEnabled] = useState<boolean>(saved?.shortTrailingEnabled ?? false)
  const [shortTrailingConfig, setShortTrailingConfig] = useState<TrailingStopConfig>(saved?.shortTrailingConfig ?? trailingConfig)
  const [longMaxBarsHeld, setLongMaxBarsHeld] = useState<number | ''>(saved?.longMaxBarsHeld ?? '')
  const [shortMaxBarsHeld, setShortMaxBarsHeld] = useState<number | ''>(saved?.shortMaxBarsHeld ?? '')
  const [longPosSize, setLongPosSize] = useState<number>(saved?.longPosSize ?? posSize)
  const [shortPosSize, setShortPosSize] = useState<number>(saved?.shortPosSize ?? posSize)

  useEffect(() => {
    if (slippageSource === 'manual' || slippageSource === 'spread-derived') return
    if (slipInfo) {
      setSlippageBps(slipInfo.modeled_bps)
      setSlippageSource(slipInfo.source)
    }
  }, [slipInfo?.modeled_bps, slipInfo?.source, slippageSource])

  function currentSnapshot(name: string): SavedStrategy {
    const strategyType: SavedStrategy['strategyType'] =
      regimeEnabled === true ? 'regime' : direction === 'short' ? 'short' : 'long'
    return {
      name, savedAt: new Date().toISOString(),
      ticker, interval,
      buyRules, sellRules, buyLogic, sellLogic,
      longBuyRules, longSellRules, longBuyLogic, longSellLogic,
      shortBuyRules, shortSellRules, shortBuyLogic, shortSellLogic,
      capital, posSize, stopLoss, maxBarsHeld,
      trailingEnabled, trailingConfig, dynamicSizing, skipAfterStop, tradingHours,
      slippageBps, commission, direction,
      perShareRate, minPerOrder, borrowRateAnnual,
      regime: regimeEnabled ? { ...regimeConfig, rules: regimeBuyRules, logic: regimeLogic, enabled: true } : undefined,
      strategyType,
      // B25: per-direction settings
      longStopLoss, shortStopLoss,
      longTrailingEnabled, longTrailingConfig,
      shortTrailingEnabled, shortTrailingConfig,
      longMaxBarsHeld, shortMaxBarsHeld,
      longPosSize, shortPosSize,
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
    setLongBuyRules(s.longBuyRules ?? []); setLongSellRules(s.longSellRules ?? [])
    setShortBuyRules(s.shortBuyRules ?? []); setShortSellRules(s.shortSellRules ?? [])
    setLongBuyLogic(s.longBuyLogic ?? 'AND'); setLongSellLogic(s.longSellLogic ?? 'AND')
    setShortBuyLogic(s.shortBuyLogic ?? 'AND'); setShortSellLogic(s.shortSellLogic ?? 'AND')
    if (s.regime) {
      setRegimeEnabled(s.regime.enabled)
      setRegimeConfig(s.regime)
      setRegimeBuyRules(s.regime.rules ?? [])
      setRegimeLogic(s.regime.logic ?? 'AND')
      if (s.regime.enabled) setActiveRuleTab('regime')
    } else {
      setRegimeEnabled(false)
    }
    // B25: load per-direction settings (fall back to global/empty if not present)
    setLongStopLoss(s.longStopLoss ?? '')
    setShortStopLoss(s.shortStopLoss ?? '')
    setLongTrailingEnabled(s.longTrailingEnabled ?? false)
    setLongTrailingConfig(s.longTrailingConfig ?? s.trailingConfig)
    setShortTrailingEnabled(s.shortTrailingEnabled ?? false)
    setShortTrailingConfig(s.shortTrailingConfig ?? s.trailingConfig)
    setLongMaxBarsHeld(s.longMaxBarsHeld ?? '')
    setShortMaxBarsHeld(s.shortMaxBarsHeld ?? '')
    setLongPosSize(s.longPosSize ?? s.posSize)
    setShortPosSize(s.shortPosSize ?? s.posSize)
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

  function importFromStrategy(tab: 'regime' | 'long' | 'short', sourceName: string) {
    const source = savedStrategies.find(s => s.name === sourceName)
    if (!source) return

    let sourceBuy: Rule[]
    let sourceSell: Rule[]
    if (tab === 'long') {
      sourceBuy = source.longBuyRules?.length ? source.longBuyRules : source.buyRules
      sourceSell = source.longSellRules?.length ? source.longSellRules : source.sellRules
    } else if (tab === 'short') {
      sourceBuy = source.shortBuyRules?.length ? source.shortBuyRules : source.buyRules
      sourceSell = source.shortSellRules?.length ? source.shortSellRules : source.sellRules
    } else {
      // Regime tab: always use single-mode buyRules
      sourceBuy = source.buyRules
      sourceSell = source.sellRules
    }

    const migratedBuy = sourceBuy.map(migrateRule)
    const migratedSell = sourceSell.map(migrateRule)

    if (migratedBuy.length === 0) {
      setImportError(`"${sourceName}" has no entry rules — it may have been saved in regime mode. Try importing its sub-strategies instead.`)
      setImportingTab(null)
      return
    }

    const destCount = tab === 'regime' ? regimeBuyRules.length : tab === 'long' ? longBuyRules.length : shortBuyRules.length
    if (destCount > 0 && !window.confirm(`Replace ${destCount} existing rule${destCount > 1 ? 's' : ''} with rules from "${sourceName}"?`)) {
      setImportingTab(null)
      return
    }

    if (tab === 'regime') {
      setRegimeBuyRules(migratedBuy)
      setRegimeLogic(source.buyLogic ?? 'AND')
    } else if (tab === 'long') {
      setLongBuyRules(migratedBuy)
      setLongSellRules(migratedSell)
      setLongBuyLogic(source.longBuyLogic ?? source.buyLogic ?? 'AND')
      setLongSellLogic(source.longSellLogic ?? source.sellLogic ?? 'AND')
      // B25: also copy settings from source into long per-direction fields
      if (source.stopLoss !== '') setLongStopLoss(source.stopLoss ?? '')
      if (source.trailingEnabled) {
        setLongTrailingEnabled(true)
        setLongTrailingConfig(source.trailingConfig)
      }
      if (source.maxBarsHeld !== undefined && source.maxBarsHeld !== '') setLongMaxBarsHeld(source.maxBarsHeld)
      setLongPosSize(source.posSize ?? posSize)
    } else if (tab === 'short') {
      setShortBuyRules(migratedBuy)
      setShortSellRules(migratedSell)
      setShortBuyLogic(source.shortBuyLogic ?? source.buyLogic ?? 'AND')
      setShortSellLogic(source.shortSellLogic ?? source.sellLogic ?? 'AND')
      // B25: also copy settings from source into short per-direction fields
      if (source.stopLoss !== '') setShortStopLoss(source.stopLoss ?? '')
      if (source.trailingEnabled) {
        setShortTrailingEnabled(true)
        setShortTrailingConfig(source.trailingConfig)
      }
      if (source.maxBarsHeld !== undefined && source.maxBarsHeld !== '') setShortMaxBarsHeld(source.maxBarsHeld)
      setShortPosSize(source.posSize ?? posSize)
    }
    setImportError(null)
    setImportingTab(null)
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
      buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss, maxBarsHeld,
      trailingEnabled, trailingConfig, dynamicSizing, skipAfterStop, tradingHours, slippageBps, commission, direction,
      perShareRate, minPerOrder, borrowRateAnnual,
      regime: { ...regimeConfig, enabled: regimeEnabled, rules: regimeBuyRules, logic: regimeLogic },
      longBuyRules, longSellRules, longBuyLogic, longSellLogic,
      shortBuyRules, shortSellRules, shortBuyLogic, shortSellLogic,
      // B25: per-direction settings
      longStopLoss, shortStopLoss,
      longTrailingEnabled, longTrailingConfig,
      shortTrailingEnabled, shortTrailingConfig,
      longMaxBarsHeld, shortMaxBarsHeld,
      longPosSize, shortPosSize,
    }))
  }, [buyRules, sellRules, buyLogic, sellLogic, capital, posSize, stopLoss, maxBarsHeld, trailingEnabled, trailingConfig, dynamicSizing, skipAfterStop, tradingHours, slippageBps, commission, direction,
      perShareRate, minPerOrder, borrowRateAnnual, regimeEnabled, regimeConfig, regimeBuyRules, regimeLogic,
      longBuyRules, longSellRules, longBuyLogic, longSellLogic,
      shortBuyRules, shortSellRules, shortBuyLogic, shortSellLogic,
      longStopLoss, shortStopLoss,
      longTrailingEnabled, longTrailingConfig,
      shortTrailingEnabled, shortTrailingConfig,
      longMaxBarsHeld, shortMaxBarsHeld,
      longPosSize, shortPosSize])

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
        regime: regimeEnabled ? { ...regimeConfig, rules: regimeBuyRules, logic: regimeLogic, enabled: true } : undefined,
        ...(regimeEnabled ? {
          long_buy_rules: longBuyRules,
          long_sell_rules: longSellRules,
          long_buy_logic: longBuyLogic,
          long_sell_logic: longSellLogic,
          short_buy_rules: shortBuyRules,
          short_sell_rules: shortSellRules,
          short_buy_logic: shortBuyLogic,
          short_sell_logic: shortSellLogic,
        } : {}),
        // B25: per-direction settings (only sent when regime is active)
        ...(regimeEnabled ? {
          long_stop_loss_pct: longStopLoss !== '' ? longStopLoss : undefined,
          short_stop_loss_pct: shortStopLoss !== '' ? shortStopLoss : undefined,
          long_trailing_stop: longTrailingEnabled ? { ...trailingConfig, type: longTrailingConfig.type, value: longTrailingConfig.value } : undefined,
          short_trailing_stop: shortTrailingEnabled ? { ...trailingConfig, type: shortTrailingConfig.type, value: shortTrailingConfig.value } : undefined,
          long_max_bars_held: longMaxBarsHeld !== '' ? longMaxBarsHeld : undefined,
          short_max_bars_held: shortMaxBarsHeld !== '' ? shortMaxBarsHeld : undefined,
          long_position_size: longPosSize / 100,
          short_position_size: shortPosSize / 100,
        } : {}),
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
            {slipInfo?.half_spread_bps != null && slipInfo.half_spread_bps > 0 && slippageSource !== 'spread-derived' && (
              <button
                onClick={() => {
                  setSlippageBps(slipInfo.half_spread_bps!)
                  setSlippageSource('spread-derived')
                }}
                style={{ fontSize: 10, padding: '1px 6px', marginLeft: 6, cursor: 'pointer',
                  background: 'rgba(88,166,255,0.1)', border: '1px solid rgba(88,166,255,0.3)',
                  borderRadius: 3, color: 'var(--accent)' }}
                title={`Use half of live spread: ${slipInfo.half_spread_bps.toFixed(1)} bps`}
              >Use live spread</button>
            )}
            {slippageSource === 'spread-derived' && (
              <button
                onClick={() => setSlippageSource('default')}
                style={{ fontSize: 10, padding: '1px 6px', marginLeft: 6, cursor: 'pointer',
                  background: 'none', border: '1px solid rgba(128,128,128,0.3)',
                  borderRadius: 3, color: 'var(--text-muted)' }}
                title="Reset to modeled slippage"
              >↩ modeled</button>
            )}
            <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 6 }}>
              {slippageSource === 'empirical' && slipInfo
                ? `empirical: ${slipInfo.fill_count} fills`
                : slippageSource === 'spread-derived'
                ? 'live spread'
                : slippageSource === 'default'
                ? 'default: 2 bps'
                : 'manual'}
              {slipInfo?.live_spread_bps != null && (
                <span style={{ marginLeft: 8, color: 'var(--accent)' }}>
                  {`live spread: ${slipInfo.live_spread_bps.toFixed(1)} bps (½: ${(slipInfo.half_spread_bps ?? slipInfo.live_spread_bps / 2).toFixed(1)})`}
                </span>
              )}
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

        {/* Column 3: Per-Direction (Regime) — shown only when regimeEnabled */}
        {regimeEnabled && (
          <div style={styles.settingsGroup}>
            <div style={styles.groupTitle}>Per-Direction</div>
            {/* Long settings */}
            <div style={{ fontSize: 11, color: '#3fb950', fontWeight: 600, marginBottom: 4 }}>Long</div>
            <div style={styles.settingsRow}>
              <label style={styles.settingsLabel}>Stop Loss (%)</label>
              <input type="number" value={longStopLoss} step={0.5} min={0} placeholder="global" onChange={e => setLongStopLoss(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
            </div>
            <div style={styles.settingsRow}>
              <label style={styles.settingsLabel}>Time Stop (bars)</label>
              <input type="number" value={longMaxBarsHeld} step={1} min={1} placeholder="global" onChange={e => setLongMaxBarsHeld(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
            </div>
            <div style={styles.settingsRow}>
              <label style={styles.settingsLabel}>Position Size (%)</label>
              <input type="number" value={longPosSize} step={1} min={1} max={100} onChange={e => setLongPosSize(+e.target.value)} style={styles.settingsInput} />
            </div>
            <div style={{ ...styles.settingsRow, marginTop: 2 }}>
              <label style={{ ...styles.settingsLabel, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                <input type="checkbox" checked={longTrailingEnabled} onChange={e => setLongTrailingEnabled(e.target.checked)} />
                Trailing Stop
              </label>
            </div>
            {longTrailingEnabled && (
              <div style={{ paddingLeft: 12, borderLeft: '2px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={styles.settingsRow}>
                  <label style={styles.settingsLabel}>Type</label>
                  <select value={longTrailingConfig.type} onChange={e => setLongTrailingConfig(c => ({ ...c, type: e.target.value as 'pct' | 'atr' }))} style={styles.settingsInput}>
                    <option value="pct">%</option>
                    <option value="atr">ATR</option>
                  </select>
                </div>
                <div style={styles.settingsRow}>
                  <label style={styles.settingsLabel}>Value</label>
                  <input type="number" value={longTrailingConfig.value} step={0.5} min={0.1} onChange={e => setLongTrailingConfig(c => ({ ...c, value: +e.target.value }))} style={styles.settingsInput} />
                </div>
              </div>
            )}
            {/* Short settings */}
            <div style={{ fontSize: 11, color: '#f85149', fontWeight: 600, marginTop: 12, marginBottom: 4 }}>Short</div>
            <div style={styles.settingsRow}>
              <label style={styles.settingsLabel}>Stop Loss (%)</label>
              <input type="number" value={shortStopLoss} step={0.5} min={0} placeholder="global" onChange={e => setShortStopLoss(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
            </div>
            <div style={styles.settingsRow}>
              <label style={styles.settingsLabel}>Time Stop (bars)</label>
              <input type="number" value={shortMaxBarsHeld} step={1} min={1} placeholder="global" onChange={e => setShortMaxBarsHeld(e.target.value === '' ? '' : +e.target.value)} style={styles.settingsInput} />
            </div>
            <div style={styles.settingsRow}>
              <label style={styles.settingsLabel}>Position Size (%)</label>
              <input type="number" value={shortPosSize} step={1} min={1} max={100} onChange={e => setShortPosSize(+e.target.value)} style={styles.settingsInput} />
            </div>
            <div style={{ ...styles.settingsRow, marginTop: 2 }}>
              <label style={{ ...styles.settingsLabel, display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                <input type="checkbox" checked={shortTrailingEnabled} onChange={e => setShortTrailingEnabled(e.target.checked)} />
                Trailing Stop
              </label>
            </div>
            {shortTrailingEnabled && (
              <div style={{ paddingLeft: 12, borderLeft: '2px solid var(--border-light)', display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={styles.settingsRow}>
                  <label style={styles.settingsLabel}>Type</label>
                  <select value={shortTrailingConfig.type} onChange={e => setShortTrailingConfig(c => ({ ...c, type: e.target.value as 'pct' | 'atr' }))} style={styles.settingsInput}>
                    <option value="pct">%</option>
                    <option value="atr">ATR</option>
                  </select>
                </div>
                <div style={styles.settingsRow}>
                  <label style={styles.settingsLabel}>Value</label>
                  <input type="number" value={shortTrailingConfig.value} step={0.5} min={0.1} onChange={e => setShortTrailingConfig(c => ({ ...c, value: +e.target.value }))} style={styles.settingsInput} />
                </div>
              </div>
            )}
          </div>
        )}

        {/* Column 4: Execution */}
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
            {(['regime', 'long', 'short'] as const).map(type => {
              const group = sortedStrategies.filter(s => (s.strategyType ?? 'long') === type)
              if (group.length === 0) return null
              const label = type === 'regime' ? 'Regime' : type === 'short' ? 'Short' : 'Long'
              return (
                <optgroup key={type} label={label}>
                  {group.map(s => (
                    <option key={s.name} value={s.name}>{s.pinned ? '★ ' : ''}{s.name}</option>
                  ))}
                </optgroup>
              )
            })}
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
            Direction: <span style={{ color: '#58a6ff' }}>{direction}</span> entry · {
              regimeConfig.on_flip === 'close_and_reverse'
                ? (shortBuyRules.some(r => r.indicator)
                  ? <>reverses to <span style={{ color: '#8b949e' }}>{direction === 'long' ? 'short' : 'long'}</span> on flip</>
                  : 'goes flat on flip (no short rules)')
                : (shortBuyRules.some(r => r.indicator)
                  ? 'goes flat on flip, re-enters on signal'
                  : 'goes flat when regime inactive')
            }
          </div>
        )}

        {/* Regime filter */}
        <div style={{ padding: '6px 16px 4px', borderBottom: '1px solid #21262d' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: regimeEnabled ? 8 : 0 }}>
            <button
              onClick={() => setRegimeEnabled((v: boolean) => {
                if (!v) {
                  setActiveRuleTab('regime');
                  if (buyRules.length > 0 && buyRules.some(r => r.indicator) && !longBuyRules.some(r => r.indicator)) {
                    setLongBuyRules([...buyRules]);
                    setLongSellRules([...sellRules]);
                    setLongBuyLogic(buyLogic);
                    setLongSellLogic(sellLogic);
                  }
                }
                return !v;
              })}
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
                {(() => { const fl: Record<string, string> = { close_only: 'close·wait', close_and_reverse: 'close·enter', hold: 'hold' }; const fp = fl[regimeConfig.on_flip ?? 'close_only'] ?? regimeConfig.on_flip; return regimeBuyRules.length > 0
                  ? `${regimeBuyRules.length} rule${regimeBuyRules.length > 1 ? 's' : ''} · ${regimeConfig.timeframe} · ${regimeConfig.min_bars}b · ${fp}`
                  : regimeConfig.indicator
                    ? `${regimeConfig.indicator.toUpperCase()}(${(regimeConfig.indicator_params as Record<string, unknown>).period as number}) ${regimeConfig.condition} · ${regimeConfig.timeframe} · ${regimeConfig.min_bars}b · ${fp}`
                    : 'No regime rules configured'
                })()}
              </span>
            )}
          </div>
          {regimeEnabled && !stopLoss && direction === 'long' && (
            <div style={{ paddingBottom: 6 }}>
              <span style={{ fontSize: 10, color: '#f0883e' }}>⚠ Add a stop-loss to limit open-position risk during flat periods</span>
            </div>
          )}
        </div>

        {/* B28/B23: tab selector for regime / long / short / single rule sets */}
        {regimeEnabled && (
          <div style={{ display: 'flex', gap: 4, padding: '6px 16px 0', borderBottom: '1px solid #21262d' }}>
            {(['regime', 'long', 'short'] as const).map(tab => (
              <button key={tab} onClick={() => setActiveRuleTab(tab)}
                style={{
                  fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 4, border: 'none',
                  cursor: 'pointer', textTransform: 'uppercase',
                  background: activeRuleTab === tab ? '#1a2a3a' : '#161b22',
                  color: activeRuleTab === tab
                    ? (tab === 'long' ? '#3fb950' : tab === 'short' ? '#f85149' : '#58a6ff')
                    : '#555',
                }}>
                {tab === 'long' ? '▲ Long' : tab === 'short' ? '▼ Short' : tab === 'regime' ? 'Regime Rules' : 'Single'}
              </button>
            ))}
          </div>
        )}

        <div style={styles.panels}>
          {/* B28: Regime Rules panel */}
          {regimeEnabled && activeRuleTab === 'regime' && (
          <div style={styles.panel}>
            <div style={styles.panelHeader}>
              <span style={{ color: '#58a6ff', fontWeight: 600 }}>Regime active when</span>
              <div style={styles.logicToggle}>
                {(['AND', 'OR'] as const).map(l => (
                  <button key={l} onClick={() => setRegimeLogic(l)} style={{ ...styles.logicBtn, ...(regimeLogic === l ? styles.logicBtnActive : {}) }}>{l}</button>
                ))}
              </div>
              <button onClick={() => setRegimeBuyRules(r => [...r, emptyRule()])} style={styles.addBtn}><Plus size={13} /> Add</button>
              <button onClick={() => setImportingTab(importingTab === 'regime' ? null : 'regime')} style={styles.addBtn}>Import</button>
              {importingTab === 'regime' && (
                <select autoFocus defaultValue=""
                  onChange={e => { if (e.target.value) importFromStrategy('regime', e.target.value) }}
                  onBlur={() => setTimeout(() => setImportingTab(null), 0)}
                  style={{ fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}>
                  <option value="">— pick strategy —</option>
                  {sortedStrategies.map(s => (
                    <option key={s.name} value={s.name}>{s.name}{s.interval ? ` (${s.interval})` : ''}</option>
                  ))}
                </select>
              )}
            </div>
            {/* Meta-controls: timeframe, on_flip, min_bars — shown ABOVE rules for clarity */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8, alignItems: 'center', borderBottom: '1px solid #21262d', paddingBottom: 8 }}>
              <label style={{ fontSize: 11, color: '#8b949e' }}>Timeframe</label>
              <select value={regimeConfig.timeframe} onChange={e => setRegimeConfig(c => ({ ...c, timeframe: e.target.value }))}
                style={{ fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}>
                {['1d', '1wk', '1mo', '4h', '1h', '15m'].map(tf => <option key={tf} value={tf}>{tf}</option>)}
              </select>
              <label style={{ fontSize: 11, color: '#8b949e', marginLeft: 4 }}>On flip</label>
              <select value={regimeConfig.on_flip ?? 'close_only'} onChange={e => setRegimeConfig(c => ({ ...c, on_flip: e.target.value as RegimeConfig['on_flip'] }))}
                style={{ fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}>
                <option value="close_only">Close, wait for signal</option>
                <option value="close_and_reverse">Close, enter immediately</option>
                <option value="hold">Hold (block new entries)</option>
              </select>
              <label style={{ fontSize: 11, color: '#8b949e', marginLeft: 4 }}>Min bars</label>
              <input type="number" min={1} max={50} value={regimeConfig.min_bars ?? 1}
                onChange={e => setRegimeConfig(c => ({ ...c, min_bars: +e.target.value }))}
                style={{ width: 46, fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }} />
            </div>
            {regimeBuyRules.map((r, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <RuleRow rule={r}
                  onChange={nr => setRegimeBuyRules(rules => rules.map((x, j) => j === i ? nr : x))}
                  onDelete={() => setRegimeBuyRules(rules => rules.filter((_, j) => j !== i))} />
                <span style={{ fontSize: 10, color: '#58a6ff', background: '#161b22', border: '1px solid #30363d', borderRadius: 3, padding: '1px 4px', whiteSpace: 'nowrap' }}>{regimeConfig.timeframe}</span>
              </div>
            ))}
          </div>
          )}

          {/* BUY — single mode or regime disabled */}
          {!regimeEnabled && (<>
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
                onDelete={() => setBuyRules(rules => rules.filter((_, j) => j !== i))}
                onSweep={onSweep && typeof r.value === 'number' ? () => onSweep(`buy_rule_${i}_value`, r.value as number) : undefined} />
            ))}
          </div>

          {/* SELL — single mode */}
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
                onDelete={() => setSellRules(rules => rules.filter((_, j) => j !== i))}
                onSweep={onSweep && typeof r.value === 'number' ? () => onSweep(`sell_rule_${i}_value`, r.value as number) : undefined} />
            ))}
          </div>
          </>)}

          {/* Long rules tab (regime active = bullish) */}
          {regimeEnabled && activeRuleTab === 'long' && (<>
          <div style={styles.panel}>
            <div style={styles.panelHeader}>
              <span style={{ color: '#3fb950', fontWeight: 600 }}>▲ Long Entry when</span>
              <div style={styles.logicToggle}>
                {(['AND', 'OR'] as const).map(l => (
                  <button key={l} onClick={() => setLongBuyLogic(l)} style={{ ...styles.logicBtn, ...(longBuyLogic === l ? styles.logicBtnActive : {}) }}>{l}</button>
                ))}
              </div>
              <button onClick={() => setLongBuyRules(r => [...r, emptyRule()])} style={styles.addBtn}><Plus size={13} /> Add</button>
              <button onClick={() => setImportingTab(importingTab === 'long' ? null : 'long')} style={styles.addBtn}>Import</button>
              {importingTab === 'long' && (
                <select autoFocus defaultValue=""
                  onChange={e => { if (e.target.value) importFromStrategy('long', e.target.value) }}
                  onBlur={() => setTimeout(() => setImportingTab(null), 0)}
                  style={{ fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}>
                  <option value="">— pick strategy —</option>
                  {sortedStrategies.map(s => (
                    <option key={s.name} value={s.name}>{s.name}{s.interval ? ` (${s.interval})` : ''}</option>
                  ))}
                </select>
              )}
            </div>
            {longBuyRules.map((r, i) => (
              <RuleRow key={i} rule={r}
                onChange={nr => setLongBuyRules(rules => rules.map((x, j) => j === i ? nr : x))}
                onDelete={() => setLongBuyRules(rules => rules.filter((_, j) => j !== i))} />
            ))}
            {(longBuyRules.length === 0 || longBuyRules.every(r => !r.indicator)) && (
              <div style={{ padding: '6px 12px 8px', fontSize: 11, color: '#8b949e', fontStyle: 'italic' }}>
                No long entry rules — no long positions will open. Exits/stops still run on existing positions.
              </div>
            )}
          </div>
          <div style={styles.panel}>
            <div style={styles.panelHeader}>
              <span style={{ color: 'var(--accent-red)', fontWeight: 600 }}>▲ Long Exit when</span>
              <div style={styles.logicToggle}>
                {(['AND', 'OR'] as const).map(l => (
                  <button key={l} onClick={() => setLongSellLogic(l)} style={{ ...styles.logicBtn, ...(longSellLogic === l ? styles.logicBtnActive : {}) }}>{l}</button>
                ))}
              </div>
              <button onClick={() => setLongSellRules(r => [...r, emptyRule()])} style={styles.addBtn}><Plus size={13} /> Add</button>
            </div>
            {longSellRules.map((r, i) => (
              <RuleRow key={i} rule={r}
                onChange={nr => setLongSellRules(rules => rules.map((x, j) => j === i ? nr : x))}
                onDelete={() => setLongSellRules(rules => rules.filter((_, j) => j !== i))} />
            ))}
          </div>
          </>)}

          {/* Short rules tab (regime inactive = bearish) */}
          {regimeEnabled && activeRuleTab === 'short' && (<>
          <div style={styles.panel}>
            <div style={styles.panelHeader}>
              <span style={{ color: '#f85149', fontWeight: 600 }}>▼ Short Entry when</span>
              <div style={styles.logicToggle}>
                {(['AND', 'OR'] as const).map(l => (
                  <button key={l} onClick={() => setShortBuyLogic(l)} style={{ ...styles.logicBtn, ...(shortBuyLogic === l ? styles.logicBtnActive : {}) }}>{l}</button>
                ))}
              </div>
              <button onClick={() => setShortBuyRules(r => [...r, emptyRule()])} style={styles.addBtn}><Plus size={13} /> Add</button>
              <button onClick={() => setImportingTab(importingTab === 'short' ? null : 'short')} style={styles.addBtn}>Import</button>
              {importingTab === 'short' && (
                <select autoFocus defaultValue=""
                  onChange={e => { if (e.target.value) importFromStrategy('short', e.target.value) }}
                  onBlur={() => setTimeout(() => setImportingTab(null), 0)}
                  style={{ fontSize: 11, background: '#161b22', color: '#c9d1d9', border: '1px solid #30363d', borderRadius: 4, padding: '2px 4px' }}>
                  <option value="">— pick strategy —</option>
                  {sortedStrategies.map(s => (
                    <option key={s.name} value={s.name}>{s.name}{s.interval ? ` (${s.interval})` : ''}</option>
                  ))}
                </select>
              )}
            </div>
            {shortBuyRules.map((r, i) => (
              <RuleRow key={i} rule={r}
                onChange={nr => setShortBuyRules(rules => rules.map((x, j) => j === i ? nr : x))}
                onDelete={() => setShortBuyRules(rules => rules.filter((_, j) => j !== i))} />
            ))}
            {(shortBuyRules.length === 0 || shortBuyRules.every(r => !r.indicator)) && (
              <div style={{ padding: '6px 12px 8px', fontSize: 11, color: '#8b949e', fontStyle: 'italic' }}>
                No short entry rules — no short positions will open. Exits/stops still run on existing positions.
              </div>
            )}
          </div>
          <div style={styles.panel}>
            <div style={styles.panelHeader}>
              <span style={{ color: 'var(--accent-red)', fontWeight: 600 }}>▼ Short Exit when</span>
              <div style={styles.logicToggle}>
                {(['AND', 'OR'] as const).map(l => (
                  <button key={l} onClick={() => setShortSellLogic(l)} style={{ ...styles.logicBtn, ...(shortSellLogic === l ? styles.logicBtnActive : {}) }}>{l}</button>
                ))}
              </div>
              <button onClick={() => setShortSellRules(r => [...r, emptyRule()])} style={styles.addBtn}><Plus size={13} /> Add</button>
            </div>
            {shortSellRules.map((r, i) => (
              <RuleRow key={i} rule={r}
                onChange={nr => setShortSellRules(rules => rules.map((x, j) => j === i ? nr : x))}
                onDelete={() => setShortSellRules(rules => rules.filter((_, j) => j !== i))} />
            ))}
          </div>
          </>)}
        </div>

        {regimeEnabled && regimeConfig.on_flip === 'close_and_reverse' && (!longBuyRules.some(r => r.indicator) || !shortBuyRules.some(r => r.indicator)) && (
          <div style={{ color: '#d29922', fontSize: 11, padding: '0 16px 4px' }}>
            Close &amp; enter requires both Long and Short rules. {!longBuyRules.some(r => r.indicator) ? 'Long' : 'Short'} tab has no rules — will go flat instead.
          </div>
        )}
        {regimeEnabled && buyRules.some(r => r.indicator) && longBuyRules.some(r => r.indicator) && shortBuyRules.some(r => r.indicator) && (
          <div style={{ color: '#8b949e', fontSize: 11, padding: '0 16px 4px' }}>
            Single tab rules are inactive — Long/Short rules take precedence.
          </div>
        )}
        {importError && (
          <div style={{ color: 'var(--accent-red)', fontSize: 11, padding: '0 16px 4px' }}>{importError}</div>
        )}
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
