import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listBots } from '../../api/bots'
import type { BotListResponse } from '../types'
import {
  fetchJournal, fetchPositions, fetchAccount, fetchOrders,
  type BrokerInfo, type JournalTrade, type StaleAware, type Position, type Order, type Account,
} from '../../api/trading'

// Reads adaptive interval from cached broker health — 10s when any broker is down, else normalMs.
// Avoids coupling individual query hooks to the useBroker hook.
function adaptiveMs(queryClient: ReturnType<typeof useQueryClient>, normalMs: number): number {
  const brokerData = queryClient.getQueryData<BrokerInfo>(['broker'])
  const anyUnhealthy = brokerData
    ? Object.values(brokerData.health).some(h => !h.healthy)
    : false
  return anyUnhealthy ? Math.max(normalMs, 10_000) : normalMs
}

export function useBotsQuery() {
  const qc = useQueryClient()
  return useQuery<BotListResponse>({
    queryKey: ['bots'],
    queryFn: listBots,
    staleTime: 0,
    refetchInterval: () => adaptiveMs(qc, 5_000),
    refetchIntervalInBackground: false,
  })
}

export function useJournalQuery(brokerFilter: string = 'all') {
  const qc = useQueryClient()
  return useQuery<JournalTrade[]>({
    queryKey: ['journal', brokerFilter],
    queryFn: ({ signal }) => fetchJournal(undefined, brokerFilter, signal),
    staleTime: 0,
    refetchInterval: () => adaptiveMs(qc, 5_000),
    refetchIntervalInBackground: false,
  })
}

export function usePositionsQuery(brokerFilter: string = 'all') {
  const qc = useQueryClient()
  return useQuery<StaleAware<Position>>({
    queryKey: ['positions', brokerFilter],
    queryFn: ({ signal }) => fetchPositions(brokerFilter, signal),
    staleTime: 0,
    refetchInterval: () => adaptiveMs(qc, 5_000),
    refetchIntervalInBackground: false,
  })
}

export function useAccountQuery() {
  return useQuery<Account>({
    queryKey: ['account'],
    queryFn: ({ signal }) => fetchAccount(signal),
    staleTime: 0,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })
}

export function useOrdersQuery(brokerFilter: string = 'all') {
  return useQuery<StaleAware<Order>>({
    queryKey: ['orders', brokerFilter],
    queryFn: ({ signal }) => fetchOrders(brokerFilter, signal),
    staleTime: 0,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })
}
