/**
 * The describe client (F454): what it sends and how it reads replies and errors.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { AxiosError, type AxiosResponse } from 'axios'

const post = vi.fn()
const get = vi.fn()
vi.mock('../api/client', () => ({ api: { post: (...a: unknown[]) => post(...a), get: (...a: unknown[]) => get(...a) } }))

const { describeGraph, describeBot, readDescribe, describeErrorText } = await import('../api/describe')
const { emptyGraph } = await import('../api/nodebuilder')

function axiosError(status: number | null, data?: unknown): AxiosError {
  const err = new AxiosError('Request failed')
  if (status != null) err.response = { status, data, statusText: '', headers: {}, config: {} } as AxiosResponse
  return err
}

beforeEach(() => {
  post.mockReset()
  get.mockReset()
})

describe('describe client', () => {
  it('posts the graph, with the sidebar values only when given', async () => {
    post.mockResolvedValue({ data: { groups: [], lines: ['Buy SPY (5m) when RSI(14) is below 40.'], warnings: [] } })
    const g = emptyGraph()
    const ctrl = new AbortController()
    const res = await describeGraph(g, { ticker: 'RIVN', interval: '30m' }, ctrl.signal)
    expect(post).toHaveBeenCalledWith('/api/nodebuilder/describe', { graph: g, ticker: 'RIVN', interval: '30m' }, { signal: ctrl.signal })
    expect(res.lines).toEqual(['Buy SPY (5m) when RSI(14) is below 40.'])
    await describeGraph(g, { ticker: '', interval: '' })
    expect(post.mock.calls[1][1]).toEqual({ graph: g })
  })

  it('sends the run direction only when it is long or short (F453 contract)', async () => {
    post.mockResolvedValue({ data: { groups: [], lines: [], warnings: [] } })
    const g = emptyGraph()
    await describeGraph(g, { ticker: 'SPY', interval: '5m', direction: 'short' })
    expect(post.mock.calls[0][1]).toEqual({ graph: g, ticker: 'SPY', interval: '5m', direction: 'short' })
    await describeGraph(g, { ticker: 'SPY', interval: '5m', direction: null })
    expect(post.mock.calls[1][1]).toEqual({ graph: g, ticker: 'SPY', interval: '5m' })
  })

  it('gets a bot by its id', async () => {
    get.mockResolvedValue({ data: { lines: ['x.'] } })
    const res = await describeBot('bot 1')
    expect(get.mock.calls[0][0]).toBe('/api/bots/bot%201/describe')
    expect(res).toEqual({ groups: [], lines: ['x.'], warnings: [] })
  })

  it('reads a broken reply as empty lists', () => {
    expect(readDescribe(null)).toEqual({ groups: [], lines: [], warnings: [] })
    expect(readDescribe({ lines: ['a', 3], warnings: 'no' })).toEqual({ groups: [], lines: ['a'], warnings: [] })
  })

  it('turns errors into plain text', () => {
    expect(describeErrorText(axiosError(null))).toBe('Could not reach the server.')
    expect(describeErrorText(axiosError(404, { detail: 'Bot not found' }), 'bot')).toBe('Bot not found.')
    // IN-2: a 404 from the node builder's route is not about a bot.
    expect(describeErrorText(axiosError(404, { detail: 'Not Found' }))).toBe('The server does not know this request yet. It may need a restart.')
    expect(describeErrorText(axiosError(400, { detail: 'The graph has a loop.' }))).toBe('The graph has a loop.')
    expect(describeErrorText(axiosError(422, { detail: [{ msg: 'bad' }] }))).toBe('The server answered with an error (HTTP 422).')
  })
})
