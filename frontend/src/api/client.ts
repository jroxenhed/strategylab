import axios, { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { SIGNED_OUT_TEXT, isSignInUrl, isSignedOut, markSignedOut } from '../shared/auth/signedOut'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL as string || 'http://localhost:8000',
})

/**
 * An axios error that reads like a 401 with a plain `detail` text, so every
 * existing error reader shows "Signed out..." and never "Network Error".
 */
function signedOutError(config: InternalAxiosRequestConfig | undefined, request?: unknown): AxiosError {
  const response = {
    status: 401,
    statusText: 'Unauthorized',
    data: { detail: SIGNED_OUT_TEXT },
    headers: {},
    config,
  } as AxiosResponse
  const err = new AxiosError(SIGNED_OUT_TEXT, 'ERR_SIGNED_OUT', config, request, response)
  ;(err as AxiosError & { signedOut: boolean }).signedOut = true
  return err
}

/** The URL the browser ended on after redirects (XMLHttpRequest only). */
function finalUrl(request: unknown): string | undefined {
  const url = (request as { responseURL?: unknown } | null | undefined)?.responseURL
  return typeof url === 'string' ? url : undefined
}

// Once signed out, do not send more requests: they would only collect 401s.
api.interceptors.request.use(config => {
  if (isSignedOut()) return Promise.reject(signedOutError(config))
  return config
})

api.interceptors.response.use(
  response => {
    // A same-origin redirect to the sign-in page ends as a 200 HTML page.
    if (isSignInUrl(finalUrl(response.request))) {
      markSignedOut()
      return Promise.reject(signedOutError(response.config, response.request))
    }
    return response
  },
  (error: unknown) => {
    if (axios.isAxiosError(error) && (error as { signedOut?: boolean }).signedOut) {
      return Promise.reject(error)
    }
    if (axios.isAxiosError(error)) {
      if (error.response?.status === 401 || isSignInUrl(finalUrl(error.request))) {
        markSignedOut()
        return Promise.reject(signedOutError(error.config, error.request))
      }
    }
    return Promise.reject(error)
  },
)
