import { describe, it, expect } from 'vitest'
import { nextTick, ref } from 'vue'
import { useRowExpansion } from '@/composables/useRowExpansion'
import type { Device } from '@/api/types'
import { makeDevice } from '../../fixtures/device'

const idle = (id: string) => makeDevice({ id, name: id })
const streaming = (id: string) => makeDevice({ id, name: id, playback: { playing: true }, audio: { streaming: true } })

describe('useRowExpansion', () => {
  it('opens the first streaming row on first load', async () => {
    const list = ref<Device[]>([])
    const { expandedId } = useRowExpansion(list)
    expect(expandedId.value).toBeNull()
    list.value = [idle('a'), streaming('b'), streaming('c')]
    await nextTick()
    expect(expandedId.value).toBe('b')
  })

  it('opens nothing when nothing streams, and does not auto-open later', async () => {
    const list = ref<Device[]>([idle('a')])
    const { expandedId } = useRowExpansion(list)
    await nextTick()
    expect(expandedId.value).toBeNull()
    list.value = [idle('a'), streaming('b')]
    await nextTick()
    expect(expandedId.value).toBeNull()
  })

  it('keeps one row open at a time and toggles it closed', () => {
    const { expandedId, toggle } = useRowExpansion(ref([idle('a'), idle('b')]))
    toggle('a')
    expect(expandedId.value).toBe('a')
    toggle('b')
    expect(expandedId.value).toBe('b')
    toggle('b')
    expect(expandedId.value).toBeNull()
  })

  it('does not open a disabled speaker', () => {
    const { expandedId, toggle } = useRowExpansion(ref([makeDevice({ id: 'd', enabled: false })]))
    toggle('d')
    expect(expandedId.value).toBeNull()
  })

  it('closes when the open row leaves the list', async () => {
    const list = ref<Device[]>([idle('a'), idle('b')])
    const { expandedId, toggle } = useRowExpansion(list)
    toggle('b')
    list.value = [idle('a')]
    await nextTick()
    expect(expandedId.value).toBeNull()
  })
})
