import AccountBar from './AccountBar'
import PositionsTable from './PositionsTable'
import SignalScanner from './SignalScanner'
import PerformanceComparison from './PerformanceComparison'
import TradeJournal from './TradeJournal'
import OrderHistory from './OrderHistory'

export default function PaperTrading() {
  return (
    <div style={styles.container}>
      <AccountBar />
      <PositionsTable />
      <SignalScanner />
      <PerformanceComparison />
      <TradeJournal />
      <OrderHistory />
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column', flex: 1,
    overflow: 'hidden', background: '#0d1117',
  },
}
