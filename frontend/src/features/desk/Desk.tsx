import { useState } from 'react'
import PremiseLibrary from './PremiseLibrary'

type DeskSubTab = 'premises' | 'inbox' | 'playbooks' | 'tracking'

export default function Desk() {
  const [subTab, setSubTab] = useState<DeskSubTab>('premises')

  return (
    <div style={styles.container}>
      {/* Sub-tab bar */}
      <div style={styles.subTabBar}>
        {(['premises', 'inbox', 'playbooks', 'tracking'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setSubTab(tab)}
            style={{
              ...styles.subTab,
              ...(subTab === tab ? styles.subTabActive : {}),
            }}
          >
            {tab === 'premises' ? 'Premises' : tab === 'inbox' ? 'Inbox' : tab === 'playbooks' ? 'Playbooks' : 'Tracking'}
            {tab !== 'premises' && (
              <span style={styles.laterBadge}>later</span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={styles.content}>
        {subTab === 'premises' && <PremiseLibrary />}
        {subTab !== 'premises' && (
          <div style={styles.stub}>Coming later</div>
        )}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    background: '#0d1117',
    overflow: 'hidden',
  },
  subTabBar: {
    display: 'flex',
    gap: 2,
    padding: '0 16px',
    background: '#0d1117',
    borderBottom: '1px solid #21262d',
    flexShrink: 0,
  },
  subTab: {
    fontSize: 12,
    padding: '10px 14px',
    background: 'transparent',
    color: '#8b949e',
    cursor: 'pointer',
    fontWeight: 600,
    border: 'none',
    borderBottom: '2px solid transparent',
    transition: 'color 0.15s ease',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  subTabActive: {
    color: '#58a6ff',
    borderBottom: '2px solid #58a6ff',
  },
  laterBadge: {
    fontSize: 9,
    fontWeight: 500,
    color: '#484f58',
    background: '#161b22',
    border: '1px solid #21262d',
    borderRadius: 3,
    padding: '1px 4px',
    letterSpacing: '0.02em',
  },
  content: {
    flex: 1,
    overflow: 'hidden',
    minHeight: 0,
  },
  stub: {
    padding: 32,
    color: '#484f58',
    fontSize: 12,
  },
}
