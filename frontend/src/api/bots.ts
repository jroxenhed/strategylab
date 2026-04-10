import { api } from './client'
import type { BotConfig, BotDetail, BotFundStatus, BotListResponse } from '../shared/types'

export async function getBotFund(): Promise<BotFundStatus> {
  const res = await api.get('/api/bots/fund')
  return res.data
}

export async function setBotFund(amount: number): Promise<BotFundStatus> {
  const res = await api.put('/api/bots/fund', { amount })
  return res.data
}

export async function addBot(config: Omit<BotConfig, 'bot_id'>): Promise<{ bot_id: string }> {
  const res = await api.post('/api/bots', config)
  return res.data
}

export async function listBots(): Promise<BotListResponse> {
  const res = await api.get('/api/bots')
  return res.data
}

export async function fetchBotDetail(botId: string): Promise<BotDetail> {
  const res = await api.get(`/api/bots/${botId}`)
  return res.data
}

export async function startBot(botId: string): Promise<void> {
  await api.post(`/api/bots/${botId}/start`)
}

export async function stopBot(botId: string, close = false): Promise<void> {
  await api.post(`/api/bots/${botId}/stop`, null, { params: { close } })
}

export async function backtestBot(botId: string): Promise<void> {
  await api.post(`/api/bots/${botId}/backtest`)
}

export async function updateBot(botId: string, updates: Record<string, unknown>): Promise<void> {
  await api.patch(`/api/bots/${botId}`, updates)
}

export async function manualBuyBot(botId: string): Promise<{ qty: number; fill_price: number; slippage_pct: number }> {
  const res = await api.post(`/api/bots/${botId}/buy`)
  return res.data
}

export async function deleteBot(botId: string): Promise<void> {
  await api.delete(`/api/bots/${botId}`)
}
