<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSyncStore } from '@/stores/sync'
import { SbBadge, SbButton, SbCard, SbSpinner, SbTabs } from '@/kit'
import { ArrowLeft } from 'lucide-vue-next'

const { t } = useI18n()
const route = useRoute()
const sync = useSyncStore()

const groupId = computed(() => String(route.params.groupId ?? ''))
const activeTab = ref('group')
const tabs = computed(() => [
  { id: 'group', label: t('sync.tabs.group') },
  { id: 'manual', label: t('sync.tabs.manual') },
  { id: 'measure', label: t('sync.tabs.measure') },
])

const group = computed(() => (sync.group?.id === groupId.value ? sync.group : null))
const restore = computed(() => {
  const pending = sync.restoreFor(groupId.value)
  return pending?.error ? pending : null
})
const retrying = ref(false)

/** Step status, announced to screen readers as it changes. */
const status = computed(() => t(`sync.state.${sync.session?.state ?? 'idle'}`))

async function load(id: string) {
  if (!id) return
  await Promise.allSettled([sync.open(id), sync.fetchRestores()])
}

async function retry() {
  retrying.value = true
  try {
    await sync.retryRestore(groupId.value)
  } finally {
    retrying.value = false
  }
}

watch(groupId, (id) => void load(id), { immediate: true })
</script>

<template>
  <div>
    <RouterLink
      :to="{ name: 'groups' }"
      class="mb-3 inline-flex items-center gap-1 text-sm text-text-secondary hover:text-text-primary"
    >
      <ArrowLeft class="size-4" aria-hidden="true" />
      {{ t('sync.backToGroups') }}
    </RouterLink>

    <div class="mb-4 flex flex-wrap items-baseline justify-between gap-2">
      <h1 class="text-2xl font-semibold tracking-tight text-text-primary">
        {{ t('nav.sync') }}<template v-if="group">: {{ group.name }}</template>
      </h1>
      <p class="text-sm text-text-secondary" aria-live="polite">{{ status }}</p>
    </div>

    <div
      v-if="restore"
      role="alert"
      class="mb-4 flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      <div class="min-w-0 flex-1">
        <p class="font-medium">{{ t('sync.restoreFailed') }}</p>
        <p class="text-text-secondary">{{ t('sync.restoreFailedHelp', { code: restore.error }) }}</p>
      </div>
      <SbButton variant="outline" size="sm" :loading="retrying" @click="retry">{{ t('common.retry') }}</SbButton>
    </div>

    <div v-if="sync.loading && !group" class="flex justify-center py-8">
      <SbSpinner />
    </div>

    <div
      v-else-if="!group && sync.error"
      role="alert"
      class="rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
    >
      {{ t('sync.groupError', { code: sync.error }) }}
    </div>

    <template v-else-if="group">
      <div
        v-if="group.error"
        role="alert"
        class="mb-4 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-2 text-sm text-text-primary"
      >
        {{ t('sync.loadError', { code: group.error }) }}
      </div>

      <SbTabs v-model="activeTab" :tabs="tabs">
        <template #group>
          <SbCard class="mt-4">
            <h2 class="mb-2 text-sm font-medium text-text-secondary">{{ t('sync.members') }}</h2>
            <p v-if="group.members.length === 0" class="text-sm text-text-secondary">{{ t('sync.noMembers') }}</p>
            <ul v-else class="divide-y divide-border">
              <li
                v-for="member in group.members"
                :key="member.ma_player_id"
                class="flex items-center justify-between gap-3 py-2"
              >
                <span class="min-w-0 truncate text-text-primary">{{ member.name }}</span>
                <span v-if="member.adjustable && member.delay_ms != null" class="text-sm tabular-nums text-text-secondary">
                  {{ t('sync.delay', { ms: member.delay_ms }) }}
                </span>
                <SbBadge v-else tone="neutral" size="sm">{{ t('sync.notAdjustable') }}</SbBadge>
              </li>
            </ul>
          </SbCard>
        </template>
        <template #manual>
          <p class="mt-4 text-sm text-text-secondary">{{ t('sync.manualIntro') }}</p>
        </template>
        <template #measure>
          <p class="mt-4 text-sm text-text-secondary">{{ t('sync.measureIntro') }}</p>
        </template>
      </SbTabs>
    </template>
  </div>
</template>
