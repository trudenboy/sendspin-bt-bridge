import { ref, computed, type Ref } from 'vue'
import type { Device } from '@/api/types'

export function useDeviceSelection(devices: Ref<Device[]>) {
  const selected = ref<Set<string>>(new Set())

  const selectedCount = computed(() => selected.value.size)

  const allSelected = computed(
    () => devices.value.length > 0 && selected.value.size === devices.value.length,
  )

  const someSelected = computed(
    () => selected.value.size > 0 && !allSelected.value,
  )

  const selectedDevices = computed(() =>
    devices.value.filter((d) => selected.value.has(d.id)),
  )

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
      selected.value = new Set(devices.value.map((d) => d.id))
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
    selectedNames,
    toggle,
    toggleAll,
    clear,
    selectGroup,
  }
}
