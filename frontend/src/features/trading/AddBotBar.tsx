import { useEffect, useState } from 'react'
import type { BotFundStatus, SavedStrategy } from '../../shared/types'
import { fmtUsd } from '../../shared/utils/format'
import { apiErrorDetail } from '../../shared/utils/errors'
import { btnStyle } from './ui'

const SAVED_KEY = 'strategylab-saved-strategies'
const INTERVALS = ['1m', '5m', '15m', '30m', '1h']

export const sectionStyle: React.CSSProperties = {
  background: '#161b22',
  border: '1px solid #1e2530',
  borderRadius: 6,
  padding: '10px 12px',
}

export const inputStyle: React.CSSProperties = {
  background: '#0d1117',
  border: '1px solid #2a3040',
  borderRadius: 4,
  color: '#e6edf3',
  padding: '4px 8px',
  fontSize: 12,
}

export default function AddBotBar({
  fund, onAdd,
}: {
  fund: BotFundStatus | null
  onAdd: (bot: any) => void
}) {
  const [strategies, setStrategies] = useState<SavedStrategy[]>([])
  const [selectedIdx, setSelectedIdx] = useState(-1)
  const [symbol, setSymbol] = useState('')
  const [interval, setInterval] = useState('15m')
  const [allocation, setAllocation] = useState('')
  const [dataSource, setDataSource] = useState('alpaca-iex')
  const [broker, setBroker] = useState<'alpaca' | 'ibkr'>('alpaca')
  const [direction, setDirection] = useState<'long' | 'short'>('long')
  const [maxSpreadBps, setMaxSpreadBps] = useState('50')
  const [error, setError] = useState('')

  const loadStrategies = () => {
    try {
      const raw = localStorage.getItem(SAVED_KEY)
      if (raw) setStrategies(JSON.parse(raw))
    } catch {}
  }

  useEffect(() => {
    loadStrategies()
    // Check for pending spawn from Discovery tab
    try {
      const pending = localStorage.getItem('strategylab-pending-spawn')
      if (pending) {
        localStorage.removeItem('strategylab-pending-spawn')
        const { symbol: pendingSymbol, strategyName } = JSON.parse(pending)
        const raw = localStorage.getItem(SAVED_KEY)
        const strats: SavedStrategy[] = raw ? JSON.parse(raw) : []
        const idx = strats.findIndex(s => s.name === strategyName)
        if (idx >= 0) {
          setStrategies(strats)
          setSelectedIdx(idx)
          setSymbol(pendingSymbol ?? strats[idx].ticker ?? '')
          setInterval(strats[idx].interval ?? '15m')
        } else if (pendingSymbol) {
          setSymbol(pendingSymbol)
        }
      }
    } catch {}
  }, [])

  const onStrategyChange = (idx: number) => {
    setSelectedIdx(idx)
    if (idx >= 0 && strategies[idx]) {
      const s = strategies[idx]
      setSymbol(s.ticker ?? '')
      setInterval(s.interval ?? '15m')
    }
  }

  const available = fund?.available ?? 0
  const canAdd = fund && fund.bot_fund > 0 && available > 0 && selectedIdx >= 0 && symbol && allocation

  const handleAdd = async () => {
    setError('')
    const alloc = parseFloat(allocation)
    if (isNaN(alloc) || alloc <= 0) { setError('Enter a valid allocation'); return }
    if (alloc > available) { setError(`Max available: ${fmtUsd(available)}`); return }
    const s = strategies[selectedIdx]
    try {
      await onAdd({
        strategy_name: s.name,
        symbol: symbol.toUpperCase(),
        interval,
        buy_rules: s.buyRules,
        sell_rules: s.sellRules,
        buy_logic: s.buyLogic ?? 'AND',
        sell_logic: s.sellLogic ?? 'AND',
        allocated_capital: alloc,
        position_size: 1.0,
        stop_loss_pct: typeof s.stopLoss === 'number' ? s.stopLoss : null,
        max_bars_held: typeof s.maxBarsHeld === 'number' ? s.maxBarsHeld : null,
        trailing_stop: s.trailingEnabled ? s.trailingConfig : null,
        dynamic_sizing: s.dynamicSizing ?? null,
        skip_after_stop: s.skipAfterStop ?? null,
        trading_hours: s.tradingHours ?? null,
        slippage_bps: typeof s.slippageBps === 'number' ? s.slippageBps : 2.0,
        max_spread_bps: maxSpreadBps ? parseFloat(maxSpreadBps) || null : null,
        data_source: dataSource,
        direction,
        broker,
      })
      setAllocation('')
    } catch (e) {
      setError(apiErrorDetail(e, 'Failed to add bot'))
    }
  }

  return (
    <div style={{ ...sectionStyle, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {/* Strategy dropdown */}
        <select
          value={selectedIdx}
          onChange={e => onStrategyChange(Number(e.target.value))}
          onFocus={loadStrategies}
          style={{ ...inputStyle, minWidth: 160 }}
        >
          <option value={-1}>Select strategy…</option>
          {strategies.map((s, i) => (
            <option key={i} value={i}>{s.name}</option>
          ))}
        </select>

        {/* Ticker */}
        <input
          placeholder="Ticker"
          value={symbol}
          onChange={e => setSymbol(e.target.value.toUpperCase())}
          style={{ ...inputStyle, width: 70 }}
        />

        {/* Interval */}
        <select value={interval} onChange={e => setInterval(e.target.value)} style={inputStyle}>
          {INTERVALS.map(v => <option key={v} value={v}>{v}</option>)}
        </select>

        {/* Data source — where OHLCV bars come from for signal evaluation */}
        <select
          value={dataSource}
          onChange={e => setDataSource(e.target.value)}
          style={inputStyle}
          title="Data source — where the bot fetches price bars to evaluate its rules"
        >
          <option value="alpaca-iex">data: IEX</option>
          <option value="alpaca">data: Alpaca SIP</option>
          <option value="ibkr">data: IBKR</option>
          <option value="yahoo">data: Yahoo</option>
        </select>

        {/* Broker (executes orders) */}
        <select
          value={broker}
          onChange={e => setBroker(e.target.value as 'alpaca' | 'ibkr')}
          style={inputStyle}
          title="Broker — which account executes the trades"
        >
          <option value="alpaca">via Alpaca</option>
          <option value="ibkr">via IBKR</option>
        </select>

        {/* Direction */}
        <select value={direction} onChange={e => setDirection(e.target.value as 'long' | 'short')} style={inputStyle}>
          <option value="long">Long</option>
          <option value="short">Short</option>
        </select>

        {/* Allocation */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <input
            type="number"
            placeholder="Allocation $"
            value={allocation}
            onChange={e => setAllocation(e.target.value)}
            max={available}
            style={{ ...inputStyle, width: 110 }}
          />
          {fund && fund.bot_fund > 0 && (
            <span style={{ fontSize: 11, color: '#555', whiteSpace: 'nowrap' }}>
              / {fmtUsd(available)}
            </span>
          )}
        </div>

        {/* Max Spread */}
        <input
          type="number"
          placeholder="Max Spread bps"
          value={maxSpreadBps}
          min={0}
          onChange={e => setMaxSpreadBps(e.target.value)}
          style={{ ...inputStyle, width: 70 }}
          title="Skip entries when bid/ask spread exceeds this (bps). Empty = disabled."
        />

        <button
          onClick={handleAdd}
          disabled={!canAdd}
          style={btnStyle('#1e3a5f', !canAdd)}
        >
          + Add Bot
        </button>
      </div>

      {error && <span style={{ color: '#ef5350', fontSize: 12 }}>{error}</span>}
    </div>
  )
}
