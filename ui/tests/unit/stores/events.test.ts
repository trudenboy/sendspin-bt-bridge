import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/events', () => ({
  queryEvents: vi.fn(),
  getEventStats: vi.fn(),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

import { queryEvents, getEventStats } from '@/api/events'
import { useEventStore } from '@/stores/events'
import type { EventRecord, EventStoreStats } from '@/api/types'

describe('useEventStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('has correct initial state', () => {
    const store = useEventStore()
    expect(store.events).toEqual([])
    expect(store.stats).toBeNull()
    expect(store.loading).toBe(false)
    expect(store.filter.limit).toBe(100)
  })

  it('fetchEvents loads events from API', async () => {
    const mockEvents: EventRecord[] = [
      {
        event_type: 'connected',
        subject_id: 'player-1',
        category: 'connection',
        payload: {},
        at: '2025-01-01T00:00:00Z',
      },
    ]
    vi.mocked(queryEvents).mockResolvedValue(mockEvents)
    const store = useEventStore()
    await store.fetchEvents()
    expect(store.events).toEqual(mockEvents)
    expect(store.loading).toBe(false)
    expect(queryEvents).toHaveBeenCalledWith({
      player_id: undefined,
      type: undefined,
      since: undefined,
      limit: 100,
    })
  })

  it('fetchEvents passes filter overrides to API', async () => {
    vi.mocked(queryEvents).mockResolvedValue([])
    const store = useEventStore()
    store.setFilter({ playerId: 'p1', eventType: 'error' })
    await store.fetchEvents({ limit: 50 })
    expect(queryEvents).toHaveBeenCalledWith({
      player_id: 'p1',
      type: 'error',
      since: undefined,
      limit: 50,
    })
  })

  it('fetchStats loads stats', async () => {
    const mockStats: EventStoreStats = {
      total_events: 42,
      buffer_capacity: 1000,
      unique_subjects: 3,
      event_types: ['connected', 'disconnected'],
    }
    vi.mocked(getEventStats).mockResolvedValue(mockStats)
    const store = useEventStore()
    await store.fetchStats()
    expect(store.stats).toEqual(mockStats)
  })

  it('setFilter merges partial updates', () => {
    const store = useEventStore()
    store.setFilter({ playerId: 'p1' })
    expect(store.filter.playerId).toBe('p1')
    expect(store.filter.limit).toBe(100)

    store.setFilter({ limit: 50 })
    expect(store.filter.playerId).toBe('p1')
    expect(store.filter.limit).toBe(50)
  })
})
