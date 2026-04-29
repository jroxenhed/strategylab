import React from 'react'

// ---------------------------------------------------------------------------
// Shared layout constants
// ---------------------------------------------------------------------------

export const CARD_COLUMN_FLEX = '1 1 50%'
export const INFO_COLUMN_FLEX = '0 0 35%'

// ---------------------------------------------------------------------------
// StatCell — label above value
// ---------------------------------------------------------------------------

export function StatCell({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
      <span style={{ color: '#666', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</span>
      <span style={{ color: '#aaa', fontSize: 12 }}>{value}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shared button style (moved from BotCard to avoid cross-file coupling)
// ---------------------------------------------------------------------------

export function btnStyle(bg: string, disabled = false): React.CSSProperties {
  return {
    background: disabled ? '#1a1a1a' : bg,
    color: disabled ? '#444' : '#ccc',
    border: '1px solid #2a3040',
    borderRadius: 4,
    padding: '4px 10px',
    fontSize: 12,
    cursor: disabled ? 'not-allowed' : 'pointer',
  }
}
