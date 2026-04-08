import { useQuery } from '@tanstack/react-query'
import axios from 'axios'
import type { OHLCVBar, DataSource } from '../types'

const API = 'http://localhost:8000'

export function useOHLCV(ticker: string, start: string, end: string, interval: string, source: DataSource = 'yahoo') {
  return useQuery<OHLCVBar[]>({
    queryKey: ['ohlcv', ticker, start, end, interval, source],
    queryFn: async () => {
      const { data } = await axios.get(`${API}/api/ohlcv/${ticker}`, { params: { start, end, interval, source } })
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
      const { data } = await axios.get(`${API}/api/indicators/${ticker}`, {
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
      const { data } = await axios.get(`${API}/api/providers`)
      return data.providers
    },
    staleTime: 60 * 1000,
  })
}

export function useSearch(q: string) {
  return useQuery({
    queryKey: ['search', q],
    queryFn: async () => {
      const { data } = await axios.get(`${API}/api/search`, { params: { q } })
      return data
    },
    enabled: q.length > 1,
    staleTime: 60 * 1000,
  })
}
