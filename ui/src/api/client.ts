import { ofetch } from 'ofetch'
import { useIngress } from '@/composables/useIngress'

export class ApiError extends Error {
  status: number
  data?: unknown

  constructor(message: string, status: number, data?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
  }
}

function createClient() {
  const { apiBase } = useIngress()

  return ofetch.create({
    baseURL: apiBase,
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },

    onResponseError({ response }) {
      if (response.status === 401) {
        window.location.href = `${apiBase}/login`
        return
      }
      throw new ApiError(
        response._data?.error || response.statusText,
        response.status,
        response._data,
      )
    },
  })
}

let _client: ReturnType<typeof createClient> | null = null

export function getClient() {
  if (!_client) _client = createClient()
  return _client
}

/** Typed GET helper. */
export function apiGet<T>(url: string, opts?: Record<string, unknown>) {
  return getClient()<T>(url, { method: 'GET', ...opts })
}

/** Typed POST helper. */
export function apiPost<T>(
  url: string,
  body?: Record<string, unknown> | null,
) {
  return getClient()<T>(url, { method: 'POST', body })
}

/** Typed DELETE helper. */
export function apiDelete<T>(url: string) {
  return getClient()<T>(url, { method: 'DELETE' })
}
