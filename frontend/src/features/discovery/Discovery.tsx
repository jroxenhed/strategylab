import SignalScanner from './SignalScanner'
import PerformanceComparison from './PerformanceComparison'

export default function Discovery() {
  return (
    <div style={styles.container}>
      <SignalScanner />
      <PerformanceComparison />
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    height: '100%', overflowY: 'auto', background: '#0d1117',
  },
}
