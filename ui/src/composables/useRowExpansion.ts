import { ref, watch, type Ref } from 'vue'
import { deviceState } from '@/stores/devices'
import type { Device } from '@/api/types'

/**
 * Which list row is expanded: one at a time, never a disabled speaker. On the
 * first non-empty list the first streaming speaker opens by itself (stable
 * 2.76 does the same); after that only the user opens rows.
 */
export function useRowExpansion(devices: Ref<Device[]>) {
  const expandedId = ref<string | null>(null)
  let primed = false

  watch(
    devices,
    (list) => {
      if (!primed && list.length) {
        primed = true
        expandedId.value = list.find((d) => deviceState(d) === 'streaming')?.id ?? null
        return
      }
      if (expandedId.value && !list.some((d) => d.id === expandedId.value)) expandedId.value = null
    },
    { immediate: true },
  )

  function toggle(id: string) {
    if (expandedId.value === id) {
      expandedId.value = null
      return
    }
    const device = devices.value.find((d) => d.id === id)
    if (!device || !device.enabled) return
    expandedId.value = id
  }

  return { expandedId, toggle }
}
