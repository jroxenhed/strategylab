import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

export interface SlippageInfo {
  modeled_bps: number
  measured_bps: number | null
  fill_bias_bps: number | null
  fill_count: number
  source: 'default' | 'empirical' | 'spread-derived'
  live_spread_bps?: number | null
  half_spread_bps?: number | null
}

export function useSlippage(symbol: string) {
  return useQuery<SlippageInfo>({
    queryKey: ['slippage', symbol.toUpperCase()],
    queryFn: async () => {
      const { data } = await api.get(`/api/slippage/${symbol.toUpperCase()}`)
      return data
    },
    enabled: !!symbol,
    staleTime: 60 * 1000,
  })
}
