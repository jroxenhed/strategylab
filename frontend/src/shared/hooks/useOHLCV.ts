import { useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { fetchBroker, setBroker as setBrokerApi, type BrokerInfo } from '../../api/trading'
import type { OHLCVBar, DataSource, IndicatorInstance } from '../types'

export function useOHLCV(ticker: string, start: string, end: string, interval: string, source: DataSource = 'yahoo', extendedHours: boolean = false) {
  return useQuery<OHLCVBar[]>({
    queryKey: ['ohlcv', ticker, start, end, interval, source, extendedHours],
    queryFn: async () => {
      const { data } = await api.get(`/api/ohlcv/${ticker}`, { params: { start, end, interval, source, extended_hours: extendedHours } })
      return data.data
    },
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  })
}

export function useInstanceIndicators(
  ticker: string,
  start: string,
  end: string,
  interval: string,
  instances: IndicatorInstance[],
  source: DataSource = 'yahoo',
  extendedHours: boolean = false,
) {
  const enabledInstances = instances.filter(i => i.enabled)
  const instancesQueryKey = enabledInstances.map(i => ({ id: i.id, type: i.type, params: i.params }))

  return useQuery<Record<string, Record<string, { time: string; value: number | null }[]>>>({
    queryKey: ['instance-indicators', ticker, start, end, interval, instancesQueryKey, source, extendedHours],
    queryFn: async () => {
      const { data } = await api.post(`/api/indicators/${ticker}`, {
        start,
        end,
        interval,
        source,
        extended_hours: extendedHours,
        instances: enabledInstances.map(i => ({ id: i.id, type: i.type, params: i.params })),
      })
      return data
    },
    enabled: !!ticker && enabledInstances.length > 0,
    staleTime: 5 * 60 * 1000,
  })
}

export function useProviders() {
  return useQuery<string[]>({
    queryKey: ['providers'],
    queryFn: async () => {
      const { data } = await api.get('/api/providers')
      return data.providers
    },
    staleTime: 60 * 1000,
  })
}

export function useSearch(q: string) {
  return useQuery({
    queryKey: ['search', q],
    queryFn: async () => {
      const { data } = await api.get('/api/search', { params: { q } })
      return data
    },
    enabled: q.length > 1,
    staleTime: 60 * 1000,
  })
}

export function useBroker() {
  const queryClient = useQueryClient()

  const query = useQuery<BrokerInfo>({
    queryKey: ['broker'],
    queryFn: fetchBroker,
    staleTime: 10_000,
    refetchInterval: 10_000,
  })

  const switchBroker = async (broker: string) => {
    const result = await setBrokerApi(broker)
    queryClient.setQueryData(['broker'], result)
    queryClient.invalidateQueries({ queryKey: ['account'] })
    queryClient.invalidateQueries({ queryKey: ['positions'] })
    queryClient.invalidateQueries({ queryKey: ['orders'] })
    return result
  }

  const health = query.data?.health ?? {}
  const anyUnhealthy = Object.values(health).some(h => !h.healthy)

  // Stable identity so consumers that list this in useEffect deps don't
  // clear+reinstall their polling timers on every broker refetch.
  const adaptiveInterval = useCallback(
    (normalMs: number) => anyUnhealthy ? Math.max(normalMs, 10_000) : normalMs,
    [anyUnhealthy],
  )

  return {
    broker: query.data?.active ?? 'alpaca',
    available: query.data?.available ?? [],
    health,
    heartbeatWarmup: query.data?.heartbeat_warmup ?? false,
    anyBrokerUnhealthy: anyUnhealthy,
    /** Use instead of a hardcoded interval — backs off when a broker is down */
    adaptiveInterval,
    isLoading: query.isLoading,
    switchBroker,
  }
}
