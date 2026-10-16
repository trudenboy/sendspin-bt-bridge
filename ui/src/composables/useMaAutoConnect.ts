import { watch } from 'vue'
import { useMaStore } from '@/stores/ma'
import { useBridgeStore } from '@/stores/bridge'
import { useIngress } from '@/composables/useIngress'

/** Tries no more often than this, so an unreachable Music Assistant isn't hammered. */
const MIN_ATTEMPT_INTERVAL_MS = 30_000
/** Each sign-in that worked but was lost again doubles the wait, up to this. */
const MAX_ATTEMPT_INTERVAL_MS = 15 * 60_000

/**
 * Signs the bridge in to Music Assistant by itself in the Home Assistant add-on.
 *
 * The bridge can't do this alone: Music Assistant trusts the ingress user
 * headers only from the Supervisor, and the Supervisor opens an ingress session
 * only for Home Assistant Core — never for an add-on's own token. The HA user's
 * token the frontend holds can, so the page lends it to the bridge
 * (``POST /api/v1/music-assistant/session/ha-silent``).
 *
 * Mounted once for the whole app, it runs when any page loads without a Music
 * Assistant connection, and again whenever the bridge loses it — e.g. the
 * monitor finds the stored token invalid. Outside ingress it does nothing and
 * the manual sign-in stays. Progress and failure live in the ``ma`` store.
 */
export function useMaAutoConnect() {
  const ma = useMaStore()
  const bridge = useBridgeStore()
  let lastAttempt = 0
  let interval = MIN_ATTEMPT_INTERVAL_MS

  function isIngressMode(): boolean {
    const { basePath } = useIngress()
    return basePath.includes('hassio_ingress')
  }

  function extractHaToken(): string | null {
    try {
      // HA frontend exposes hass object on the home-assistant element
      const ha =
        (window.parent as any)?.document?.querySelector?.('home-assistant') as any
      return ha?.hass?.auth?.data?.access_token ?? null
    } catch {
      // Cross-origin or unavailable — expected outside ingress
      return null
    }
  }

  async function attempt() {
    if (ma.autoConnecting || bridge.maConnected) return
    if (!isIngressMode()) return
    const now = Date.now()
    if (lastAttempt && now - lastAttempt < interval) return

    const haToken = extractHaToken()
    if (!haToken) return

    lastAttempt = now
    ma.autoConnecting = true
    ma.autoConnectFailed = false
    try {
      await ma.fetchConnection()
      // The configured server, else only the Music Assistant add-on the
      // Supervisor reports — never a server merely seen on the network, as the
      // new token is checked against this address.
      let maUrl = String(ma.connection?.configured_url ?? '')
      if (!maUrl) {
        const addon = (await ma.discover()).find((s) => s.discovery_source === 'ha_addon_hostname')
        maUrl = addon?.url ?? ''
      }
      if (!maUrl) {
        ma.autoConnectFailed = true
        return
      }
      await ma.silentAuth(haToken, maUrl)
      // A sign-in that has to be repeated soon backs off (each one mints a token).
      interval = Math.min(interval * 2, MAX_ATTEMPT_INTERVAL_MS)
      await ma.fetchGroups()
    } catch {
      ma.autoConnectFailed = true
    } finally {
      ma.autoConnecting = false
    }
  }

  // Not immediate: the first status snapshot may still be loading.
  watch(
    () => [bridge.snapshot !== null, bridge.maConnected] as const,
    ([loaded, connected], previous) => {
      if (!loaded || connected || !isIngressMode()) return
      const wasConnected = previous?.[1] === true
      // The bridge lost Music Assistant: what this page signed in with is gone too.
      if (wasConnected) ma.connected = false
      if (!ma.connected) void attempt()
    },
  )
  if (bridge.snapshot && !bridge.maConnected && !ma.connected) void attempt()

  return { attempt }
}
