/**
 * HA Ingress path detection.
 *
 * When served through HA Ingress, the app URL looks like:
 *   /api/hassio_ingress/<TOKEN>/
 *
 * All API calls and router links must be prefixed with this base path.
 * This composable computes it once from `window.location.pathname`.
 */

import { APP_SEGMENTS } from '@/router/segments'

const SPA_ROUTES = new RegExp(`/(${APP_SEGMENTS.join('|')})(/|$)`)

let _cached: { basePath: string; apiBase: string } | null = null

/** The prefix in front of the app's own routes (empty outside HA ingress). */
export function basePathFor(pathname: string): string {
  const match = pathname.match(SPA_ROUTES)
  const path = match ? pathname.substring(0, match.index) : pathname
  return path.replace(/\/+$/, '')
}

function compute(): { basePath: string; apiBase: string } {
  if (_cached) return _cached

  const basePath = basePathFor(window.location.pathname)
  const apiBase = basePath

  _cached = { basePath, apiBase }
  return _cached
}

export function useIngress() {
  return compute()
}
