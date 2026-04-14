import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

export interface EmpiricalSlippage {
  empirical_pct: number | null
  fill_count: number
}

export function useEmpiricalSlippage(symbol: string) {
  return useQuery<EmpiricalSlippage>({
    queryKey: ['slippage', symbol.toUpperCase()],
    queryFn: async () => {
      const { data } = await api.get(`/api/slippage/${symbol.toUpperCase()}`)
      return data
    },
    enabled: !!symbol,
    staleTime: 60 * 1000,
  })
}
