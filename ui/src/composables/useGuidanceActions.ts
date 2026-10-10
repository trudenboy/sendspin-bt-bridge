import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useBridgeStore } from '@/stores/bridge'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { reconnectDevice, unmuteSink } from '@/api/devices'
import { rerunCheck } from '@/api/diagnostics'
import { api, unwrap, ApiError } from '@/api/client'

export interface GuidanceAction {
  key: string
  label: string
  device_names?: string[]
  check_key?: string
  value?: unknown
}

/** Settings sections that guidance links to (ids of ConfigView's sections). */
const SETTINGS_SECTION: Record<string, string> = {
  open_bluetooth_settings: 'bluetooth',
  open_bt_settings: 'bluetooth',
  open_ma_settings: 'musicAssistant',
  open_latency_settings: 'audio',
  open_config: 'general',
}

/**
 * Carries out an action the bridge's guidance suggests. Navigation goes to
 * the page or settings section; device actions call the API for every named
 * device. Unknown keys fall back to the diagnostics page.
 */
export function useGuidanceActions() {
  const { t } = useI18n()
  const router = useRouter()
  const bridge = useBridgeStore()
  const bluetooth = useBluetoothStore()
  const devices = useDeviceStore()
  const notifications = useNotificationStore()

  function idsFor(names: string[] = []) {
    const wanted = new Set(names)
    return bridge.devices.filter((d) => d.name != null && wanted.has(d.name)).map((d) => d.id)
  }

  async function forEachDevice(action: GuidanceAction, run: (id: string) => Promise<unknown>) {
    const ids = idsFor(action.device_names)
    await Promise.all(ids.map(run))
  }

  async function run(action: GuidanceAction) {
    const section = SETTINGS_SECTION[action.key]
    try {
      if (section) {
        await router.push({ path: '/config', hash: `#settings-${section}` })
        return
      }
      switch (action.key) {
        case 'open_devices_settings':
          await router.push('/')
          return
        case 'scan_devices':
        case 'pair_device':
          bluetooth.scanRequested = true
          await router.push('/')
          return
        case 'reconnect_device':
        case 'reconnect_devices':
          await forEachDevice(action, reconnectDevice)
          break
        case 'enable_device':
        case 'enable_devices':
          await forEachDevice(action, (id) => devices.setEnabled(id, true))
          break
        case 'unmute_sink':
          await forEachDevice(action, unmuteSink)
          break
        case 'toggle_bt_management':
        case 'toggle_bt_management_devices':
          await forEachDevice(action, (id) => devices.release(id, false))
          break
        case 'rerun_safe_check':
          if (action.check_key) await rerunCheck(action.check_key, action.device_names)
          break
        case 'apply_latency_recommended':
          if (typeof action.value === 'number') {
            await unwrap(api().POST('/api/v1/latency/recommendations/apply', { body: { pulse_latency_msec: action.value } }))
            notifications.warning(t('settings.restartNeeded'))
            return
          }
          break
        case 'refresh_diagnostics':
          await bridge.refresh()
          break
        default:
          await router.push('/diagnostics')
          return
      }
      notifications.success(t('guidance.done', { action: action.label }))
    } catch (e) {
      notifications.error(e instanceof ApiError ? e.message : t('common.error'))
    }
  }

  return { run }
}
