import { useQuery } from '@tanstack/react-query'
import { listBots } from '../../api/bots'
import type { BotListResponse } from '../types'
import {
  fetchJournal, fetchPositions, fetchAccount, fetchOrders,
  type JournalResponse, type StaleAware, type Position, type Order, type Account,
} from '../../api/trading'

// Polling for all trading queries is owned by a single setInterval in
// PaperTrading that calls invalidateQueries on each key. tanstack dedupes
// the refetch across observers, so 1 fetch/cycle no matter how many
// components subscribe — vs the previous per-observer refetchInterval
// that fired N independent timers and N fetches.
// Cadences: journal + bots + positions → 5 s; orders + account → 30 s.
export function useBotsQuery() {
  return useQuery<BotListResponse>({
    queryKey: ['bots'],
    queryFn: ({ signal }) => listBots(signal),
    staleTime: 0,
  })
}

export function useJournalQuery(brokerFilter: string = 'all', limit?: number) {
  return useQuery<JournalResponse>({
    queryKey: ['journal', brokerFilter, limit],
    queryFn: ({ signal }) => fetchJournal(undefined, brokerFilter, signal, limit),
    staleTime: 0,
  })
}

export function usePositionsQuery(brokerFilter: string = 'all') {
  return useQuery<StaleAware<Position>>({
    queryKey: ['positions', brokerFilter],
    queryFn: ({ signal }) => fetchPositions(brokerFilter, signal),
    staleTime: 0,
  })
}

export function useAccountQuery() {
  return useQuery<Account>({
    queryKey: ['account'],
    queryFn: ({ signal }) => fetchAccount(signal),
    staleTime: 0,
    // 'always': fetch immediately on every (re)mount — bridges the gap until
    // PaperTrading's 30s setInterval fires its first tick after remount (COR-05).
    refetchOnMount: 'always',
  })
}

export function useOrdersQuery(brokerFilter: string = 'all') {
  return useQuery<StaleAware<Order>>({
    queryKey: ['orders', brokerFilter],
    queryFn: ({ signal }) => fetchOrders(brokerFilter, signal),
    staleTime: 0,
    // 'always': same rationale as useAccountQuery — see COR-05.
    refetchOnMount: 'always',
  })
}
