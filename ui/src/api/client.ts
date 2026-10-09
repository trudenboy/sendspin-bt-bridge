import createClient, { type Middleware } from 'openapi-fetch'
import type { components, paths } from './schema'
import { useIngress } from '@/composables/useIngress'

export type Schemas = components['schemas']

/** An RFC 9457 problem from the API, with its stable ``code``. */
export interface Problem {
  type?: string
  title?: string
  status: number
  code: string
  detail?: string | null
  errors?: { loc?: (string | number)[]; msg?: string; field?: string; message?: string }[] | null
  [key: string]: unknown
}

export class ApiError extends Error {
  status: number
  code: string
  problem: Problem

  constructor(problem: Problem) {
    super(problem.detail || problem.title || problem.code)
    this.name = 'ApiError'
    this.status = problem.status
    this.code = problem.code
    this.problem = problem
  }
}

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
let csrfToken = ''
let onUnauthorized: (() => void) | null = null

/** The session's CSRF token, echoed on every state-changing request. */
export function setCsrfToken(token: string) {
  csrfToken = token
}

export function getCsrfToken() {
  return csrfToken
}

/** Called when the API answers 401 (the session expired). */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler
}

const sessionMiddleware: Middleware = {
  onRequest({ request }) {
    if (csrfToken && UNSAFE.has(request.method)) request.headers.set('X-CSRF-Token', csrfToken)
    return request
  },
  onResponse({ response }) {
    if (response.status === 401) onUnauthorized?.()
    return response
  },
}

function makeClient() {
  const { apiBase } = useIngress()
  const client = createClient<paths>({ baseUrl: apiBase, credentials: 'same-origin' })
  client.use(sessionMiddleware)
  return client
}

let _client: ReturnType<typeof makeClient> | null = null

/** The typed API client (paths are the full ``/api/v1/...`` paths). */
export function api() {
  if (!_client) _client = makeClient()
  return _client
}

/** Test seam: forget the cached client (base path, middleware). */
export function resetClient() {
  _client = null
}

function toProblem(error: unknown, response: Response): Problem {
  if (error && typeof error === 'object' && 'code' in error) return error as Problem
  return {
    status: response.status,
    code: response.status === 0 ? 'network_error' : 'http_error',
    title: response.statusText || 'Request failed',
  }
}

/** Resolve an openapi-fetch result to its data, or throw ``ApiError``. */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call
  if (!response.ok) throw new ApiError(toProblem(error, response))
  return data as T
}

/** A same-origin URL for downloads and EventSource (ingress-aware). */
export function apiUrl(path: string) {
  const { apiBase } = useIngress()
  return `${apiBase}${path}`
}
