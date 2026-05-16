import SignalScanner from './SignalScanner'
import PerformanceComparison from './PerformanceComparison'

const PREVIEW_CARDS = [
  {
    title: "Today's movers",
    lines: ["Top gainers and losers by % change", "Filtered by min. volume threshold", "Cross-referenced against watchlist"],
  },
  {
    title: "Volume leaders",
    lines: ["Unusual volume vs 20-day average", "Sorted by relative volume ratio", "Highlights pre-breakout accumulation"],
  },
  {
    title: "Breakout candidates",
    lines: ["Price near 52-week high with range contraction", "Requires strategy signal confirmation", "Scores by ATR-normalized proximity"],
  },
]

export default function Discovery({ onSpawnBot }: { onSpawnBot?: (symbol: string, strategyName: string) => void }) {
  return (
    <div style={styles.container}>
      <SignalScanner onSpawnBot={onSpawnBot} />
      <PerformanceComparison />
      <div style={styles.previewSection}>
        <div style={styles.previewHeader}>
          Preview only — scanner coming soon
        </div>
        <div style={styles.cardGrid}>
          {PREVIEW_CARDS.map(card => (
            <div key={card.title} style={styles.card}>
              <div style={styles.cardTitle}>{card.title}</div>
              {card.lines.map((line, i) => (
                <div key={i} style={styles.cardLine}>{line}</div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    height: '100%', overflowY: 'auto', background: '#0d1117',
  },
  previewSection: {
    padding: '24px 16px 32px',
    borderTop: '1px solid #21262d',
  },
  previewHeader: {
    fontSize: 14, fontWeight: 600, color: 'var(--text-secondary, #8b949e)',
    marginBottom: 16, letterSpacing: '0.01em',
  },
  cardGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 16,
  },
  card: {
    background: '#161b22', border: '1px solid #21262d',
    borderRadius: 8, padding: '14px 16px',
  },
  cardTitle: {
    fontSize: 12, fontWeight: 600,
    color: 'var(--text-secondary, #8b949e)',
    marginBottom: 10,
  },
  cardLine: {
    fontSize: 11, color: 'var(--text-muted, #484f58)',
    lineHeight: '1.6',
  },
}
