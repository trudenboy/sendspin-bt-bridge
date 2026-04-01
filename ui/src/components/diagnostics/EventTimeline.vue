<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useEventStore } from '@/stores/events'
import { SbFilterBar, SbTimeline, SbSpinner, SbButton, SbEmptyState } from '@/kit'

const { t } = useI18n()
const eventStore = useEventStore()

const searchQuery = ref('')
const activeTypes = ref<Set<string>>(new Set())

const typeFilters = computed(() => {
  const types = eventStore.stats?.event_types ?? []
  return types.map((et) => ({
    key: et,
    label: et,
    active: activeTypes.value.has(et),
  }))
})

function toggleFilter(key: string) {
  const s = new Set(activeTypes.value)
  if (s.has(key)) s.delete(key)
  else s.add(key)
  activeTypes.value = s
  eventStore.fetchEvents({
    eventType: s.size === 1 ? [...s][0] : undefined,
  })
}

const timelineEvents = computed(() =>
  eventStore.events
    .filter((e) => {
      if (searchQuery.value) {
        const q = searchQuery.value.toLowerCase()
        const text = `${e.event_type} ${e.subject_id} ${JSON.stringify(e.payload)}`.toLowerCase()
        if (!text.includes(q)) return false
      }
      if (activeTypes.value.size > 0 && !activeTypes.value.has(e.event_type)) return false
      return true
    })
    .map((e) => ({
      id: `${e.at}-${e.subject_id}-${e.event_type}`,
      timestamp: new Date(e.at).toLocaleString(),
      title: e.event_type,
      description: e.subject_id ? `${e.subject_id}` : undefined,
      type: categoryToType(e.category),
    })),
)

function categoryToType(cat: string): 'info' | 'success' | 'warning' | 'error' {
  const map: Record<string, 'info' | 'success' | 'warning' | 'error'> = {
    connection: 'success',
    playback: 'info',
    error: 'error',
    warning: 'warning',
  }
  return map[cat] ?? 'info'
}

const pageSize = 100
const currentLimit = ref(pageSize)

function loadMore() {
  currentLimit.value += pageSize
  eventStore.fetchEvents({ limit: currentLimit.value })
}

const hasMore = computed(() => eventStore.events.length >= currentLimit.value)

onMounted(async () => {
  await Promise.all([eventStore.fetchEvents(), eventStore.fetchStats()])
})
</script>

<template>
  <div class="space-y-4">
    <SbFilterBar
      v-model="searchQuery"
      :placeholder="t('common.search')"
      :filters="typeFilters"
      @toggle-filter="toggleFilter"
    />

    <div v-if="eventStore.loading" class="flex justify-center py-8">
      <SbSpinner size="md" :label="t('common.loading')" />
    </div>

    <template v-else-if="timelineEvents.length > 0">
      <SbTimeline :events="timelineEvents" />

      <div v-if="hasMore" class="flex justify-center pt-2">
        <SbButton variant="secondary" size="sm" @click="loadMore">
          {{ t('diagnostics.events.loadMore') }}
        </SbButton>
      </div>
    </template>

    <SbEmptyState
      v-else
      :title="t('diagnostics.events.empty')"
      :description="t('diagnostics.events.emptyDesc')"
    />
  </div>
</template>
