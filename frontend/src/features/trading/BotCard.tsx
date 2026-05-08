import { useEffect, useRef, useState } from 'react'
import { MoreHorizontal } from 'lucide-react'
import type { BotSummary, BotDetail, BotActivityEntry, SavedStrategy } from '../../shared/types'
import { fetchBotDetail } from '../../api/bots'
import { fmtUsd, fmtPnl } from '../../shared/utils/format'
import { statusColor, levelColor } from '../../shared/utils/colors'
import { fmtTimeET } from '../../shared/utils/time'
import MiniSparkline from './MiniSparkline'
import DailyPnlChart from './DailyPnlChart'
import { useBroker } from '../../shared/hooks/useOHLCV'
import { INFO_COLUMN_FLEX, StatCell, btnStyle } from './ui'

const SAVED_KEY = 'strategylab-saved-strategies'

const POLL_SECONDS: Record<string, number> = { '1m': 10, '5m': 15, '15m': 20, '30m': 30, '1h': 60 }

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
  const lastTick = detail?.state.last_tick ?? summary.last_tick
  if (!lastTick) return '#484f58'
  const elapsed = (Date.now() - new Date(lastTick).getTime()) / 1000
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
  dragHandleProps,
  compact = false,
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
  dragHandleProps?: Record<string, unknown>
  compact?: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState<BotDetail | null>(null)
  const [editingAlloc, setEditingAlloc] = useState(false)
  const [allocValue, setAllocValue] = useState('')
  const [editingSpread, setEditingSpread] = useState(false)
  const [spreadValue, setSpreadValue] = useState('')
  const [editingDD, setEditingDD] = useState(false)
  const [ddValue, setDdValue] = useState('')
  const [editingStrategy, setEditingStrategy] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  const { adaptiveInterval } = useBroker()
  const running = summary.status === 'running'
  const stopped = summary.status === 'stopped'

  // Reset kebab menu when switching between compact/expanded mode
  useEffect(() => { setMenuOpen(false) }, [compact])

  // Click-outside for kebab menu — only registered while menu is open
  useEffect(() => {
    if (!menuOpen) return
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [menuOpen])

  useEffect(() => {
    if (!expanded) { setDetail(null); return }
    let active = true
    const load = async () => {
      if (document.hidden) return
      try {
        const d = await fetchBotDetail(summary.bot_id)
        if (active) setDetail(d)
      } catch {}
    }
    load()
    const id = setInterval(load, adaptiveInterval(5000))
    return () => { active = false; clearInterval(id) }
  }, [expanded, summary.bot_id, adaptiveInterval])

  const pnlColor = summary.total_pnl >= 0 ? '#26a69a' : '#ef5350'
  const dir = summary.direction ?? 'long'
  const bgTint = dir === 'short' ? 'rgba(239, 83, 80, 0.08)' : 'rgba(38, 166, 154, 0.05)'

  // Guard division-by-zero for P&L percentage
  const pnlPct = summary.allocated_capital > 0
    ? (summary.total_pnl / summary.allocated_capital * 100).toFixed(1)
    : '0.0'

  const lastTickStr = (() => { const t = detail?.state.last_tick ?? summary.last_tick; return t ? fmtTimeET(t) : 'No tick yet' })()
  const statusTooltip = [
    `Status: ${summary.status}`,
    `P&L: ${fmtPnl(summary.total_pnl)} (${pnlPct}%)`,
    summary.has_position ? 'In position' : 'No position',
    `Last tick: ${lastTickStr}`,
  ].join('\n')

  // ---- Compact layout ----
  if (compact) {
    return (
      <div style={{
        background: `linear-gradient(135deg, ${bgTint}, #161b22)`,
        border: '1px solid #1e2530', borderRadius: 4,
        display: 'flex', flexDirection: 'column',
      }}>
        {/* Compact two-column row */}
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '4px 8px', minHeight: 34, cursor: 'pointer',
          }}
          onClick={() => setExpanded(e => !e)}
        >
          {/* Left column — text info */}
          <div style={{ flex: INFO_COLUMN_FLEX, display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            {/* Drag handle */}
            {dragHandleProps && (
              <div
                {...dragHandleProps}
                onClick={e => e.stopPropagation()}
                style={{
                  cursor: 'grab', color: '#484f58', fontSize: 14,
                  userSelect: 'none', flexShrink: 0, lineHeight: 1,
                }}
                title="Drag to reorder"
              >
                ⠿
              </div>
            )}

            {/* Heartbeat dot */}
            <div
              title={lastTickStr}
              style={{
                width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                background: heartbeatColor(summary, detail),
                boxShadow: running ? `0 0 4px ${heartbeatColor(summary, detail)}` : 'none',
              }}
            />

            {/* Symbol + badge + strategy name */}
            <span style={{ fontSize: 12, minWidth: 0, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>
              <span style={{ color: '#e6edf3', fontWeight: 600 }}>{summary.symbol}</span>
              {dir === 'short' && (
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: '0px 4px', borderRadius: 2,
                  background: 'rgba(239,83,80,0.15)', color: '#ef5350',
                  lineHeight: '16px', marginLeft: 4, verticalAlign: 'middle',
                }}>S</span>
              )}
              <span style={{ color: '#666', marginLeft: 6 }}>{summary.strategy_name}</span>
            </span>

            {/* P&L: dollar + percentage */}
            <span style={{ fontSize: 12, color: pnlColor, flexShrink: 0 }}>
              {fmtPnl(summary.total_pnl)}
              <span style={{ color: pnlColor, opacity: 0.7, marginLeft: 3 }}>
                ({pnlPct}%)
              </span>
            </span>

            {/* Status badge */}
            <span
              style={{ fontSize: 10, color: statusColor(summary.status), textTransform: 'capitalize', flexShrink: 0, cursor: 'default' }}
              title={statusTooltip}
            >
              {summary.status}
            </span>
            {stopped && summary.was_running && (
              <span style={{ fontSize: 10, color: '#f0b74e', flexShrink: 0 }} title="Was running before restart">
                ⚡ Was running
              </span>
            )}

            {/* Kebab menu — replaces inline buttons */}
            <div
              ref={menuRef}
              style={{ position: 'relative', marginLeft: 'auto', flexShrink: 0 }}
              onClick={e => e.stopPropagation()}
            >
              <button
                onClick={() => setMenuOpen(o => !o)}
                style={{
                  background: 'none', border: '1px solid #2a3040', borderRadius: 4,
                  color: '#8b949e', cursor: 'pointer', padding: '1px 4px',
                  display: 'flex', alignItems: 'center',
                }}
                title="Actions"
              >
                <MoreHorizontal size={14} />
              </button>

              {menuOpen && (
                <div style={{
                  position: 'absolute', top: '100%', right: 0,
                  background: '#161b22', border: '1px solid #2a3040',
                  borderRadius: 4, boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
                  zIndex: 100, minWidth: 120, marginTop: 2,
                }}>
                  {[
                    {
                      label: 'Backtest',
                      disabled: running,
                      action: () => { onBacktest(); setMenuOpen(false) },
                    },
                    {
                      label: stopped ? 'Start' : 'Stop',
                      disabled: false,
                      action: () => { stopped ? onStart() : onStop(); setMenuOpen(false) },
                    },
                    {
                      label: dir === 'short' ? 'Short' : 'Buy',
                      disabled: !running || summary.has_position,
                      action: () => { onManualBuy(); setMenuOpen(false) },
                    },
                    {
                      label: expanded ? 'Hide Log' : 'Show Log',
                      disabled: false,
                      action: () => { setExpanded(e => !e); setMenuOpen(false) },
                    },
                    {
                      label: 'Reset P&L',
                      disabled: false,
                      action: () => {
                        if (confirm('Reset P&L for this bot? Journal rows are kept; the display starts fresh from now.')) onResetPnl()
                        setMenuOpen(false)
                      },
                    },
                    ...(stopped ? [{
                      label: 'Delete',
                      disabled: false,
                      action: () => { onDelete(); setMenuOpen(false) },
                    }] : []),
                  ].map(item => (
                    <button
                      key={item.label}
                      onClick={item.action}
                      disabled={item.disabled}
                      style={{
                        display: 'block', width: '100%', textAlign: 'left',
                        background: 'none', border: 'none', padding: '6px 12px',
                        fontSize: 12, color: item.disabled ? '#444' : '#ccc',
                        cursor: item.disabled ? 'not-allowed' : 'pointer',
                      }}
                      onMouseEnter={e => { if (!item.disabled) (e.target as HTMLElement).style.background = '#1e2530' }}
                      onMouseLeave={e => { (e.target as HTMLElement).style.background = 'none' }}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right column — sparkline (matches expanded proportions) */}
          <div style={{ flex: 1, minWidth: 120, height: 24 }} onClick={e => e.stopPropagation()}>
            <MiniSparkline equityData={detail?.state.equity_snapshots ?? summary.equity_snapshots ?? []} alignedRange={alignedRange} height={24} />
          </div>
        </div>

        {/* Expandable activity log */}
        {expanded && (
          <div style={{ padding: '0 8px 8px' }}>
            <ActivityLog entries={detail?.state.activity_log ?? []} />
          </div>
        )}
      </div>
    )
  }

  // ---- Expanded (default) layout ----
  return (
    <div style={{
      background: `linear-gradient(135deg, ${bgTint}, #161b22)`, border: '1px solid #1e2530', borderRadius: 6,
      padding: 12, display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      {/* Two-column layout */}
      <div style={{ display: 'flex', gap: 12 }}>
        {/* Drag handle */}
        {dragHandleProps && (
          <div
            {...dragHandleProps}
            style={{
              display: 'flex', alignItems: 'center', cursor: 'grab',
              color: '#484f58', fontSize: 16, padding: '0 2px',
              userSelect: 'none', flexShrink: 0, alignSelf: 'stretch',
            }}
            title="Drag to reorder"
          >
            ⠿
          </div>
        )}
        {/* Left column */}
        <div style={{ flex: INFO_COLUMN_FLEX, display: 'flex', flexDirection: 'column', gap: 6, minWidth: 120 }}>
          {/* Header row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* Heartbeat dot */}
            <div
              title={lastTickStr}
              style={{
                width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                background: heartbeatColor(summary, detail),
                boxShadow: running ? `0 0 6px ${heartbeatColor(summary, detail)}` : 'none',
              }}
            />
            <span style={{ color: '#e6edf3', fontWeight: 600 }}>
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

          {/* Stats row — columnar layout */}
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '4px 10px',
            fontSize: 12,
          }}>
            <StatCell
              label="Allocated"
              value={editingAlloc ? (
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
                  style={{ width: '100%', boxSizing: 'border-box', fontSize: 12, background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 3, padding: '1px 4px' }}
                />
              ) : (
                <span
                  style={{ color: stopped ? '#58a6ff' : '#aaa', cursor: stopped ? 'pointer' : 'default', borderBottom: stopped ? '1px dashed #58a6ff' : 'none' }}
                  onClick={() => { if (stopped) { setAllocValue(String(summary.allocated_capital)); setEditingAlloc(true) } }}
                  title={stopped ? 'Click to edit' : 'Stop bot to edit'}
                >{fmtUsd(summary.allocated_capital)}</span>
              )}
            />
            <StatCell label="Trades" value={<span style={{ color: '#aaa' }}>{summary.trades_count}</span>} />
            <StatCell
              label="P&L"
              value={<span style={{ color: pnlColor }}>{fmtPnl(summary.total_pnl)} ({pnlPct}%)</span>}
            />
            <StatCell
              label="Status"
              value={<span style={{ color: statusColor(summary.status), textTransform: 'capitalize', cursor: 'default' }} title={statusTooltip}>{summary.status}</span>}
            />
            {stopped && summary.was_running && (
              <StatCell
                label=""
                value={<span style={{ color: '#f0b74e', fontSize: 11 }} title="This bot was running before the server restarted">⚡ Was running before restart</span>}
              />
            )}
            {summary.regime_direction != null && (
              <StatCell
                label="Regime"
                value={
                  summary.pending_regime_flip ? (
                    <span style={{ color: '#f0b74e' }}>⏳ Pending flip</span>
                  ) : (
                    <span style={{
                      color: summary.regime_direction === 'long' ? '#26a69a'
                           : summary.regime_direction === 'short' ? '#ef5350'
                           : '#666',
                    }}>
                      {summary.regime_direction === 'long' ? '▲ Long'
                       : summary.regime_direction === 'short' ? '▼ Short'
                       : '⊘ Flat'}
                    </span>
                  )
                }
              />
            )}
            {summary.avg_cost_bps != null && (
              <StatCell
                label="Slippage"
                value={<span style={{ color: summary.avg_cost_bps > 5 ? '#f85149' : '#8b949e' }}>{summary.avg_cost_bps.toFixed(1)} bps</span>}
              />
            )}
            <StatCell
              label="Spread cap"
              value={editingSpread ? (
                <input
                  autoFocus
                  type="number"
                  value={spreadValue}
                  min={0}
                  onChange={e => setSpreadValue(e.target.value)}
                  onBlur={() => {
                    const v = spreadValue === '' ? 0 : parseFloat(spreadValue)
                    if (!isNaN(v) && v >= 0) onUpdate({ max_spread_bps: v })
                    setEditingSpread(false)
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                    if (e.key === 'Escape') setEditingSpread(false)
                  }}
                  style={{ width: '100%', boxSizing: 'border-box', fontSize: 12, background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 3, padding: '1px 4px' }}
                />
              ) : (
                <span
                  style={{ color: stopped ? '#58a6ff' : '#aaa', cursor: stopped ? 'pointer' : 'default', borderBottom: stopped ? '1px dashed #58a6ff' : 'none' }}
                  onClick={() => { if (stopped) { setSpreadValue(summary.max_spread_bps ? String(summary.max_spread_bps) : ''); setEditingSpread(true) } }}
                  title={stopped ? 'Click to edit (empty = disabled)' : 'Stop bot to edit'}
                >{summary.max_spread_bps ? `${summary.max_spread_bps} bps` : 'off'}</span>
              )}
            />
            <StatCell
              label="Max DD"
              value={editingDD ? (
                <input
                  autoFocus
                  type="number"
                  value={ddValue}
                  min={0}
                  step={0.1}
                  onChange={e => setDdValue(e.target.value)}
                  onBlur={() => {
                    const v = ddValue === '' ? 0 : parseFloat(ddValue)
                    if (!isNaN(v) && v >= 0) onUpdate({ drawdown_threshold_pct: v > 0 ? v : undefined })
                    setEditingDD(false)
                  }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                    if (e.key === 'Escape') setEditingDD(false)
                  }}
                  style={{ width: '100%', boxSizing: 'border-box', fontSize: 12, background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 3, padding: '1px 4px' }}
                />
              ) : (
                <span
                  style={{ color: stopped ? '#58a6ff' : '#aaa', cursor: stopped ? 'pointer' : 'default', borderBottom: stopped ? '1px dashed #58a6ff' : 'none' }}
                  onClick={() => { if (stopped) { setDdValue(summary.drawdown_threshold_pct ? String(summary.drawdown_threshold_pct) : ''); setEditingDD(true) } }}
                  title={stopped ? 'Click to edit (empty = disabled)' : 'Stop bot to edit'}
                >{summary.drawdown_threshold_pct ? `${summary.drawdown_threshold_pct}%` : '—'}</span>
              )}
            />
          </div>

          {/* Pause reason (structural IBKR reject) */}
          {(detail?.state.pause_reason ?? summary.pause_reason) && (
            <div style={{ fontSize: 11, color: '#f0b74e', background: 'rgba(240,183,78,0.08)', padding: '3px 8px', borderRadius: 3 }}>
              {detail?.state.pause_reason ?? summary.pause_reason}
            </div>
          )}

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
          <MiniSparkline equityData={detail?.state.equity_snapshots ?? summary.equity_snapshots ?? []} alignedRange={alignedRange} />
        </div>
      </div>

      {/* Daily P&L bar chart */}
      {(detail?.state.equity_snapshots ?? summary.equity_snapshots ?? []).length >= 2 && (
        <DailyPnlChart snapshots={detail?.state.equity_snapshots ?? summary.equity_snapshots ?? []} />
      )}

      {/* Expandable activity log */}
      {expanded && (
        <ActivityLog entries={detail?.state.activity_log ?? []} />
      )}
    </div>
  )
}
