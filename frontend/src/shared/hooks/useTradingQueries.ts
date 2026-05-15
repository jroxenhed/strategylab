import { useQuery } from '@tanstack/react-query'
import { listBots } from '../../api/bots'
import type { BotListResponse } from '../types'
import {
  fetchJournal, fetchPositions, fetchAccount, fetchOrders,
  type JournalTrade, type StaleAware, type Position, type Order, type Account,
} from '../../api/trading'

// Polling for these two queries is owned by a single setInterval in
// PaperTrading that calls invalidateQueries(['bots']) / (['journal']).
// tanstack dedupes the refetch across observers, so 1 fetch/cycle no
// matter how many components subscribe — vs the previous per-observer
// refetchInterval that fired N independent timers and N fetches.
export function useBotsQuery() {
  return useQuery<BotListResponse>({
    queryKey: ['bots'],
    queryFn: ({ signal }) => listBots(signal),
    staleTime: 0,
  })
}

export function useJournalQuery(brokerFilter: string = 'all', limit?: number) {
  return useQuery<JournalTrade[]>({
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
    refetchInterval: 5_000,
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
