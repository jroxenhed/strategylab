import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { BotSummary, BotFundStatus } from '../../shared/types'
import {
  setBotFund, addBot,
  startBot, stopBot, backtestBot, deleteBot, manualBuyBot, updateBot, resetBotPnl,
  startAllBots, stopAllBots, stopAndCloseAllBots, reorderBots,
} from '../../api/bots'
import { api } from '../../api/client'
import { fmtUsd } from '../../shared/utils/format'
import { apiErrorDetail } from '../../shared/utils/errors'
import BotCard from './BotCard'
import { btnStyle } from './ui'
import PortfolioStrip from './PortfolioStrip'
import AddBotBar, { sectionStyle, inputStyle } from './AddBotBar'
import { useBroker } from '../../shared/hooks/useOHLCV'
import { useBotsQuery } from '../../shared/hooks/useTradingQueries'
import {
  DndContext, closestCenter, PointerSensor, useSensor, useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

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
// SortableBotCard — wraps BotCard with dnd-kit sortable
// ---------------------------------------------------------------------------

function SortableBotCard(props: {
  botId: string
  summary: BotSummary
  alignedRange?: { from: number; to: number }
  compact?: boolean
  onStart: () => void
  onStop: () => void
  onBacktest: () => void
  onDelete: () => void
  onManualBuy: () => void
  onUpdate: (updates: Record<string, unknown>) => void
  onResetPnl: () => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: props.botId,
  })

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.7 : 1,
    scale: isDragging ? '1.02' : undefined,
    boxShadow: isDragging ? '0 8px 24px rgba(0,0,0,0.4)' : 'none',
    zIndex: isDragging ? 10 : 'auto',
    position: 'relative' as const,
  }

  return (
    <div ref={setNodeRef} style={style} {...attributes}>
      <BotCard
        summary={props.summary}
        alignedRange={props.alignedRange}
        compact={props.compact}
        onStart={props.onStart}
        onStop={props.onStop}
        onBacktest={props.onBacktest}
        onDelete={props.onDelete}
        onManualBuy={props.onManualBuy}
        onUpdate={props.onUpdate}
        onResetPnl={props.onResetPnl}
        dragHandleProps={listeners}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// BotControlCenter — main component
// ---------------------------------------------------------------------------

export default function BotControlCenter() {
  const qc = useQueryClient()
  const { anyBrokerUnhealthy, health, pollIntervalMs } = useBroker()
  const [brokerBannerDismissed, setBrokerBannerDismissed] = useState(false)
  const [botsErrorDismissed, setBotsErrorDismissed] = useState(false)
  const [error, setError] = useState('')
  const [pollInput, setPollInput] = useState(pollIntervalMs != null ? String(pollIntervalMs) : '')
  const [pollFocused, setPollFocused] = useState(false)
  useEffect(() => {
    if (!pollFocused) setPollInput(pollIntervalMs != null ? String(pollIntervalMs) : '')
  }, [pollIntervalMs, pollFocused])
  // Track user-set order; updated on drag-end and reconciled with server data
  const orderRef = useRef<string[]>([])

  const { data: botsData, isError: botsError } = useBotsQuery()
  const fund: BotFundStatus | null = botsData?.fund ?? null
  const bots: BotSummary[] = botsData?.bots ?? []

  const invalidateBots = useCallback(() => qc.invalidateQueries({ queryKey: ['bots'] }), [qc])

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  // Reconcile server bots with local order: keep existing order, append new IDs at end
  const orderedBots = useMemo(() => {
    const order = orderRef.current
    const botMap = new Map(bots.map(b => [b.bot_id, b]))
    const result: BotSummary[] = []
    for (const id of order) {
      const bot = botMap.get(id)
      if (bot) { result.push(bot); botMap.delete(id) }
    }
    // Append any bots not in the saved order (newly added)
    for (const bot of botMap.values()) result.push(bot)
    return result
  }, [bots])

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const ids = orderedBots.map(b => b.bot_id)
    const oldIdx = ids.indexOf(active.id as string)
    const newIdx = ids.indexOf(over.id as string)
    if (oldIdx === -1 || newIdx === -1) return
    const newOrder = [...ids]
    const [moved] = newOrder.splice(oldIdx, 1)
    newOrder.splice(newIdx, 0, moved)
    orderRef.current = newOrder
    // Optimistic: update cache immediately so cards reorder without waiting for round-trip
    qc.setQueryData(['bots'], (old: { bots: BotSummary[]; fund: unknown } | undefined) => {
      if (!old) return old
      const map = new Map(old.bots.map(b => [b.bot_id, b]))
      return { ...old, bots: newOrder.map(id => map.get(id)).filter(Boolean) as BotSummary[] }
    })
    reorderBots(newOrder).then(() => invalidateBots()).catch(() => {})
  }, [orderedBots, invalidateBots])

  const [sparklineScale, setSparklineScale] = useState<'local' | 'aligned'>(() => {
    const v = localStorage.getItem('sparklineScale')
    return v === 'aligned' ? 'aligned' : 'local'
  })
  const [compactMode, setCompactMode] = useState<boolean>(() => {
    return localStorage.getItem('botCardCompact') === 'true'
  })
  useEffect(() => {
    localStorage.setItem('sparklineScale', sparklineScale)
  }, [sparklineScale])
  useEffect(() => {
    localStorage.setItem('botCardCompact', String(compactMode))
  }, [compactMode])
  useEffect(() => {
    if (!anyBrokerUnhealthy) setBrokerBannerDismissed(false)
  }, [anyBrokerUnhealthy])
  useEffect(() => { if (!botsError) setBotsErrorDismissed(false) }, [botsError])

  // Initialise drag-drop order from server on first load; keep user order on subsequent loads
  useEffect(() => {
    if (bots.length > 0 && orderRef.current.length === 0) {
      orderRef.current = bots.map(b => b.bot_id)
    }
  }, [bots])

  const handleSetFund = async (amount: number) => {
    try { await setBotFund(amount); invalidateBots() }
    catch (e) { setError(apiErrorDetail(e, 'Failed to set fund')) }
  }

  const handlePollIntervalCommit = async () => {
    if (!pollInput.trim()) return
    const ms = parseInt(pollInput, 10)
    if (isNaN(ms) || ms < 100 || ms > 60000) {
      setError('Poll interval must be between 100 and 60000 ms')
      return
    }
    try {
      await api.patch('/api/broker/poll-interval', { ms })
    } catch (e) {
      setError(apiErrorDetail(e, 'Failed to set poll interval'))
    }
  }

  const handleAdd = async (config: any) => {
    await addBot(config)
    invalidateBots()
  }

  const handleStart = async (botId: string) => {
    try { await startBot(botId); invalidateBots() }
    catch (e) { setError(apiErrorDetail(e, 'Failed to start bot')) }
  }

  const handleStop = async (botId: string) => {
    try { await stopBot(botId); invalidateBots() }
    catch (e) { setError(apiErrorDetail(e, 'Failed to stop bot')) }
  }

  const handleBacktest = async (botId: string) => {
    try { await backtestBot(botId); invalidateBots() }
    catch (e) { setError(apiErrorDetail(e, 'Failed to run backtest')) }
  }

  const handleDelete = async (botId: string) => {
    try { await deleteBot(botId); invalidateBots() }
    catch (e) { setError(apiErrorDetail(e, 'Failed to delete bot')) }
  }

  const handleManualBuy = async (botId: string) => {
    try { await manualBuyBot(botId); invalidateBots() }
    catch (e) { setError(apiErrorDetail(e, 'Failed to place buy')) }
  }

  const handleResetPnl = async (botId: string) => {
    try { await resetBotPnl(botId); invalidateBots() }
    catch (e) { setError(apiErrorDetail(e, 'Failed to reset P&L')) }
  }

  const handleUpdate = async (botId: string, updates: Record<string, unknown>) => {
    try { await updateBot(botId, updates); invalidateBots() }
    catch (e) { setError(apiErrorDetail(e, 'Failed to update bot')) }
  }

  const handleStartAll = async () => {
    try {
      const r = await startAllBots()
      invalidateBots()
      setError(r.failed.length ? `Started ${r.started.length}, ${r.failed.length} failed` : '')
    } catch (e) {
      setError(apiErrorDetail(e, 'Failed to start all bots'))
    }
  }

  const handleStopAll = async () => {
    try {
      const r = await stopAllBots()
      invalidateBots()
      setError(r.failed.length ? `Stopped ${r.stopped.length}, ${r.failed.length} failed` : '')
    } catch (e) {
      setError(apiErrorDetail(e, 'Failed to stop all bots'))
    }
  }

  const handleStopAndCloseAll = async () => {
    const openCount = bots.filter(b => b.has_position).length
    const running = bots.filter(b => b.status === 'running').length
    if (!window.confirm(`Close ${openCount} open position${openCount === 1 ? '' : 's'} at market and stop ${running} running bot${running === 1 ? '' : 's'}?`)) {
      return
    }
    try {
      const r = await stopAndCloseAllBots()
      invalidateBots()
      setError(r.failed.length ? `Closed ${r.closed.length}, ${r.failed.length} failed` : '')
    } catch (e) {
      setError(apiErrorDetail(e, 'Failed to stop and close all bots'))
    }
  }

  const alignedRange = useMemo(() => {
    if (sparklineScale !== 'aligned') return undefined
    const times = bots
      .map(b => b.first_trade_time)
      .filter((t): t is string => !!t)
      .map(t => Math.floor(new Date(t).getTime() / 1000))
    if (times.length === 0) return undefined
    return { from: Math.min(...times), to: Math.floor(Date.now() / 1000) }
  }, [bots, sparklineScale])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '10px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '0 2px', flexWrap: 'wrap' }}>
        <div style={{ color: '#e6edf3', fontWeight: 700, fontSize: 14 }}>
          Live Trading Bots
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button onClick={handleStartAll} style={btnStyle('#1a3a2a')}>Start All</button>
          <button onClick={handleStopAll} style={btnStyle('#3a1a1a')}>Stop All</button>
          <button onClick={handleStopAndCloseAll} style={btnStyle('#5a1a1a')}>Stop and Close</button>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <label style={{ fontSize: 11, color: '#8b949e', whiteSpace: 'nowrap' }}>Poll (ms)</label>
          <input
            type="number"
            value={pollInput}
            onChange={e => setPollInput(e.target.value)}
            onFocus={() => setPollFocused(true)}
            onBlur={() => { setPollFocused(false); handlePollIntervalCommit() }}
            onKeyDown={e => { if (e.key === 'Enter') { e.currentTarget.blur() } }}
            placeholder="auto"
            min={100}
            max={60000}
            style={{
              ...inputStyle,
              width: 72,
              fontSize: 11,
              padding: '3px 6px',
            }}
          />
        </div>
        <div style={{ display: 'flex', gap: 4, background: '#0d1117', border: '1px solid #1e2530', borderRadius: 4, padding: 2 }}>
          {(['expanded', 'compact'] as const).map(mode => (
            <button
              key={mode}
              onClick={() => setCompactMode(mode === 'compact')}
              style={{
                fontSize: 11, padding: '3px 10px', borderRadius: 3, border: 'none', cursor: 'pointer',
                background: (mode === 'compact') === compactMode ? '#1e3a5f' : 'transparent',
                color: (mode === 'compact') === compactMode ? '#e6edf3' : '#8b949e',
              }}
            >
              {mode === 'expanded' ? 'Expanded' : 'Compact'}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 4, background: '#0d1117', border: '1px solid #1e2530', borderRadius: 4, padding: 2 }}>
          {(['local', 'aligned'] as const).map(mode => (
            <button
              key={mode}
              onClick={() => setSparklineScale(mode)}
              style={{
                fontSize: 11, padding: '3px 10px', borderRadius: 3, border: 'none', cursor: 'pointer',
                background: sparklineScale === mode ? '#1e3a5f' : 'transparent',
                color: sparklineScale === mode ? '#e6edf3' : '#8b949e',
              }}
            >
              {mode === 'local' ? 'Local' : 'Aligned'}
            </button>
          ))}
        </div>
      </div>

      {(error || (botsError && !botsErrorDismissed)) && (
        <div style={{ color: '#ef5350', fontSize: 12, padding: '4px 8px', background: '#1a0d0d', borderRadius: 4 }}>
          {error || 'Could not reach bot API'}
          {error
            ? <button onClick={() => setError('')} style={{ marginLeft: 8, background: 'none', border: 'none', color: '#ef5350', cursor: 'pointer' }}>×</button>
            : <button onClick={() => setBotsErrorDismissed(true)} style={{ marginLeft: 8, background: 'none', border: 'none', color: '#ef5350', cursor: 'pointer' }}>×</button>
          }
        </div>
      )}

      {anyBrokerUnhealthy && !brokerBannerDismissed && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '6px 10px', fontSize: 12,
          background: 'rgba(240, 183, 78, 0.1)', border: '1px solid rgba(240, 183, 78, 0.3)',
          borderRadius: 4, color: '#f0b74e',
        }}>
          <span style={{ fontWeight: 600 }}>Broker issue</span>
          <span style={{ color: '#c9a84e' }}>
            {Object.entries(health).filter(([, h]) => !h.healthy).map(([name]) => name.toUpperCase()).join(', ')} unhealthy — polling slowed to 10s
          </span>
          <button
            onClick={() => setBrokerBannerDismissed(true)}
            style={{ marginLeft: 'auto', background: 'none', border: 'none', color: '#f0b74e', cursor: 'pointer', fontSize: 14 }}
          >×</button>
        </div>
      )}

      <FundBar fund={fund} onSetFund={handleSetFund} />
      <AddBotBar fund={fund} onAdd={handleAdd} />

      {bots.some(b => (b.equity_snapshots?.length ?? 0) > 0) && (
        <PortfolioStrip bots={bots} alignedRange={alignedRange} />
      )}

      {bots.length === 0 && fund && fund.bot_fund > 0 && (
        <div style={{ color: '#555', fontSize: 13, padding: '8px 12px' }}>
          No bots yet. Add one above.
        </div>
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={orderedBots.map(b => b.bot_id)} strategy={verticalListSortingStrategy}>
          {orderedBots.map(bot => (
            <SortableBotCard
              key={bot.bot_id}
              botId={bot.bot_id}
              summary={bot}
              alignedRange={alignedRange}
              compact={compactMode}
              onStart={() => handleStart(bot.bot_id)}
              onStop={() => handleStop(bot.bot_id)}
              onBacktest={() => handleBacktest(bot.bot_id)}
              onDelete={() => handleDelete(bot.bot_id)}
              onManualBuy={() => handleManualBuy(bot.bot_id)}
              onUpdate={(updates) => handleUpdate(bot.bot_id, updates)}
              onResetPnl={() => handleResetPnl(bot.bot_id)}
            />
          ))}
        </SortableContext>
      </DndContext>
    </div>
  )
}
