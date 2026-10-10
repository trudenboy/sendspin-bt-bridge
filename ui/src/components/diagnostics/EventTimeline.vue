<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useEventStore } from '@/stores/events'
import { SbFilterBar, SbTimeline, SbSpinner, SbButton, SbEmptyState } from '@/kit'
import { ClipboardCopy } from 'lucide-vue-next'
import { copyToClipboard } from '@/utils/clipboard'

const { t } = useI18n()
const eventStore = useEventStore()
const copyLabel = ref(t('diagnostics.copy'))

async function copyEvents() {
  const header = 'Timestamp\tLevel\tSource\tEvent\tSummary'
  const rows = eventStore.events.map((e) => [e.at, e.level, e.source, e.label, e.summary].join('\t'))
  const ok = await copyToClipboard([header, ...rows].join('\n'))
  copyLabel.value = ok ? t('diagnostics.copied') : t('diagnostics.copyFailed')
  setTimeout(() => { copyLabel.value = t('diagnostics.copy') }, 2000)
}

const searchQuery = ref('')
const activeLevels = ref<Set<string>>(new Set())

/** One chip per level present in the timeline. */
const typeFilters = computed(() =>
  [...new Set(eventStore.events.map((e) => e.level))].sort().map((level) => ({
    key: level,
    label: level,
    active: activeLevels.value.has(level),
  })),
)

function toggleFilter(key: string) {
  const next = new Set(activeLevels.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  activeLevels.value = next
}

const timelineEvents = computed(() =>
  eventStore.events
    .filter((e) => {
      if (searchQuery.value) {
        const q = searchQuery.value.toLowerCase()
        if (!`${e.label} ${e.source} ${e.summary}`.toLowerCase().includes(q)) return false
      }
      return activeLevels.value.size === 0 || activeLevels.value.has(e.level)
    })
    .map((e) => ({
      id: `${e.at}-${e.source}-${e.label}`,
      timestamp: new Date(e.at).toLocaleString(),
      title: e.label,
      description: `${e.source}: ${e.summary}`,
      type: levelToType(e.level),
    })),
)

function levelToType(level: string): 'info' | 'success' | 'warning' | 'error' {
  const map: Record<string, 'info' | 'success' | 'warning' | 'error'> = {
    error: 'error',
    warning: 'warning',
    success: 'success',
  }
  return map[level] ?? 'info'
}

const pageSize = 100
const currentLimit = ref(pageSize)

function loadMore() {
  currentLimit.value += pageSize
  eventStore.fetchEvents({ limit: currentLimit.value })
}

const hasMore = computed(() => eventStore.events.length >= currentLimit.value)

onMounted(async () => {
  await eventStore.fetchEvents()
})
</script>

<template>
  <div class="space-y-4">
    <h2 class="text-base font-medium text-text-primary">{{ t('diagnostics.timeline.title') }}</h2>
    <div class="flex items-center gap-2">
      <div class="flex-1">
        <SbFilterBar
          v-model="searchQuery"
          :placeholder="t('common.search')"
          :filters="typeFilters"
          @toggle-filter="toggleFilter"
        />
      </div>
      <SbButton v-if="eventStore.events.length > 0" variant="ghost" size="sm" @click="copyEvents">
        <template #icon-left>
          <ClipboardCopy class="h-4 w-4" aria-hidden="true" />
        </template>
        {{ copyLabel }}
      </SbButton>
    </div>

    <div v-if="eventStore.loading" class="flex justify-center py-8">
      <SbSpinner size="md" :label="t('common.loading')" />
    </div>

    <template v-else-if="timelineEvents.length > 0">
      <SbTimeline :events="timelineEvents" />

      <div v-if="hasMore" class="flex justify-center pt-2">
        <SbButton variant="outline" size="sm" @click="loadMore">
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
