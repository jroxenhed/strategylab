import { useEffect, useMemo, useState } from 'react'
import type { BotSummary, BotFundStatus } from '../../shared/types'
import {
  listBots, setBotFund, addBot,
  startBot, stopBot, backtestBot, deleteBot, manualBuyBot, updateBot, resetBotPnl,
  startAllBots, stopAllBots, stopAndCloseAllBots,
} from '../../api/bots'
import { fmtUsd } from '../../shared/utils/format'
import BotCard, { btnStyle } from './BotCard'
import AddBotBar, { sectionStyle, inputStyle } from './AddBotBar'
import { useBroker } from '../../shared/hooks/useOHLCV'

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
// BotControlCenter — main component
// ---------------------------------------------------------------------------

export default function BotControlCenter() {
  const { adaptiveInterval, anyBrokerUnhealthy, health } = useBroker()
  const [brokerBannerDismissed, setBrokerBannerDismissed] = useState(false)
  const [fund, setFund] = useState<BotFundStatus | null>(null)
  const [bots, setBots] = useState<BotSummary[]>([])
  const [error, setError] = useState('')
  const [sparklineScale, setSparklineScale] = useState<'local' | 'aligned'>(() => {
    const v = localStorage.getItem('sparklineScale')
    return v === 'aligned' ? 'aligned' : 'local'
  })
  useEffect(() => {
    localStorage.setItem('sparklineScale', sparklineScale)
  }, [sparklineScale])
  useEffect(() => {
    if (!anyBrokerUnhealthy) setBrokerBannerDismissed(false)
  }, [anyBrokerUnhealthy])

  const loadBots = async () => {
    if (document.hidden) return
    try {
      const data = await listBots()
      setFund(data.fund)
      setBots(data.bots)
      setError('')
    } catch {
      setError('Could not reach bot API')
    }
  }

  useEffect(() => {
    loadBots()
    const id = setInterval(loadBots, adaptiveInterval(5000))
    return () => clearInterval(id)
  }, [adaptiveInterval])

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

  const handleResetPnl = async (botId: string) => {
    try { await resetBotPnl(botId); await loadBots() }
    catch (e: any) { setError(e?.response?.data?.detail ?? 'Failed to reset P&L') }
  }

  const handleUpdate = async (botId: string, updates: Record<string, unknown>) => {
    try { await updateBot(botId, updates); await loadBots() }
    catch (e: any) { setError(e?.response?.data?.detail ?? 'Failed to update bot') }
  }

  const handleStartAll = async () => {
    try {
      const r = await startAllBots()
      await loadBots()
      setError(r.failed.length ? `Started ${r.started.length}, ${r.failed.length} failed` : '')
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to start all bots')
    }
  }

  const handleStopAll = async () => {
    try {
      const r = await stopAllBots()
      await loadBots()
      setError(r.failed.length ? `Stopped ${r.stopped.length}, ${r.failed.length} failed` : '')
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to stop all bots')
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
      await loadBots()
      setError(r.failed.length ? `Closed ${r.closed.length}, ${r.failed.length} failed` : '')
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Failed to stop and close all bots')
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

      {error && (
        <div style={{ color: '#ef5350', fontSize: 12, padding: '4px 8px', background: '#1a0d0d', borderRadius: 4 }}>
          {error}
          <button onClick={() => setError('')} style={{ marginLeft: 8, background: 'none', border: 'none', color: '#ef5350', cursor: 'pointer' }}>×</button>
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

      {bots.length === 0 && fund && fund.bot_fund > 0 && (
        <div style={{ color: '#555', fontSize: 13, padding: '8px 12px' }}>
          No bots yet. Add one above.
        </div>
      )}

      {bots.map(bot => (
        <BotCard
          key={bot.bot_id}
          summary={bot}
          alignedRange={alignedRange}
          onStart={() => handleStart(bot.bot_id)}
          onStop={() => handleStop(bot.bot_id)}
          onBacktest={() => handleBacktest(bot.bot_id)}
          onDelete={() => handleDelete(bot.bot_id)}
          onManualBuy={() => handleManualBuy(bot.bot_id)}
          onUpdate={(updates) => handleUpdate(bot.bot_id, updates)}
          onResetPnl={() => handleResetPnl(bot.bot_id)}
        />
      ))}
    </div>
  )
}
