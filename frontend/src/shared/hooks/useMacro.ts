import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { MacroResponse, StrategyRequest } from '../types'

export function useMacro(request: StrategyRequest | null, bucket: string | null) {
  return useQuery<MacroResponse>({
    queryKey: ['macro', bucket, request ? JSON.stringify(request) : ''],
    queryFn: async () => {
      const { data } = await api.post(`/api/backtest/macro?macro_bucket=${bucket}`, request)
      return data
    },
    enabled: !!request && !!bucket,
    staleTime: Infinity,
  })
}
