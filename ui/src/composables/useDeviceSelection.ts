import { ref, computed, type Ref } from 'vue'
import type { DeviceSnapshot } from '@/api/types'

export function useDeviceSelection(devices: Ref<DeviceSnapshot[]>) {
  const selected = ref<Set<string>>(new Set())

  const selectedCount = computed(() => selected.value.size)

  const allSelected = computed(
    () => devices.value.length > 0 && selected.value.size === devices.value.length,
  )

  const someSelected = computed(
    () => selected.value.size > 0 && !allSelected.value,
  )

  const selectedDevices = computed(() =>
    devices.value.filter((d) => selected.value.has(d.player_name)),
  )

  const selectedNames = computed(() => [...selected.value])

  function toggle(name: string) {
    const next = new Set(selected.value)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    selected.value = next
  }

  function toggleAll() {
    if (allSelected.value) {
      selected.value = new Set()
    } else {
      selected.value = new Set(devices.value.map((d) => d.player_name))
    }
  }

  function clear() {
    selected.value = new Set()
  }

  function selectGroup(names: string[]) {
    selected.value = new Set(names)
  }

  return {
    selected,
    selectedCount,
    allSelected,
    someSelected,
    selectedDevices,
    selectedNames,
    toggle,
    toggleAll,
    clear,
    selectGroup,
  }
}
