/**
 * AutoRenderToggle — toolbar button for Graph View / Back to Chart toggle.
 * Unit 4a: read-only canvas viewer.
 */

interface AutoRenderToggleProps {
  active: boolean
  onToggle: () => void
}

export default function AutoRenderToggle({ active, onToggle }: AutoRenderToggleProps) {
  return (
    <button
      onClick={onToggle}
      style={{
        fontSize: 11,
        padding: '3px 10px',
        borderRadius: 4,
        background: active ? 'rgba(88,166,255,0.15)' : '#21262d',
        color: active ? '#58a6ff' : '#8b949e',
        border: active ? '1px solid #58a6ff55' : '1px solid #30363d',
        cursor: 'pointer',
        fontWeight: 600,
        whiteSpace: 'nowrap',
      }}
      title={active ? 'Switch back to chart view' : 'View current strategy as a node graph'}
    >
      {active ? 'Back to Chart' : 'View as Graph'}
    </button>
  )
}
