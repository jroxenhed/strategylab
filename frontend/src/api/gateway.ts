import { api } from './client'

export type GatewayState =
  | 'logged_in'
  | 'awaiting_2fa'
  | 'relogin_required'
  | 'locked_out'
  | 'bad_credentials'
  | 'restarting'
  | 'logging_in'
  | 'down'
  | 'unknown'

export interface GatewayCommandPort {
  host: string
  port: number
  reachable: boolean
}

export interface GatewayStatus {
  state: GatewayState
  since: string | null
  log_file: string | null
  last_lines: string[]
  api_connected: boolean
  command_port: GatewayCommandPort
}

export type GatewayCommand = 'RESTART' | 'RECONNECTACCOUNT' | 'RECONNECTDATA' | 'STOP'

export interface GatewayCommandReply {
  ok: boolean
  reply: string
}

export async function getGatewayStatus(): Promise<GatewayStatus> {
  const res = await api.get('/api/gateway/status')
  return res.data
}

export async function sendGatewayCommand(cmd: GatewayCommand): Promise<GatewayCommandReply> {
  const res = await api.post(`/api/gateway/command/${cmd}`)
  return res.data
}
