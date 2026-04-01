/**
 * HA Ingress path detection.
 *
 * When served through HA Ingress, the app URL looks like:
 *   /api/hassio_ingress/<TOKEN>/
 *
 * All API calls and router links must be prefixed with this base path.
 * This composable computes it once from `window.location.pathname`.
 */

const SPA_ROUTES = /\/(dashboard|devices|config|diagnostics|ma|login)(\/|$)/

let _cached: { basePath: string; apiBase: string } | null = null

function compute(): { basePath: string; apiBase: string } {
  if (_cached) return _cached

  let path = window.location.pathname

  // Strip SPA route segments to find the real base
  const match = path.match(SPA_ROUTES)
  if (match) {
    path = path.substring(0, match.index)
  }

  // Remove trailing slash
  const basePath = path.replace(/\/+$/, '')
  const apiBase = basePath

  _cached = { basePath, apiBase }
  return _cached
}

export function useIngress() {
  return compute()
}
