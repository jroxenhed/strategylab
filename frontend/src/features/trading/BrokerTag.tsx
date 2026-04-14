import type { BrokerHealth } from '../../api/trading'

interface Props {
  name: string
  health?: BrokerHealth
  warmingUp?: boolean
}

const COLORS: Record<string, string> = {
  alpaca: '#3b82f6',
  ibkr: '#f97316',
}

export function BrokerTag({ name, health, warmingUp }: Props) {
  const color = COLORS[name] ?? '#6b7280'
  const hasHealth = !!health
  const healthy = health?.healthy === true
  const dotColor = warmingUp ? '#9ca3af' : healthy ? '#10b981' : '#ef4444'
  const title = warmingUp
    ? 'Broker heartbeat warming up…'
    : hasHealth
      ? healthy
        ? `OK — last ping ${health!.last_ok_ts ?? '—'}`
        : `Disconnected — ${health!.last_error ?? 'unknown error'}`
      : undefined

  return (
    <span
      title={title}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 8px',
        borderRadius: 999,
        background: `${color}22`,
        color,
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {hasHealth && (
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: dotColor,
          }}
        />
      )}
      {name.toUpperCase()}
    </span>
  )
}
