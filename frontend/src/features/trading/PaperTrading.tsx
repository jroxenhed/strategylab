import AccountBar from './AccountBar'
import BotControlCenter from './BotControlCenter'
import PositionsTable from './PositionsTable'
import TradeJournal from './TradeJournal'
import OrderHistory from './OrderHistory'

export default function PaperTrading() {
  return (
    <div style={styles.container}>
      <AccountBar />
      <BotControlCenter />
      <PositionsTable />
      <TradeJournal />
      <OrderHistory />
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    height: '100%', overflowY: 'auto', background: '#0d1117',
  },
}
