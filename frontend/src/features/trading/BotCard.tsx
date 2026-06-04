import { useEffect, useRef, useState } from 'react'
import { MoreHorizontal } from 'lucide-react'
import type { BotSummary, BotDetail, BotActivityEntry, SavedStrategy } from '../../shared/types'
import { fetchBotDetail } from '../../api/bots'
import { fmtUsd, fmtPnl } from '../../shared/utils/format'
import { statusColor, levelColor } from '../../shared/utils/colors'
import { fmtTimeET } from '../../shared/utils/time'
import MiniSparkline from './MiniSparkline'
import DailyPnlChart from './DailyPnlChart'
import { INFO_COLUMN_FLEX, StatCell, btnStyle } from './ui'

const SAVED_KEY = 'strategylab-saved-strategies'

const POLL_SECONDS: Record<string, number> = { '1m': 10, '5m': 15, '15m': 20, '30m': 30, '1h': 60 }

// ---------------------------------------------------------------------------
// ActivityLog
// ---------------------------------------------------------------------------

function ActivityLog({ entries, status }: { entries: BotActivityEntry[], status?: string }) {
  const emptyText = status === 'running' ? 'Waiting for next tick'
    : status === 'stopped' ? 'Bot is stopped'
    : 'No activity yet.'
  return (
    <div style={{
      maxHeight: 160, overflowY: 'auto', background: 'var(--gh-bg-deep)',
      border: '1px solid var(--gh-bg-alt)', borderRadius: 4, padding: '6px 8px',
      fontFamily: 'monospace', fontSize: 11,
    }}>
      {entries.length === 0 && <span style={{ color: 'var(--gh-text-placeholder)' }}>{emptyText}</span>}
      {entries.map((e, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 2 }}>
          <span style={{ color: 'var(--gh-text-disabled)', flexShrink: 0 }}>
            {fmtTimeET(e.time)}
          </span>
          <span style={{ color: levelColor(e.level), flexShrink: 0 }}>[{e.level}]</span>
          <span style={{ color: 'var(--gh-text-light)' }}>{e.msg}</span>
        </div>
      ))}
    </div>
  )
}

/**
 * Returns a CSS custom-property reference string (e.g. 'var(--gh-green)'),
 * NOT a hex/rgb colour value. Browsers resolve var() correctly in inline
 * styles (background, boxShadow). Do NOT pass this return value to a
 * lw-charts addSeries colour option — lw-charts does not resolve CSS vars
 * and will silently render the string as an invalid colour (K-01).
 */
function heartbeatColor(summary: BotSummary, detail: BotDetail | null): string {
  if (summary.status === 'stopped') return 'var(--gh-text-dim)'  // grey
  const lastTick = detail?.state?.last_tick ?? summary.last_tick
  if (!lastTick) return 'var(--gh-text-dim)'
  const elapsed = (Date.now() - new Date(lastTick).getTime()) / 1000
  const interval = POLL_SECONDS[summary.interval] ?? 60
  return elapsed <= interval * 2 ? 'var(--gh-green)' : 'var(--gh-red)'  // green or red
}

// ---------------------------------------------------------------------------
// StatusBadge — custom popover replacing native title= tooltip
// ---------------------------------------------------------------------------

function StatusBadge({ status, tooltip, style }: { status: string; tooltip: string; style?: React.CSSProperties }) {
  const [hovered, setHovered] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  const [popPos, setPopPos] = useState<{ left: number; bottom: number } | null>(null)

  function handleMouseEnter() {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect()
      setPopPos({ left: r.left + r.width / 2, bottom: window.innerHeight - r.top + 6 })
    }
    setHovered(true)
  }

  return (
    <span
      ref={ref}
      style={{ position: 'relative', cursor: 'default', ...style }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={() => setHovered(false)}
    >
      {status}
      {hovered && popPos && (
        <div style={{
          position: 'fixed',
          left: popPos.left,
          bottom: popPos.bottom,
          transform: 'translateX(-50%)',
          background: 'var(--gh-bg-card)',
          border: '1px solid var(--gh-border)',
          borderRadius: 6,
          padding: '6px 10px',
          fontSize: 11,
          color: 'var(--gh-text-primary)',
          whiteSpace: 'pre',
          zIndex: 9999,
          pointerEvents: 'none',
          boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
        }}>
          {tooltip}
        </div>
      )}
    </span>
  )
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
  adaptiveInterval,
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
  adaptiveInterval: (ms: number) => number
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
  const [confirmingResetPnl, setConfirmingResetPnl] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  // F297: restore focus when Reset P&L confirm is cancelled.
  // Compact layout: the confirm panel is additive ({confirmingResetPnl && ...}) — the kebab
  //   button stays mounted throughout, so direct .focus() works.
  // Expanded layout: the ternary unmounts the "Reset P&L" trigger button while confirming,
  //   so the button remounts on cancel — requestAnimationFrame defers focus until after DOM flush.
  const menuBtnRef = useRef<HTMLButtonElement>(null)
  const resetPnlBtnRef = useRef<HTMLButtonElement>(null)
  const cancelResetPnlCompact = () => { setConfirmingResetPnl(false); menuBtnRef.current?.focus() }
  const cancelResetPnlExpanded = () => { setConfirmingResetPnl(false); requestAnimationFrame(() => { resetPnlBtnRef.current?.focus() }) }

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

  const pnlColor = summary.total_pnl >= 0 ? 'var(--gh-teal)' : 'var(--gh-red-alt)'
  const dir = summary.direction ?? 'long'
  const bgTint = dir === 'short' ? 'rgba(239, 83, 80, 0.08)' : 'rgba(38, 166, 154, 0.05)'

  // Guard division-by-zero for P&L percentage
  const pnlPct = summary.allocated_capital > 0
    ? (summary.total_pnl / summary.allocated_capital * 100).toFixed(1)
    : '0.0'

  const lastTickStr = (() => { const t = detail?.state?.last_tick ?? summary.last_tick; return t ? fmtTimeET(t) : 'No tick yet' })()
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
        background: `linear-gradient(135deg, ${bgTint}, var(--gh-bg-panel))`,
        border: '1px solid var(--gh-bg-alt)', borderRadius: 4,
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
                  cursor: 'grab', color: 'var(--gh-text-dim)', fontSize: 14,
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
              <span style={{ color: 'var(--gh-text-primary)', fontWeight: 600 }}>{summary.symbol}</span>
              {dir === 'short' && (
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: '0px 4px', borderRadius: 2,
                  background: 'rgba(239,83,80,0.15)', color: 'var(--gh-red-alt)',
                  lineHeight: '16px', marginLeft: 4, verticalAlign: 'middle',
                }}>S</span>
              )}
              <span style={{ color: 'var(--gh-text-faint2)', marginLeft: 6 }}>{summary.strategy_name}</span>
            </span>

            {/* P&L: dollar + percentage */}
            <span style={{ fontSize: 12, color: pnlColor, flexShrink: 0 }}>
              {fmtPnl(summary.total_pnl)}
              <span style={{ color: pnlColor, opacity: 0.7, marginLeft: 3 }}>
                ({pnlPct}%)
              </span>
            </span>

            {/* Status badge */}
            <StatusBadge
              status={summary.status}
              tooltip={statusTooltip}
              style={{ fontSize: 10, color: statusColor(summary.status), textTransform: 'capitalize', flexShrink: 0 }}
            />
            {stopped && summary.was_running && (
              <span style={{ fontSize: 10, color: 'var(--gh-yellow-warm)', flexShrink: 0 }} title="Was running before restart">
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
                ref={menuBtnRef}
                onClick={() => setMenuOpen(o => !o)}
                style={{
                  background: 'none', border: '1px solid var(--gh-border-btn)', borderRadius: 4,
                  color: 'var(--gh-text-muted)', cursor: 'pointer', padding: '1px 4px',
                  display: 'flex', alignItems: 'center',
                }}
                title="Actions"
              >
                <MoreHorizontal size={14} />
              </button>

              {menuOpen && (
                <div style={{
                  position: 'absolute', top: '100%', right: 0,
                  background: 'var(--gh-bg-panel)', border: '1px solid var(--gh-border-btn)',
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
                        setConfirmingResetPnl(true)
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
                        fontSize: 12, color: item.disabled ? 'var(--gh-text-disabled)' : 'var(--gh-text-light)',
                        cursor: item.disabled ? 'not-allowed' : 'pointer',
                      }}
                      onMouseEnter={e => { if (!item.disabled) (e.target as HTMLElement).style.background = 'var(--gh-bg-alt)' }}
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
            <MiniSparkline equityData={detail?.state?.equity_snapshots ?? summary.equity_snapshots ?? []} alignedRange={alignedRange} height={24} />
          </div>
        </div>

        {/* Expandable activity log */}
        {expanded && (
          <div style={{ padding: '0 8px 8px' }}>
            <ActivityLog entries={detail?.state?.activity_log ?? []} status={summary.status} />
          </div>
        )}
        {/* Inline reset-P&L confirm (triggered from menu) */}
        {confirmingResetPnl && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px', background: 'rgba(240,183,78,0.08)', borderTop: '1px solid rgba(240,183,78,0.2)' }} onClick={e => e.stopPropagation()}>
            <span style={{ fontSize: 11, color: 'var(--gh-text-light)', flex: 1 }}>Reset P&L for this bot? Journal rows are kept; the display starts fresh from now.</span>
            <button onClick={() => { onResetPnl(); setConfirmingResetPnl(false) }} style={btnStyle('var(--gh-red-bg)')}>Confirm</button>
            <button onClick={cancelResetPnlCompact} style={btnStyle('var(--gh-bg-alt)')}>Cancel</button>
          </div>
        )}
      </div>
    )
  }

  // ---- Expanded (default) layout ----
  return (
    <div style={{
      background: `linear-gradient(135deg, ${bgTint}, var(--gh-bg-panel))`, border: '1px solid var(--gh-bg-alt)', borderRadius: 6,
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
              color: 'var(--gh-text-dim)', fontSize: 16, padding: '0 2px',
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
            <span style={{ color: 'var(--gh-text-primary)', fontWeight: 600 }}>
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
                  style={{ fontSize: 12, background: 'var(--gh-bg-deep)', color: 'var(--gh-text-primary)', border: '1px solid var(--gh-border)', borderRadius: 3 }}
                >
                  <option value={-1}>Select strategy…</option>
                  {(() => { try { return JSON.parse(localStorage.getItem(SAVED_KEY) || '[]') } catch { return [] } })()
                    .map((s: SavedStrategy, i: number) => <option key={i} value={i}>{s.name}</option>)}
                </select>
              ) : (
                <span
                  style={{ cursor: stopped ? 'pointer' : 'default', borderBottom: stopped ? '1px dashed var(--gh-blue)' : 'none' }}
                  onClick={() => { if (stopped) setEditingStrategy(true) }}
                  title={stopped ? 'Click to change strategy' : 'Stop bot to edit'}
                >{summary.strategy_name}</span>
              )}
              <span style={{
                fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 3,
                marginLeft: 6,
                background: dir === 'short' ? 'rgba(239,83,80,0.15)' : 'rgba(38,166,154,0.15)',
                color: dir === 'short' ? 'var(--gh-red-alt)' : 'var(--gh-teal)',
                textTransform: 'uppercase', letterSpacing: 0.5,
              }}>
                {dir}
              </span>
            </span>
            <span style={{ color: 'var(--gh-text-faint)', fontSize: 12 }}>
              {summary.symbol} · {summary.interval} · {summary.data_source ?? 'alpaca-iex'}
              {' · '}
              <span style={{ color: (summary.broker ?? 'alpaca') === 'ibkr' ? 'var(--gh-yellow-warm)' : 'var(--gh-blue-alt)' }}>
                via {(summary.broker ?? 'alpaca') === 'ibkr' ? 'IBKR' : 'Alpaca'}
              </span>
              {summary.kind === 'graph' && (
                <span style={{
                  marginLeft: 6,
                  background: 'var(--gh-green-bg)',
                  color: 'var(--gh-green-bright)',
                  border: '1px solid var(--gh-green-dim)',
                  borderRadius: 3,
                  padding: '1px 5px',
                  fontSize: 10,
                  fontWeight: 600,
                  letterSpacing: 0.3,
                }}>
                  via Graph
                </span>
              )}
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
                  style={{ width: '100%', boxSizing: 'border-box', fontSize: 12, background: 'var(--gh-bg-deep)', color: 'var(--gh-text-primary)', border: '1px solid var(--gh-border)', borderRadius: 3, padding: '1px 4px' }}
                />
              ) : (
                <span
                  style={{ color: stopped ? 'var(--gh-blue)' : 'var(--gh-text-mid)', cursor: stopped ? 'pointer' : 'default', borderBottom: stopped ? '1px dashed var(--gh-blue)' : 'none' }}
                  onClick={() => { if (stopped) { setAllocValue(String(summary.allocated_capital)); setEditingAlloc(true) } }}
                  title={stopped ? 'Click to edit' : 'Stop bot to edit'}
                >{fmtUsd(summary.allocated_capital)}</span>
              )}
            />
            <StatCell label="Trades" value={<span style={{ color: 'var(--gh-text-mid)' }}>{summary.trades_count}</span>} />
            <StatCell
              label="P&L"
              value={<span style={{ color: pnlColor }}>{fmtPnl(summary.total_pnl)} ({pnlPct}%)</span>}
            />
            <StatCell
              label="Status"
              value={<StatusBadge status={summary.status} tooltip={statusTooltip} style={{ color: statusColor(summary.status), textTransform: 'capitalize' }} />}
            />
            {stopped && summary.was_running && (
              <StatCell
                label=""
                value={<span style={{ color: 'var(--gh-yellow-warm)', fontSize: 11 }} title="This bot was running before the server restarted">⚡ Was running before restart</span>}
              />
            )}
            {summary.regime_direction != null && (
              <StatCell
                label="Regime"
                value={
                  summary.pending_regime_flip ? (
                    <span style={{ color: 'var(--gh-yellow-warm)' }}>⏳ Pending flip</span>
                  ) : (
                    <span style={{
                      color: summary.regime_direction === 'long' ? 'var(--gh-teal)'
                           : summary.regime_direction === 'short' ? 'var(--gh-red-alt)'
                           : 'var(--gh-text-faint2)',
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
                value={<span style={{ color: summary.avg_cost_bps > 5 ? 'var(--gh-red)' : 'var(--gh-text-muted)' }}>{summary.avg_cost_bps.toFixed(1)} bps</span>}
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
                  style={{ width: '100%', boxSizing: 'border-box', fontSize: 12, background: 'var(--gh-bg-deep)', color: 'var(--gh-text-primary)', border: '1px solid var(--gh-border)', borderRadius: 3, padding: '1px 4px' }}
                />
              ) : (
                <span
                  style={{ color: stopped ? 'var(--gh-blue)' : 'var(--gh-text-mid)', cursor: stopped ? 'pointer' : 'default', borderBottom: stopped ? '1px dashed var(--gh-blue)' : 'none' }}
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
                  style={{ width: '100%', boxSizing: 'border-box', fontSize: 12, background: 'var(--gh-bg-deep)', color: 'var(--gh-text-primary)', border: '1px solid var(--gh-border)', borderRadius: 3, padding: '1px 4px' }}
                />
              ) : (
                <span
                  style={{ color: stopped ? 'var(--gh-blue)' : 'var(--gh-text-mid)', cursor: stopped ? 'pointer' : 'default', borderBottom: stopped ? '1px dashed var(--gh-blue)' : 'none' }}
                  onClick={() => { if (stopped) { setDdValue(summary.drawdown_threshold_pct ? String(summary.drawdown_threshold_pct) : ''); setEditingDD(true) } }}
                  title={stopped ? 'Click to edit (empty = disabled)' : 'Stop bot to edit'}
                >{summary.drawdown_threshold_pct ? `${summary.drawdown_threshold_pct}%` : '—'}</span>
              )}
            />
          </div>

          {/* Pause reason (structural IBKR reject) */}
          {(detail?.state?.pause_reason ?? summary.pause_reason) && (
            <div style={{ fontSize: 11, color: 'var(--gh-yellow-warm)', background: 'rgba(240,183,78,0.08)', padding: '3px 8px', borderRadius: 3 }}>
              {detail?.state?.pause_reason ?? summary.pause_reason}
            </div>
          )}

          {/* Backtest summary (always visible if available) */}
          {summary.backtest_summary && (
            <div style={{ display: 'flex', gap: 12, fontSize: 11, color: 'var(--gh-text-faint2)' }}>
              {(() => {
                const s = summary.backtest_summary
                return <>
                  <span>BT Return: <span style={{ color: 'var(--gh-text-mid)' }}>{s.total_return_pct != null ? s.total_return_pct.toFixed(1) : '—'}%</span></span>
                  <span>Sharpe: <span style={{ color: 'var(--gh-text-mid)' }}>{s.sharpe_ratio != null ? s.sharpe_ratio.toFixed(2) : '—'}</span></span>
                  <span>MDD: <span style={{ color: 'var(--gh-red-alt)' }}>{s.max_drawdown_pct != null ? s.max_drawdown_pct.toFixed(1) : '—'}%</span></span>
                </>
              })()}
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <button onClick={onBacktest} disabled={running} style={btnStyle('var(--gh-blue-bg)', running)}>
              Backtest
            </button>
            {stopped ? (
              <button onClick={onStart} style={btnStyle('var(--gh-green-bg)')}>Start</button>
            ) : (
              <button onClick={onStop} style={btnStyle('var(--gh-red-bg)')}>Stop</button>
            )}
            <button
              onClick={onManualBuy}
              disabled={!running || summary.has_position}
              style={btnStyle('var(--gh-green-bg)', !running || summary.has_position)}
            >{dir === 'short' ? 'Short' : 'Buy'}</button>
            <button
              onClick={() => setExpanded(e => !e)}
              style={btnStyle('var(--gh-bg-alt)')}
            >
              {expanded ? 'Hide Log' : 'Show Log'}
            </button>
            {confirmingResetPnl ? (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 11, color: 'var(--gh-text-light)' }}>Reset P&L for this bot? Journal rows are kept; the display starts fresh from now.</span>
                <button onClick={() => { onResetPnl(); setConfirmingResetPnl(false) }} style={btnStyle('var(--gh-red-bg)')}>Confirm</button>
                <button onClick={cancelResetPnlExpanded} style={btnStyle('var(--gh-bg-alt)')}>Cancel</button>
              </span>
            ) : (
              <button
                ref={resetPnlBtnRef}
                onClick={() => setConfirmingResetPnl(true)}
                style={btnStyle('var(--gh-yellow-bg)')}
                title="Soft reset: marks an epoch so only trades from now on count toward P&L"
              >Reset P&L</button>
            )}
            {stopped && (
              <button onClick={onDelete} style={btnStyle('var(--gh-red-bg)')}>Delete</button>
            )}
          </div>
        </div>

        {/* Right column: mini chart */}
        <div style={{ flex: 1, minWidth: 120, minHeight: 60 }}>
          <MiniSparkline equityData={detail?.state?.equity_snapshots ?? summary.equity_snapshots ?? []} alignedRange={alignedRange} />
        </div>
      </div>

      {/* Daily P&L bar chart */}
      {(detail?.state?.equity_snapshots ?? summary.equity_snapshots ?? []).length >= 2 && (
        <DailyPnlChart snapshots={detail?.state?.equity_snapshots ?? summary.equity_snapshots ?? []} />
      )}

      {/* Expandable activity log */}
      {expanded && (
        <ActivityLog entries={detail?.state?.activity_log ?? []} status={summary.status} />
      )}
    </div>
  )
}
