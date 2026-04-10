import { useEffect, useRef, useState } from 'react'
import { createChart, BaselineSeries } from 'lightweight-charts'
import type {
  BotSummary, BotDetail, BotFundStatus, BotActivityEntry, SavedStrategy,
} from '../../shared/types'
import {
  listBots, fetchBotDetail, setBotFund, addBot,
  startBot, stopBot, backtestBot, deleteBot, manualBuyBot, updateBot,
} from '../../api/bots'
import { fmtTimeET } from '../../shared/utils/time'

const SAVED_KEY = 'strategylab-saved-strategies'
const INTERVALS = ['1m', '5m', '15m', '30m', '1h']

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtUsd(n: number) {
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

function fmtPnl(n: number) {
  const s = Math.abs(n).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
  return n >= 0 ? `+${s}` : `-${s}`
}

function statusColor(status: string) {
  if (status === 'running') return '#26a69a'
  if (status === 'error') return '#ef5350'
  if (status === 'backtesting') return '#f0b429'
  return '#555'
}

function levelColor(level: BotActivityEntry['level']) {
  if (level === 'TRADE') return '#26a69a'
  if (level === 'ERROR') return '#ef5350'
  if (level === 'WARN') return '#f0b429'
  return '#aaa'
}

// ---------------------------------------------------------------------------
// MiniSparkline — lightweight-charts BaselineSeries in a tiny container
// ---------------------------------------------------------------------------

function MiniSparkline({ equityData }: { equityData: { time: string; value: number }[] }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current || equityData.length < 2) return
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: 60,
      layout: { background: { color: 'transparent' }, textColor: '#aaa' },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      leftPriceScale: { visible: false },
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
      handleScroll: false,
      handleScale: false,
    })
    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      topLineColor: '#26a69a',
      topFillColor1: 'rgba(38,166,154,0.2)',
      topFillColor2: 'rgba(38,166,154,0.02)',
      bottomLineColor: '#ef5350',
      bottomFillColor1: 'rgba(239,83,80,0.02)',
      bottomFillColor2: 'rgba(239,83,80,0.2)',
      lineWidth: 1,
      priceScaleId: 'right',
    })
    const mapped = equityData.map((d, i) => ({ time: i + 1, value: d.value })) as any
    series.setData(mapped)
    chart.timeScale().fitContent()

    const ro = new ResizeObserver(() => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth })
    })
    ro.observe(ref.current)

    return () => { ro.disconnect(); chart.remove() }
  }, [equityData])

  if (equityData.length < 2) return null
  return <div ref={ref} style={{ width: '100%', height: 60 }} />
}

// ---------------------------------------------------------------------------
// ActivityLog
// ---------------------------------------------------------------------------

function ActivityLog({ entries }: { entries: BotActivityEntry[] }) {
  return (
    <div style={{
      maxHeight: 160, overflowY: 'auto', background: '#0d1117',
      border: '1px solid #1e2530', borderRadius: 4, padding: '6px 8px',
      fontFamily: 'monospace', fontSize: 11,
    }}>
      {entries.length === 0 && <span style={{ color: '#555' }}>No activity yet.</span>}
      {entries.map((e, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 2 }}>
          <span style={{ color: '#444', flexShrink: 0 }}>
            {fmtTimeET(e.time)}
          </span>
          <span style={{ color: levelColor(e.level), flexShrink: 0 }}>[{e.level}]</span>
          <span style={{ color: '#ccc' }}>{e.msg}</span>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// BotCard
// ---------------------------------------------------------------------------

function BotCard({
  summary,
  onStart, onStop, onBacktest, onDelete, onManualBuy, onUpdate,
}: {
  summary: BotSummary
  onStart: () => void
  onStop: () => void
  onBacktest: () => void
  onDelete: () => void
  onManualBuy: () => void
  onUpdate: (updates: Record<string, unknown>) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState<BotDetail | null>(null)
  const [editingAlloc, setEditingAlloc] = useState(false)
  const [allocValue, setAllocValue] = useState('')
  const [editingStrategy, setEditingStrategy] = useState(false)
  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const d = await fetchBotDetail(summary.bot_id)
        if (active) setDetail(d)
      } catch {}
    }
    load()
    if (running || expanded) {
      const id = setInterval(load, 2000)
      return () => { active = false; clearInterval(id) }
    }
    return () => { active = false }
  }, [expanded, running, summary.bot_id])

  const running = summary.status === 'running'
  const stopped = summary.status === 'stopped'
  const pnlColor = summary.total_pnl >= 0 ? '#26a69a' : '#ef5350'

  const dir = summary.direction ?? 'long'
  const bgTint = dir === 'short' ? 'rgba(200, 0, 0, 0.03)' : 'rgba(0, 200, 0, 0.03)'

  return (
    <div style={{
      background: `linear-gradient(135deg, ${bgTint}, #161b22)`, border: '1px solid #1e2530', borderRadius: 6,
      padding: 12, display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      {/* Two-column layout */}
      <div style={{ display: 'flex', gap: 12 }}>
        {/* Left column */}
        <div style={{ flex: '0 0 auto', display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
          {/* Header row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* Status dot */}
            <div style={{
              width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
              background: statusColor(summary.status),
              boxShadow: running ? `0 0 6px ${statusColor(summary.status)}` : 'none',
            }} />
            <span style={{ color: '#e6edf3', fontWeight: 600, flex: 1 }}>
              {editingStrategy ? (
                <select
                  autoFocus
                  defaultValue={-1}
                  onChange={e => {
                    const idx = Number(e.target.value)
                    if (idx >= 0) {
                      try {
                        const strats: SavedStrategy[] = JSON.parse(localStorage.getItem(SAVED_KEY) || '[]')
                        const s = strats[idx]
                        if (s) onUpdate({
                          strategy_name: s.name,
                          buy_rules: s.buyRules,
                          sell_rules: s.sellRules,
                          buy_logic: s.buyLogic ?? 'AND',
                          sell_logic: s.sellLogic ?? 'AND',
                        })
                      } catch {}
                    }
                    setEditingStrategy(false)
                  }}
                  onBlur={() => setEditingStrategy(false)}
                  style={{ fontSize: 12, background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 3 }}
                >
                  <option value={-1}>Select strategy…</option>
                  {(() => { try { return JSON.parse(localStorage.getItem(SAVED_KEY) || '[]') } catch { return [] } })()
                    .map((s: SavedStrategy, i: number) => <option key={i} value={i}>{s.name}</option>)}
                </select>
              ) : (
                <span
                  style={{ cursor: stopped ? 'pointer' : 'default', borderBottom: stopped ? '1px dashed #58a6ff' : 'none' }}
                  onClick={() => { if (stopped) setEditingStrategy(true) }}
                  title={stopped ? 'Click to change strategy' : 'Stop bot to edit'}
                >{summary.strategy_name}</span>
              )}
              <span style={{
                fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 3,
                marginLeft: 6,
                background: dir === 'short' ? 'rgba(239,83,80,0.15)' : 'rgba(38,166,154,0.15)',
                color: dir === 'short' ? '#ef5350' : '#26a69a',
                textTransform: 'uppercase', letterSpacing: 0.5,
              }}>
                {dir}
              </span>
            </span>
            <span style={{ color: '#888', fontSize: 12 }}>
              {summary.symbol} · {summary.interval} · {summary.data_source ?? 'alpaca-iex'}
            </span>
          </div>

          {/* Stats row */}
          <div style={{ display: 'flex', gap: 16, fontSize: 12 }}>
            <span style={{ color: '#666' }}>Allocated: {editingAlloc ? (
              <input
                autoFocus
                type="number"
                value={allocValue}
                onChange={e => setAllocValue(e.target.value)}
                onBlur={() => {
                  const v = parseFloat(allocValue)
                  if (!isNaN(v) && v > 0) onUpdate({ allocated_capital: v })
                  setEditingAlloc(false)
                }}
                onKeyDown={e => {
                  if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                  if (e.key === 'Escape') setEditingAlloc(false)
                }}
                style={{ width: 80, fontSize: 12, background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 3, padding: '1px 4px' }}
              />
            ) : (
              <span
                style={{ color: stopped ? '#58a6ff' : '#aaa', cursor: stopped ? 'pointer' : 'default', borderBottom: stopped ? '1px dashed #58a6ff' : 'none' }}
                onClick={() => { if (stopped) { setAllocValue(String(summary.allocated_capital)); setEditingAlloc(true) } }}
                title={stopped ? 'Click to edit' : 'Stop bot to edit'}
              >{fmtUsd(summary.allocated_capital)}</span>
            )}</span>
            <span style={{ color: '#666' }}>Trades: <span style={{ color: '#aaa' }}>{summary.trades_count}</span></span>
            <span style={{ color: '#666' }}>P&L: <span style={{ color: pnlColor }}>{fmtPnl(summary.total_pnl)}</span></span>
            <span style={{ color: '#666', textTransform: 'capitalize' }}>
              Status: <span style={{ color: statusColor(summary.status) }}>{summary.status}</span>
            </span>
            {summary.avg_slippage_pct != null && (
              <span style={{ color: '#666' }}>Slippage: <span style={{ color: Math.abs(summary.avg_slippage_pct) > 0.05 ? '#f85149' : '#8b949e' }}>{summary.avg_slippage_pct.toFixed(3)}%</span></span>
            )}
          </div>

          {/* Backtest summary (always visible if available) */}
          {summary.backtest_summary && (
            <div style={{ display: 'flex', gap: 12, fontSize: 11, color: '#666' }}>
              {(() => {
                const s = summary.backtest_summary as any
                return <>
                  <span>BT Return: <span style={{ color: '#aaa' }}>{s.total_return_pct?.toFixed(1)}%</span></span>
                  <span>Sharpe: <span style={{ color: '#aaa' }}>{s.sharpe_ratio?.toFixed(2)}</span></span>
                  <span>MDD: <span style={{ color: '#ef5350' }}>{s.max_drawdown_pct?.toFixed(1)}%</span></span>
                </>
              })()}
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <button onClick={onBacktest} disabled={running} style={btnStyle('#1e3a5f', running)}>
              Backtest
            </button>
            {stopped ? (
              <button onClick={onStart} style={btnStyle('#1a3a2a')}>Start</button>
            ) : (
              <button onClick={onStop} style={btnStyle('#3a1a1a')}>Stop</button>
            )}
            <button
              onClick={onManualBuy}
              disabled={!running || summary.has_position}
              style={btnStyle('#1a3a2a', !running || summary.has_position)}
            >{dir === 'short' ? 'Short' : 'Buy'}</button>
            <button
              onClick={() => setExpanded(e => !e)}
              style={btnStyle('#1e2530')}
            >
              {expanded ? 'Hide Log' : 'Show Log'}
            </button>
            {stopped && (
              <button onClick={onDelete} style={btnStyle('#3a1a1a')}>Delete</button>
            )}
          </div>
        </div>

        {/* Right column: mini chart */}
        <div style={{ flex: 1, minWidth: 120, minHeight: 60 }}>
          <MiniSparkline equityData={detail?.state.equity_snapshots ?? []} />
        </div>
      </div>

      {/* Expandable activity log */}
      {expanded && (
        <ActivityLog entries={detail?.state.activity_log ?? []} />
      )}
    </div>
  )
}

function btnStyle(bg: string, disabled = false): React.CSSProperties {
  return {
    background: disabled ? '#1a1a1a' : bg,
    color: disabled ? '#444' : '#ccc',
    border: '1px solid #2a3040',
    borderRadius: 4,
    padding: '4px 10px',
    fontSize: 12,
    cursor: disabled ? 'not-allowed' : 'pointer',
  }
}

// ---------------------------------------------------------------------------
// FundBar
// ---------------------------------------------------------------------------

function FundBar({ fund, onSetFund }: { fund: BotFundStatus | null; onSetFund: (n: number) => void }) {
  const [editing, setEditing] = useState(false)
  const [input, setInput] = useState('')

  const handleSet = () => {
    const n = parseFloat(input)
    if (!isNaN(n) && n >= 0) { onSetFund(n); setEditing(false) }
  }

  if (!fund || fund.bot_fund === 0) {
    return (
      <div style={{ ...sectionStyle, display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ color: '#888', fontSize: 13 }}>Set your bot fund to get started:</span>
        <input
          type="number" placeholder="e.g. 50000"
          value={input} onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSet()}
          style={inputStyle}
        />
        <button onClick={handleSet} style={btnStyle('#1e3a5f')}>Set Fund</button>
      </div>
    )
  }

  const allocPct = fund.bot_fund > 0 ? (fund.allocated / fund.bot_fund) * 100 : 0

  return (
    <div style={{ ...sectionStyle, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <span style={{ color: '#e6edf3', fontWeight: 600, fontSize: 13 }}>Bot Fund</span>
        {editing ? (
          <>
            <input type="number" value={input} onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSet()} style={inputStyle} autoFocus />
            <button onClick={handleSet} style={btnStyle('#1e3a5f')}>Save</button>
            <button onClick={() => setEditing(false)} style={btnStyle('#1e2530')}>Cancel</button>
          </>
        ) : (
          <>
            <span
              onClick={() => { setInput(String(fund.bot_fund)); setEditing(true) }}
              title="Click to edit"
              role="button"
              style={{ color: '#aaa', fontSize: 13, cursor: 'pointer', textDecoration: 'underline dotted' }}
            >
              {fmtUsd(fund.bot_fund)}
            </span>
            <span style={{ color: '#555', fontSize: 12 }}>
              Allocated: <span style={{ color: '#aaa' }}>{fmtUsd(fund.allocated)}</span>
              {' · '}
              Available: <span style={{ color: '#26a69a' }}>{fmtUsd(fund.available)}</span>
            </span>
          </>
        )}
      </div>
      {/* Progress bar */}
      <div style={{ height: 4, background: '#1e2530', borderRadius: 2 }}>
        <div style={{
          height: '100%', borderRadius: 2, background: '#26a69a',
          width: `${Math.min(allocPct, 100)}%`, transition: 'width 0.3s',
        }} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AddBotBar
// ---------------------------------------------------------------------------

function AddBotBar({
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
  const [error, setError] = useState('')

  useEffect(() => {
    try {
      const raw = localStorage.getItem(SAVED_KEY)
      if (raw) setStrategies(JSON.parse(raw))
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
        trailing_stop: s.trailingEnabled ? s.trailingConfig : null,
        dynamic_sizing: s.dynamicSizing ?? null,
        trading_hours: s.tradingHours ?? null,
        slippage_pct: typeof s.slippage === 'number' ? s.slippage : 0,
        data_source: dataSource,
      })
      setAllocation('')
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to add bot')
    }
  }

  return (
    <div style={{ ...sectionStyle, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {/* Strategy dropdown */}
        <select
          value={selectedIdx}
          onChange={e => onStrategyChange(Number(e.target.value))}
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

        {/* Data source */}
        <select value={dataSource} onChange={e => setDataSource(e.target.value)} style={inputStyle}>
          <option value="alpaca-iex">IEX</option>
          <option value="alpaca">Alpaca SIP</option>
          <option value="yahoo">Yahoo</option>
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

// ---------------------------------------------------------------------------
// BotControlCenter — main component
// ---------------------------------------------------------------------------

export default function BotControlCenter() {
  const [fund, setFund] = useState<BotFundStatus | null>(null)
  const [bots, setBots] = useState<BotSummary[]>([])
  const [error, setError] = useState('')

  const loadBots = async () => {
    try {
      const data = await listBots()
      setFund(data.fund)
      setBots(data.bots)
    } catch {
      setError('Could not reach bot API')
    }
  }

  useEffect(() => {
    loadBots()
    const id = setInterval(loadBots, 2000)
    return () => clearInterval(id)
  }, [])

  const handleSetFund = async (amount: number) => {
    try {
      const f = await setBotFund(amount)
      setFund(f)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to set fund')
    }
  }

  const handleAdd = async (config: any) => {
    await addBot(config)
    await loadBots()
  }

  const handleStart = async (botId: string) => {
    try { await startBot(botId); await loadBots() }
    catch (e: any) { setError(e?.response?.data?.detail ?? 'Failed to start bot') }
  }

  const handleStop = async (botId: string) => {
    try { await stopBot(botId); await loadBots() }
    catch (e: any) { setError(e?.response?.data?.detail ?? 'Failed to stop bot') }
  }

  const handleBacktest = async (botId: string) => {
    try { await backtestBot(botId); await loadBots() }
    catch (e: any) { setError(e?.response?.data?.detail ?? 'Failed to run backtest') }
  }

  const handleDelete = async (botId: string) => {
    try { await deleteBot(botId); await loadBots() }
    catch (e: any) { setError(e?.response?.data?.detail ?? 'Failed to delete bot') }
  }

  const handleManualBuy = async (botId: string) => {
    try { await manualBuyBot(botId); await loadBots() }
    catch (e: any) { setError(e?.response?.data?.detail ?? 'Failed to place buy') }
  }

  const handleUpdate = async (botId: string, updates: Record<string, unknown>) => {
    try { await updateBot(botId, updates); await loadBots() }
    catch (e: any) { setError(e?.response?.data?.detail ?? 'Failed to update bot') }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '10px 0' }}>
      <div style={{ color: '#e6edf3', fontWeight: 700, fontSize: 14, padding: '0 2px' }}>
        Live Trading Bots
      </div>

      {error && (
        <div style={{ color: '#ef5350', fontSize: 12, padding: '4px 8px', background: '#1a0d0d', borderRadius: 4 }}>
          {error}
          <button onClick={() => setError('')} style={{ marginLeft: 8, background: 'none', border: 'none', color: '#ef5350', cursor: 'pointer' }}>×</button>
        </div>
      )}

      <FundBar fund={fund} onSetFund={handleSetFund} />
      <AddBotBar fund={fund} onAdd={handleAdd} />

      {bots.length === 0 && fund && fund.bot_fund > 0 && (
        <div style={{ color: '#555', fontSize: 13, padding: '8px 12px' }}>
          No bots yet. Add one above.
        </div>
      )}

      {bots.map(bot => (
        <BotCard
          key={bot.bot_id}
          summary={bot}
          onStart={() => handleStart(bot.bot_id)}
          onStop={() => handleStop(bot.bot_id)}
          onBacktest={() => handleBacktest(bot.bot_id)}
          onDelete={() => handleDelete(bot.bot_id)}
          onManualBuy={() => handleManualBuy(bot.bot_id)}
          onUpdate={(updates) => handleUpdate(bot.bot_id, updates)}
        />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shared styles
// ---------------------------------------------------------------------------

const sectionStyle: React.CSSProperties = {
  background: '#161b22',
  border: '1px solid #1e2530',
  borderRadius: 6,
  padding: '10px 12px',
}

const inputStyle: React.CSSProperties = {
  background: '#0d1117',
  border: '1px solid #2a3040',
  borderRadius: 4,
  color: '#e6edf3',
  padding: '4px 8px',
  fontSize: 12,
}
