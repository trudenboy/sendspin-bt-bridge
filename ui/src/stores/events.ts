import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getRecoveryTimeline } from '@/api/diagnostics'

/** One line of the recovery timeline (startup, device and bridge events). */
export interface TimelineEntry {
  at: string
  level: string
  source: string
  label: string
  summary: string
  [key: string]: unknown
}

export interface EventFilter {
  source?: string
  level?: string
  limit: number
}

export const useEventStore = defineStore('events', () => {
  const events = ref<TimelineEntry[]>([])
  const summary = ref<Record<string, unknown> | null>(null)
  const loading = ref(false)
  const filter = ref<EventFilter>({ limit: 100 })

  async function fetchEvents(overrides?: Partial<EventFilter>) {
    loading.value = true
    const f = { ...filter.value, ...overrides }
    try {
      const timeline = (await getRecoveryTimeline()) as { entries?: TimelineEntry[]; summary?: Record<string, unknown> }
      summary.value = timeline.summary ?? null
      events.value = (timeline.entries ?? [])
        .filter((e) => (!f.source || e.source === f.source) && (!f.level || e.level === f.level))
        .slice(-f.limit)
    } finally {
      loading.value = false
    }
  }

  function setFilter(partial: Partial<EventFilter>) {
    filter.value = { ...filter.value, ...partial }
  }

  return { events, summary, loading, filter, fetchEvents, setFilter }
})
