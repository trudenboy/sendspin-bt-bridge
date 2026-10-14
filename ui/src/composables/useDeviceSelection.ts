import { ref, computed, type Ref } from 'vue'
import type { Device } from '@/api/types'

/**
 * Bulk selection over the shown players: running devices, plus disabled
 * players (by id), which only Enable and Remove apply to.
 */
export function useDeviceSelection(devices: Ref<Device[]>, disabledIds: Ref<string[]> = ref([])) {
  const selected = ref<Set<string>>(new Set())

  const allIds = computed(() => [...devices.value.map((d) => d.id), ...disabledIds.value])

  const selectedCount = computed(() => selected.value.size)

  const allSelected = computed(
    () => allIds.value.length > 0 && allIds.value.every((id) => selected.value.has(id)),
  )

  const someSelected = computed(
    () => selected.value.size > 0 && !allSelected.value,
  )

  const selectedDevices = computed(() =>
    devices.value.filter((d) => selected.value.has(d.id)),
  )

  const selectedDisabledIds = computed(() => disabledIds.value.filter((id) => selected.value.has(id)))

  const selectedNames = computed(() => [...selected.value])

  function toggle(id: string) {
    const next = new Set(selected.value)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    selected.value = next
  }

  function toggleAll() {
    if (allSelected.value) {
      selected.value = new Set()
    } else {
      selected.value = new Set(allIds.value)
    }
  }

  function clear() {
    selected.value = new Set()
  }

  function selectGroup(ids: string[]) {
    selected.value = new Set(ids)
  }

  return {
    selected,
    selectedCount,
    allSelected,
    someSelected,
    selectedDevices,
    selectedDisabledIds,
    selectedNames,
    toggle,
    toggleAll,
    clear,
    selectGroup,
  }
}
