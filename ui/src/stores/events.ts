import { defineStore } from 'pinia'
import { ref } from 'vue'
import { queryEvents, getEventStats } from '@/api/events'
import type { EventRecord, EventStoreStats } from '@/api/types'

export interface EventFilter {
  playerId?: string
  eventType?: string
  since?: string
  limit: number
}

export const useEventStore = defineStore('events', () => {
  const events = ref<EventRecord[]>([])
  const stats = ref<EventStoreStats | null>(null)
  const loading = ref(false)
  const filter = ref<EventFilter>({ limit: 100 })

  async function fetchEvents(overrides?: Partial<EventFilter>) {
    loading.value = true
    const f = { ...filter.value, ...overrides }
    try {
      events.value = await queryEvents({
        player_id: f.playerId,
        type: f.eventType,
        since: f.since,
        limit: f.limit,
      })
    } finally {
      loading.value = false
    }
  }

  async function fetchStats() {
    stats.value = await getEventStats()
  }

  function setFilter(partial: Partial<EventFilter>) {
    filter.value = { ...filter.value, ...partial }
  }

  return {
    events,
    stats,
    loading,
    filter,
    fetchEvents,
    fetchStats,
    setFilter,
  }
})
