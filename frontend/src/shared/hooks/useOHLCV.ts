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

export function useIndicators(
  ticker: string, start: string, end: string, interval: string,
  indicators: string[], source: DataSource = 'yahoo',
  maSettings?: { type: string; sg8Window: number; sg8Poly: number; sg21Window: number; sg21Poly: number; predictiveSg?: boolean },
) {
  return useQuery({
    queryKey: ['indicators', ticker, start, end, interval, indicators.join(','), source, maSettings],
    queryFn: async () => {
      const params: Record<string, string | number> = { start, end, interval, indicators: indicators.join(','), source }
      if (maSettings) {
        params.ma_type = maSettings.type
        params.sg8_window = maSettings.sg8Window
        params.sg8_poly = maSettings.sg8Poly
        params.sg21_window = maSettings.sg21Window
        params.sg21_poly = maSettings.sg21Poly
        if (maSettings.predictiveSg) params.predictive_sg = 1
      }
      const { data } = await api.get(`/api/indicators/${ticker}`, { params })
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
