import { useEffect, useState } from 'react'
import type { BotSummary, BotDetail, BotActivityEntry, SavedStrategy } from '../../shared/types'
import { fetchBotDetail } from '../../api/bots'
import { fmtUsd, fmtPnl } from '../../shared/utils/format'
import { statusColor, levelColor } from '../../shared/utils/colors'
import { fmtTimeET } from '../../shared/utils/time'
import MiniSparkline from './MiniSparkline'

const SAVED_KEY = 'strategylab-saved-strategies'

const POLL_SECONDS: Record<string, number> = { '1m': 10, '5m': 15, '15m': 20, '30m': 30, '1h': 60 }

// ---------------------------------------------------------------------------
// Shared button style
// ---------------------------------------------------------------------------

export function btnStyle(bg: string, disabled = false): React.CSSProperties {
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

function heartbeatColor(summary: BotSummary, detail: BotDetail | null): string {
  if (summary.status === 'stopped') return '#484f58'  // grey
  if (!detail?.state.last_tick) return '#484f58'
  const elapsed = (Date.now() - new Date(detail.state.last_tick).getTime()) / 1000
  const interval = POLL_SECONDS[summary.interval] ?? 60
  return elapsed <= interval * 2 ? '#26a641' : '#f85149'  // green or red
}

// ---------------------------------------------------------------------------
// BotCard
// ---------------------------------------------------------------------------

export default function BotCard({
  summary,
  onStart, onStop, onBacktest, onDelete, onManualBuy, onUpdate, onResetPnl,
  alignedRange,
}: {
  summary: BotSummary
  onStart: () => void
  onStop: () => void
  onBacktest: () => void
  onDelete: () => void
  onManualBuy: () => void
  onUpdate: (updates: Record<string, unknown>) => void
  onResetPnl: () => void
  alignedRange?: { from: number; to: number }
}) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState<BotDetail | null>(null)
  const [editingAlloc, setEditingAlloc] = useState(false)
  const [allocValue, setAllocValue] = useState('')
  const [editingStrategy, setEditingStrategy] = useState(false)

  const running = summary.status === 'running'
  const stopped = summary.status === 'stopped'

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
  const pnlColor = summary.total_pnl >= 0 ? '#26a69a' : '#ef5350'

  const dir = summary.direction ?? 'long'
  const bgTint = dir === 'short' ? 'rgba(239, 83, 80, 0.08)' : 'rgba(38, 166, 154, 0.05)'

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
            {/* Heartbeat dot */}
            <div
              title={detail?.state.last_tick ? `Last tick: ${fmtTimeET(detail.state.last_tick)}` : 'No tick yet'}
              style={{
                width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                background: heartbeatColor(summary, detail),
                boxShadow: running ? `0 0 6px ${heartbeatColor(summary, detail)}` : 'none',
              }}
            />
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
              {' · '}
              <span style={{ color: (summary.broker ?? 'alpaca') === 'ibkr' ? '#f0b74e' : '#58a6ff' }}>
                via {(summary.broker ?? 'alpaca') === 'ibkr' ? 'IBKR' : 'Alpaca'}
              </span>
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
            {summary.avg_cost_bps != null && (
              <span style={{ color: '#666' }}>Slippage: <span style={{ color: summary.avg_cost_bps > 5 ? '#f85149' : '#8b949e' }}>{summary.avg_cost_bps.toFixed(1)} bps</span></span>
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
            <button
              onClick={() => {
                if (confirm('Reset P&L for this bot? Journal rows are kept; the display starts fresh from now.')) onResetPnl()
              }}
              style={btnStyle('#3a2e1a')}
              title="Soft reset: marks an epoch so only trades from now on count toward P&L"
            >Reset P&L</button>
            {stopped && (
              <button onClick={onDelete} style={btnStyle('#3a1a1a')}>Delete</button>
            )}
          </div>
        </div>

        {/* Right column: mini chart */}
        <div style={{ flex: 1, minWidth: 120, minHeight: 60 }}>
          <MiniSparkline equityData={detail?.state.equity_snapshots ?? []} alignedRange={alignedRange} />
        </div>
      </div>

      {/* Expandable activity log */}
      {expanded && (
        <ActivityLog entries={detail?.state.activity_log ?? []} />
      )}
    </div>
  )
}
