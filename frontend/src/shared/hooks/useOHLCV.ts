import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { OHLCVBar, DataSource } from '../types'

export function useOHLCV(ticker: string, start: string, end: string, interval: string, source: DataSource = 'yahoo') {
  return useQuery<OHLCVBar[]>({
    queryKey: ['ohlcv', ticker, start, end, interval, source],
    queryFn: async () => {
      const { data } = await api.get(`/api/ohlcv/${ticker}`, { params: { start, end, interval, source } })
      return data.data
    },
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  })
}

export function useIndicators(ticker: string, start: string, end: string, interval: string, indicators: string[], source: DataSource = 'yahoo') {
  return useQuery({
    queryKey: ['indicators', ticker, start, end, interval, indicators.join(','), source],
    queryFn: async () => {
      const { data } = await api.get(`/api/indicators/${ticker}`, {
        params: { start, end, interval, indicators: indicators.join(','), source }
      })
      return data
    },
    enabled: !!ticker && indicators.length > 0,
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
