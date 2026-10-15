/**
 * Signed-out handling: an expired sign-in (oauth2-proxy answers 401) must
 * flip the app into "signed out", stop polling, and show the banner.
 */
import { describe, it, expect, vi, afterEach, beforeAll } from 'vitest'
import { render, screen, cleanup, waitFor, act } from '@testing-library/react'
import { createElement } from 'react'
import { AxiosError, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { apiFetch } from '../api/apiFetch'
import {
  SIGNED_OUT_TEXT, SignedOutError, installSignedOutQueryGuard, isSignedOut, markSignedOut, resetSignedOut,
} from '../shared/auth/signedOut'
import SignedOutBanner from '../shared/auth/SignedOutBanner'
import { apiErrorDetail } from '../shared/utils/errors'

beforeAll(() => installSignedOutQueryGuard())

afterEach(() => {
  cleanup()
  resetSignedOut()
  vi.restoreAllMocks()
  window.history.replaceState({}, '', '/')
})

function reject401(): AxiosAdapter {
  return vi.fn(async (config: InternalAxiosRequestConfig) => {
    const response = { status: 401, statusText: 'Unauthorized', data: '<html>401</html>', headers: {}, config } as AxiosResponse
    throw new AxiosError('Request failed with status code 401', 'ERR_BAD_REQUEST', config, {}, response)
  }) as unknown as AxiosAdapter
}

function fakeResponse(init: Partial<Response>): Response {
  return { ok: true, status: 200, type: 'basic', redirected: false, url: '', ...init } as Response
}

describe('axios client', () => {
  it('a 401 sets signed out and rejects with the plain signed-out text', async () => {
    const adapter = reject401()
    const err = await api.get('/api/bots', { adapter }).catch(e => e)
    expect(isSignedOut()).toBe(true)
    expect(err.response.status).toBe(401)
    expect(apiErrorDetail(err, 'fallback')).toBe(SIGNED_OUT_TEXT)
    expect(err.message).not.toMatch(/network error/i)
  })

  it('stops sending requests once signed out', async () => {
    const adapter = reject401()
    await api.get('/api/bots', { adapter }).catch(() => {})
    expect(adapter).toHaveBeenCalledTimes(1)
    await api.get('/api/journal', { adapter }).catch(() => {})
    expect(adapter).toHaveBeenCalledTimes(1)
  })

  it('a redirect that ended on the sign-in page sets signed out', async () => {
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => ({
      status: 200, statusText: 'OK', data: '<html>sign in</html>', headers: {}, config,
      request: { responseURL: 'https://strategylab.milford.se/oauth2/sign_in?rd=%2F' },
    })) as unknown as AxiosAdapter
    await expect(api.get('/api/bots', { adapter })).rejects.toMatchObject({ response: { status: 401 } })
    expect(isSignedOut()).toBe(true)
  })

  it('other errors do not set signed out', async () => {
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const response = { status: 500, statusText: 'Error', data: {}, headers: {}, config } as AxiosResponse
      throw new AxiosError('boom', 'ERR_BAD_RESPONSE', config, {}, response)
    }) as unknown as AxiosAdapter
    await api.get('/api/bots', { adapter }).catch(() => {})
    expect(isSignedOut()).toBe(false)
  })
})

describe('apiFetch', () => {
  it('a 401 sets signed out and throws SignedOutError', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(fakeResponse({ ok: false, status: 401 }))
    await expect(apiFetch('/api/strategies')).rejects.toBeInstanceOf(SignedOutError)
    expect(isSignedOut()).toBe(true)
    // Asks for JSON so oauth2-proxy answers 401 instead of redirecting.
    const init = fetchSpy.mock.calls[0][1] as RequestInit
    expect((init.headers as Record<string, string>).Accept).toBe('application/json')
  })

  it('a redirect to the sign-in page sets signed out', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      fakeResponse({ redirected: true, url: 'https://strategylab.milford.se/oauth2/start?rd=%2F' }),
    )
    await expect(apiFetch('/api/strategies')).rejects.toBeInstanceOf(SignedOutError)
    expect(isSignedOut()).toBe(true)
  })

  it('sends nothing once signed out, and passes normal responses through', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(fakeResponse({ ok: false, status: 500 }))
    const resp = await apiFetch('/api/strategies')
    expect(resp.status).toBe(500)
    expect(isSignedOut()).toBe(false)
    markSignedOut()
    await expect(apiFetch('/api/strategies')).rejects.toBeInstanceOf(SignedOutError)
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })
})

describe('SignedOutBanner', () => {
  it('renders nothing while signed in', () => {
    render(createElement(SignedOutBanner))
    expect(screen.queryByTestId('signed-out-banner')).toBeNull()
  })

  it('shows the message and a sign-in link back to the current page', () => {
    window.history.replaceState({}, '', '/trading?tab=bots&x=1')
    markSignedOut()
    render(createElement(SignedOutBanner))
    expect(screen.getByText('Your sign-in has expired. Sign in again to see live data.')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'Sign in again' })
    expect(link.getAttribute('href')).toBe('/oauth2/sign_in?rd=%2Ftrading%3Ftab%3Dbots%26x%3D1')
  })
})

describe('polling stops once signed out', () => {
  it('a query with refetchInterval does not refetch after signed out', async () => {
    const queryFn = vi.fn(async () => 'ok')
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    function Poller() {
      const q = useQuery({
        queryKey: ['poll'], queryFn, refetchInterval: 20, refetchIntervalInBackground: true,
      })
      return createElement('span', null, q.data ?? 'none')
    }
    render(createElement(QueryClientProvider, { client }, createElement(Poller)))
    await waitFor(() => expect(queryFn.mock.calls.length).toBeGreaterThanOrEqual(2))

    act(() => markSignedOut())
    const callsAtSignOut = queryFn.mock.calls.length
    await new Promise(r => setTimeout(r, 150))
    // An explicit invalidate (what PaperTrading's ticks do) must not fetch either.
    await act(async () => { client.invalidateQueries({ queryKey: ['poll'] }) })
    await new Promise(r => setTimeout(r, 30))
    expect(queryFn.mock.calls.length).toBe(callsAtSignOut)
    client.clear()
  })
})
