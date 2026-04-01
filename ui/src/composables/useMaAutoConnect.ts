import { ref, watch } from 'vue'
import { useMaStore } from '@/stores/ma'
import { useBridgeStore } from '@/stores/bridge'
import { useIngress } from '@/composables/useIngress'

/**
 * Attempts silent MA auto-connect when running as an HA addon.
 *
 * Flow:
 * 1. Wait for bridge snapshot to load
 * 2. If already connected → skip
 * 3. Detect HA addon mode (ingress path present)
 * 4. Extract HA access token from parent frame's hass object
 * 5. POST /api/ma/ha-silent-auth with token + MA URL
 * 6. On success → ma store marks connected, groups are fetched
 * 7. On failure → silently fall through to manual login
 */
export function useMaAutoConnect() {
  const ma = useMaStore()
  const bridge = useBridgeStore()

  const autoConnecting = ref(false)
  const autoConnectFailed = ref(false)
  const attempted = ref(false)

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
    if (attempted.value) return
    attempted.value = true

    // Already connected — nothing to do
    if (ma.connected || bridge.maConnected) return

    // Only attempt in HA addon ingress mode
    if (!isIngressMode()) return

    const maUrl = bridge.snapshot?.ma_url
    const haToken = extractHaToken()

    if (!haToken || !maUrl) return

    autoConnecting.value = true
    try {
      const result = await ma.silentAuth(haToken, maUrl)
      if (result.success) {
        await ma.fetchGroups()
      } else {
        autoConnectFailed.value = true
      }
    } catch {
      autoConnectFailed.value = true
    } finally {
      autoConnecting.value = false
    }
  }

  // Wait for the bridge snapshot to load before attempting
  const stop = watch(
    () => bridge.snapshot,
    (snap) => {
      if (snap) {
        stop()
        attempt()
      }
    },
    { immediate: true },
  )

  return { autoConnecting, autoConnectFailed }
}
