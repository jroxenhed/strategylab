import { useState, useCallback, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import AccountBar from './AccountBar'
import BotControlCenter from './BotControlCenter'
import PositionsTable from './PositionsTable'
import TradeJournal from './TradeJournal'
import OrderHistory from './OrderHistory'
import { useLocalStorage } from '../../shared/hooks/useLocalStorage'
import { useBroker } from '../../shared/hooks/useOHLCV'

export default function PaperTrading() {
  const { available, health, heartbeatWarmup } = useBroker()
  const [brokerFilter, setBrokerFilter] = useLocalStorage<string>('paper.brokerFilter', 'all')
  const [stale, setStale] = useState<string[]>([])
  const [dismissed, setDismissed] = useState(false)

  // Single polling owner for journal + bots — invalidate triggers a deduped
  // refetch on all subscribers, so 1 fetch/cycle no matter how many components
  // read the data. Replaces per-observer refetchInterval (which fired N timers).
  const qc = useQueryClient()
  useEffect(() => {
    const tick = () => {
      if (document.hidden) return
      qc.invalidateQueries({ queryKey: ['journal'] })
      qc.invalidateQueries({ queryKey: ['bots'] })
    }
    const id = window.setInterval(tick, 5_000)
    return () => window.clearInterval(id)
  }, [qc])

  const onStale = useCallback((list: string[]) => {
    setStale(prev => {
      const a = prev.join(',')
      const b = list.join(',')
      return a === b ? prev : list
    })
  }, [])

  const visibleStale = dismissed ? [] : stale

  return (
    <div style={styles.container}>
      {visibleStale.length > 0 && (
        <div style={styles.banner}>
          <span>
            ⚠ Stale broker{visibleStale.length > 1 ? 's' : ''}: {visibleStale.map(b => b.toUpperCase()).join(', ')} — data omitted.
          </span>
          <button style={styles.dismiss} onClick={() => setDismissed(true)}>dismiss</button>
        </div>
      )}
      <AccountBar />
      <BotControlCenter />
      <PositionsTable
        brokerFilter={brokerFilter}
        onBrokerFilterChange={setBrokerFilter}
        availableBrokers={available}
        health={health}
        heartbeatWarmup={heartbeatWarmup}
        onStale={onStale}
      />
      <TradeJournal
        brokerFilter={brokerFilter}
        onBrokerFilterChange={setBrokerFilter}
        availableBrokers={available}
        health={health}
        heartbeatWarmup={heartbeatWarmup}
      />
      <OrderHistory
        brokerFilter={brokerFilter}
        onBrokerFilterChange={setBrokerFilter}
        availableBrokers={available}
        health={health}
        heartbeatWarmup={heartbeatWarmup}
        onStale={onStale}
      />
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    height: '100%', overflowY: 'auto', background: '#0d1117',
  },
  banner: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    gap: 8, padding: '6px 16px',
    background: 'rgba(248, 81, 73, 0.12)', color: '#f85149',
    fontSize: 12, borderBottom: '1px solid #30363d',
  },
  dismiss: {
    background: 'none', border: '1px solid #30363d', color: '#f85149',
    fontSize: 11, padding: '2px 8px', borderRadius: 4, cursor: 'pointer',
  },
}
