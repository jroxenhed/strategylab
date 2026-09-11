/**
 * Tests for GatewayPanel.
 *
 * Mocks:
 *   - ../../api/gateway → getGatewayStatus / sendGatewayCommand
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { GatewayStatus } from '../../api/gateway'

// ---------------------------------------------------------------------------
// Mock: API
// ---------------------------------------------------------------------------

const getGatewayStatus = vi.fn()
const sendGatewayCommand = vi.fn()

vi.mock('../../api/gateway', async () => {
  const actual = await vi.importActual<typeof import('../../api/gateway')>('../../api/gateway')
  return {
    ...actual,
    getGatewayStatus: (...args: unknown[]) => getGatewayStatus(...args),
    sendGatewayCommand: (...args: unknown[]) => sendGatewayCommand(...args),
  }
})

const { default: GatewayPanel } = await import('./GatewayPanel')

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function wrapper({ children }: { children: React.ReactNode }) {
  return createElement(QueryClientProvider, { client: makeQueryClient() }, children)
}

function makeStatus(overrides: Partial<GatewayStatus> = {}): GatewayStatus {
  return {
    state: 'logged_in',
    since: new Date().toISOString(),
    log_file: '/var/log/ibc/ibc-1.txt',
    last_lines: ['Login has completed'],
    api_connected: true,
    command_port: { host: '127.0.0.1', port: 7462, reachable: true },
    ...overrides,
  }
}

function renderPanel() {
  return render(createElement(GatewayPanel), { wrapper })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('GatewayPanel', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders nothing when status fetch 404s', async () => {
    getGatewayStatus.mockRejectedValue({ response: { status: 404 } })
    const { container } = renderPanel()
    await waitFor(() => expect(getGatewayStatus).toHaveBeenCalled())
    expect(container.firstChild).toBeNull()
  })

  it('shows an error indicator when status fetch fails with a non-404', async () => {
    getGatewayStatus.mockRejectedValue({ response: { status: 500 } })
    renderPanel()
    expect(await screen.findByText('Gateway status unavailable')).toBeInTheDocument()
  })

  const stateCases: Array<[GatewayStatus['state'], string]> = [
    ['logged_in', 'Logged in'],
    ['awaiting_2fa', 'Approve on IBKR Mobile'],
    ['relogin_required', 'Re-login required'],
    ['locked_out', 'Locked out'],
    ['bad_credentials', 'Bad credentials'],
    ['restarting', 'Restarting'],
    ['logging_in', 'Logging in'],
    ['down', 'Down'],
    ['unknown', 'Unknown'],
  ]

  it.each(stateCases)('renders chip label for state %s', async (state, label) => {
    getGatewayStatus.mockResolvedValue(makeStatus({ state }))
    renderPanel()
    expect(await screen.findByText(label)).toBeInTheDocument()
  })

  it('shows confirm prompt before calling RESTART', async () => {
    getGatewayStatus.mockResolvedValue(makeStatus())
    sendGatewayCommand.mockResolvedValue({ ok: true, reply: 'OK' })
    renderPanel()
    await screen.findByText('Logged in')

    await userEvent.click(screen.getByRole('button', { name: /^restart$/i }))
    expect(screen.getByText(/Restart the Gateway\?/i)).toBeInTheDocument()
    expect(sendGatewayCommand).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: /^yes$/i }))
    await waitFor(() => expect(sendGatewayCommand).toHaveBeenCalledWith('RESTART'))
  })

  it('cancels restart confirm on No', async () => {
    getGatewayStatus.mockResolvedValue(makeStatus())
    renderPanel()
    await screen.findByText('Logged in')

    await userEvent.click(screen.getByRole('button', { name: /^restart$/i }))
    await userEvent.click(screen.getByRole('button', { name: /^no$/i }))
    expect(screen.queryByText(/Restart the Gateway\?/i)).not.toBeInTheDocument()
    expect(sendGatewayCommand).not.toHaveBeenCalled()
  })

  it('disables command buttons when command_port is unreachable', async () => {
    getGatewayStatus.mockResolvedValue(makeStatus({ command_port: { host: '127.0.0.1', port: 7462, reachable: false } }))
    renderPanel()
    await screen.findByText('Logged in')
    expect(screen.getByRole('button', { name: /reconnect account/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /reconnect data/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /^restart$/i })).toBeDisabled()
  })

  it('expands to show last log lines', async () => {
    getGatewayStatus.mockResolvedValue(makeStatus({ last_lines: ['Login has completed', 'Second line'] }))
    renderPanel()
    await screen.findByText('Logged in')
    await userEvent.click(screen.getByRole('button', { name: /show log/i }))
    expect(screen.getByText(/Second line/)).toBeInTheDocument()
  })
})
