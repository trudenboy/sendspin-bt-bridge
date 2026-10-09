import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/diagnostics', () => ({ getRecoveryTimeline: vi.fn() }))

import { getRecoveryTimeline } from '@/api/diagnostics'
import { useEventStore } from '@/stores/events'

const entry = (n: number, level: string, source: string) => ({ at: `t${n}`, level, source, label: `e${n}`, summary: '' })

describe('useEventStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads the recovery timeline', async () => {
    vi.mocked(getRecoveryTimeline).mockResolvedValue({ summary: { entry_count: 2 }, entries: [entry(1, 'info', 'Kitchen'), entry(2, 'error', 'Bridge')] })
    const store = useEventStore()
    await store.fetchEvents()
    expect(store.events).toHaveLength(2)
    expect(store.summary).toEqual({ entry_count: 2 })
  })

  it('filters by source and level and keeps the newest', async () => {
    vi.mocked(getRecoveryTimeline).mockResolvedValue({
      entries: [entry(1, 'error', 'Kitchen'), entry(2, 'info', 'Kitchen'), entry(3, 'error', 'Kitchen'), entry(4, 'error', 'Bridge')],
    })
    const store = useEventStore()
    await store.fetchEvents({ source: 'Kitchen', level: 'error', limit: 1 })
    expect(store.events.map((e) => e.label)).toEqual(['e3'])
  })

  it('merges filter updates', () => {
    const store = useEventStore()
    store.setFilter({ level: 'error' })
    expect(store.filter).toEqual({ limit: 100, level: 'error' })
  })
})
