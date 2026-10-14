import { describe, it, expect } from 'vitest'
import { ref } from 'vue'
import { useDeviceSelection } from '@/composables/useDeviceSelection'
import { makeDevice } from '../../fixtures/device'

describe('useDeviceSelection with disabled players', () => {
  const devices = ref([makeDevice({ id: 'a' }), makeDevice({ id: 'b' })])
  const disabled = ref(['off'])

  it('selects all of them, keeping running and disabled apart', () => {
    const s = useDeviceSelection(devices, disabled)
    s.toggleAll()
    expect(s.selectedCount.value).toBe(3)
    expect(s.allSelected.value).toBe(true)
    expect(s.selectedDevices.value.map((d) => d.id)).toEqual(['a', 'b'])
    expect(s.selectedDisabledIds.value).toEqual(['off'])
  })

  it('counts a selected disabled player as part of the selection', () => {
    const s = useDeviceSelection(devices, disabled)
    s.toggle('off')
    expect(s.someSelected.value).toBe(true)
    expect(s.selectedDevices.value).toEqual([])
    expect(s.selectedDisabledIds.value).toEqual(['off'])
  })

  it('works without disabled players', () => {
    const s = useDeviceSelection(devices)
    s.toggleAll()
    expect(s.allSelected.value).toBe(true)
    expect(s.selectedDisabledIds.value).toEqual([])
  })
})
