import { useCallback } from 'react'
import { useQuery, useQueries, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { fetchBroker, setBroker as setBrokerApi, type BrokerInfo } from '../../api/trading'
import type { OHLCVBar, DataSource, IndicatorInstance } from '../types'

export function useOHLCV(ticker: string, start: string, end: string, interval: string, source: DataSource = 'yahoo', extendedHours: boolean = false, enabled: boolean = true) {
  return useQuery<OHLCVBar[]>({
    queryKey: ['ohlcv', ticker, start, end, interval, source, extendedHours],
    queryFn: async () => {
      const { data } = await api.get(`/api/ohlcv/${ticker}`, { params: { start, end, interval, source, extended_hours: extendedHours } })
      return data.data
    },
    enabled: !!ticker && enabled,
    staleTime: 5 * 60 * 1000,
  })
}

type IndicatorData = Record<string, Record<string, { time: string; value: number | null }[]>>

export function useInstanceIndicators(
  ticker: string,
  start: string,
  end: string,
  interval: string,
  instances: IndicatorInstance[],
  source: DataSource = 'yahoo',
  extendedHours: boolean = false,
  viewInterval?: string,
) {
  const enabledInstances = instances.filter(i => i.enabled)
  const regularInstances = enabledInstances.filter(i => !i.htfInterval)

  // Group HTF instances by their htfInterval
  const htfGroupsMap = new Map<string, IndicatorInstance[]>()
  for (const inst of enabledInstances.filter(i => !!i.htfInterval)) {
    const key = inst.htfInterval!
    const group = htfGroupsMap.get(key)
    if (group) group.push(inst)
    else htfGroupsMap.set(key, [inst])
  }
  const htfGroups = Array.from(htfGroupsMap.entries())

  const regularQueryKey = regularInstances.map(i => ({ id: i.id, type: i.type, params: i.params }))

  const regularQuery = useQuery<IndicatorData>({
    queryKey: ['instance-indicators', ticker, start, end, interval, viewInterval, regularQueryKey, source, extendedHours],
    queryFn: async () => {
      const body: Record<string, unknown> = {
        start, end, interval, source, extended_hours: extendedHours,
        instances: regularInstances.map(i => ({ id: i.id, type: i.type, params: i.params })),
      }
      if (viewInterval && viewInterval !== interval) {
        body.view_interval = viewInterval
      }
      const { data } = await api.post(`/api/indicators/${ticker}`, body)
      return data
    },
    enabled: !!ticker && regularInstances.length > 0,
    staleTime: 5 * 60 * 1000,
  })

  const htfQueryResults = useQueries({
    queries: htfGroups.map(([htfInterval, insts]) => ({
      queryKey: [
        'instance-indicators-htf', ticker, start, end, interval, htfInterval,
        insts.map(i => ({ id: i.id, type: i.type, params: i.params })), source, extendedHours,
      ],
      queryFn: async (): Promise<IndicatorData> => {
        const { data } = await api.post(`/api/indicators/${ticker}`, {
          start, end, interval, source, extended_hours: extendedHours,
          htf_interval: htfInterval,
          instances: insts.map(i => ({ id: i.id, type: i.type, params: i.params })),
        })
        return data
      },
      enabled: !!ticker,
      staleTime: 5 * 60 * 1000,
    })),
  })

  const merged: IndicatorData = { ...(regularQuery.data ?? {}) }
  for (const q of htfQueryResults) {
    if (q.data) Object.assign(merged, q.data)
  }

  const refetch = async () => {
    await regularQuery.refetch()
    await Promise.all(htfQueryResults.map(q => q.refetch()))
  }

  const isSuccess =
    (regularInstances.length === 0 || regularQuery.isSuccess) &&
    htfQueryResults.every(q => q.isSuccess)

  const fetchStatus =
    regularInstances.length > 0 ? regularQuery.fetchStatus
    : htfGroups.length > 0 ? (htfQueryResults[0]?.fetchStatus ?? 'idle')
    : 'idle'

  return { data: merged, refetch, isSuccess, fetchStatus }
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
