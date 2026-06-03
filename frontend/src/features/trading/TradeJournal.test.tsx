/**
 * Tests for TradeJournal count chip (F217 / F217b).
 *
 * The chip should:
 *   - Not render at all when data is undefined (loading / error state)
 *   - Show plain total when all trades are loaded (trades.length >= total)
 *   - Show "N of TOTAL" when truncated by the limit
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// vi.mock is hoisted by vitest — factory must be self-contained (no closure over
// top-level consts).  We use vi.hoisted() to create the mocks before hoisting.
const { mockUseJournalQuery, mockUseBotsQuery } = vi.hoisted(() => ({
  mockUseJournalQuery: vi.fn(),
  mockUseBotsQuery: vi.fn(),
}))

vi.mock('../../shared/hooks/useTradingQueries', () => ({
  useJournalQuery: mockUseJournalQuery,
  useBotsQuery: mockUseBotsQuery,
}))

vi.mock('../../api/trading', () => ({
  fetchJournal: vi.fn(),
}))

// Import AFTER mock declarations so the module sees the mock
import TradeJournal from './TradeJournal'

// ── default props ─────────────────────────────────────────────────────────────
const defaultProps = {
  brokerFilter: 'all',
  onBrokerFilterChange: vi.fn(),
  availableBrokers: [],
  health: {},
  heartbeatWarmup: false,
}

function setupBots() {
  mockUseBotsQuery.mockReturnValue({ data: { bots: [] }, refetch: vi.fn() })
}

// ── helper to find the count chip ────────────────────────────────────────────
// The chip is the <span style={styles.count}> rendered immediately after the
// title span in the header div.  We locate it by querying all direct-child
// spans in the header div; first = title, second (when present) = count chip.
function getCountChip() {
  const title = screen.getByText('Trade Journal')
  const header = title.closest('div')
  if (!header) return null
  const spans = header.querySelectorAll(':scope > span')
  // First span is the title; count chip is the second (only if rendered)
  return spans.length > 1 ? spans[1] : null
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('TradeJournal count chip', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Set limit to 200 explicitly: Number(null) === 0 which is a valid LIMIT_OPTIONS entry,
    // so removeItem would cause loadLimit() to return 0 (the "All" sentinel), not the default.
    localStorage.setItem('strategylab-journal-limit', '200')
    setupBots()
  })

  it('renders no chip when data is undefined (loading state)', () => {
    mockUseJournalQuery.mockReturnValue({ data: undefined, refetch: vi.fn() })
    render(<TradeJournal {...defaultProps} />)
    expect(getCountChip()).toBeNull()
  })

  it('shows plain total when all trades are loaded (trades.length >= total)', () => {
    const trade = { id: '1', symbol: 'AAPL', qty: 10, side: 'buy', price: 100, time: '2024-01-02', broker: 'alpaca' }
    mockUseJournalQuery.mockReturnValue({
      data: { trades: [trade], total: 1 },
      refetch: vi.fn(),
    })
    render(<TradeJournal {...defaultProps} />)
    const chip = getCountChip()
    expect(chip).not.toBeNull()
    expect(chip?.textContent).toBe('1')
  })

  it('shows "N of TOTAL" when truncated by the fetch limit', () => {
    const trades = Array.from({ length: 5 }, (_, i) => ({
      id: String(i),
      symbol: 'AAPL',
      qty: 1,
      side: 'buy',
      price: 100,
      time: `2024-01-0${i + 1}`,
      broker: 'alpaca',
    }))
    // total=20 > trades.length=5 → truncated (limit defaults to 200 from localStorage)
    mockUseJournalQuery.mockReturnValue({
      data: { trades, total: 20 },
      refetch: vi.fn(),
    })
    render(<TradeJournal {...defaultProps} />)
    const chip = getCountChip()
    expect(chip).not.toBeNull()
    expect(chip?.textContent).toBe('5 of 20')
  })
})
