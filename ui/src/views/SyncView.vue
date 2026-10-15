<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSyncStore } from '@/stores/sync'
import { SbButton, SbCard, SbSpinner, SbTabs } from '@/kit'
import { ArrowLeft } from 'lucide-vue-next'
import GroupMembers from '@/components/sync/GroupMembers.vue'
import ProfilesPanel from '@/components/sync/ProfilesPanel.vue'

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
const root = ref<HTMLElement | null>(null)

/** Which measurement the Measure tab runs: suggest (review first) or apply (with undo). */
const measureMode = ref<'semi' | 'auto'>('semi')

const actions = ['manual', 'semi', 'auto'] as const

async function choose(action: 'manual' | 'semi' | 'auto') {
  if (action === 'manual') {
    activeTab.value = 'manual'
  } else {
    measureMode.value = action
    activeTab.value = 'measure'
  }
  await nextTick()
  root.value?.querySelector<HTMLElement>(`[role="tab"][data-tab-id="${activeTab.value}"]`)?.focus()
}

/* A pending restore publishes no ``sync`` events: poll until it clears, then read the group again. */
const POLL_MS = 5000
let pollTimer: ReturnType<typeof setTimeout> | null = null
const restorePending = computed(() => !!sync.restoreFor(groupId.value))

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}

function schedulePoll() {
  stopPolling()
  pollTimer = setTimeout(async () => {
    pollTimer = null
    try {
      await sync.fetchRestores()
    } catch {
      /* try again on the next tick */
    }
    if (restorePending.value) schedulePoll()
  }, POLL_MS)
}

watch(
  () => [groupId.value, restorePending.value] as const,
  ([id, pending], [prevId, was]) => {
    if (pending) {
      if (!pollTimer) schedulePoll()
      return
    }
    stopPolling()
    // The restore of the group on screen just finished: its mutes and members may have changed.
    if (was && id && id === prevId) void sync.open(id).catch(() => {})
  },
)

onBeforeUnmount(stopPolling)

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
    await sync.fetchRestores().catch(() => {})
  } finally {
    retrying.value = false
  }
}

watch(groupId, (id) => void load(id), { immediate: true })
</script>

<template>
  <div ref="root">
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
      <SbButton variant="outline" size="sm" data-retry :loading="retrying" @click="retry">{{ t('common.retry') }}</SbButton>
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
          <div class="mt-4 flex flex-col gap-4">
            <SbCard>
              <h2 class="mb-2 text-sm font-medium text-text-secondary">{{ t('sync.members') }}</h2>
              <GroupMembers :members="group.members" />
            </SbCard>
            <SbCard>
              <h2 class="mb-3 text-sm font-medium text-text-secondary">{{ t('sync.actions.title') }}</h2>
              <ul class="grid gap-3 sm:grid-cols-3">
                <li v-for="action in actions" :key="action" class="flex flex-col gap-1">
                  <SbButton
                    type="button"
                    :variant="action === 'manual' ? 'outline' : action === 'semi' ? 'primary' : 'secondary'"
                    :data-action="action"
                    :aria-describedby="`sync-action-${action}-help`"
                    @click="choose(action)"
                  >
                    {{ t(`sync.actions.${action}`) }}
                  </SbButton>
                  <p :id="`sync-action-${action}-help`" class="text-xs text-text-secondary">
                    {{ t(`sync.actions.${action}Help`) }}
                  </p>
                </li>
              </ul>
            </SbCard>
            <SbCard>
              <ProfilesPanel :group-id="group.id" />
            </SbCard>
          </div>
        </template>
        <template #manual>
          <p class="mt-4 text-sm text-text-secondary">{{ t('sync.manualIntro') }}</p>
        </template>
        <template #measure>
          <p class="mt-4 text-sm text-text-secondary">{{ t('sync.measureIntro') }}</p>
          <p class="mt-1 text-sm text-text-primary" :data-measure-mode="measureMode">
            {{ t(`sync.measureMode.${measureMode}`) }}
          </p>
        </template>
      </SbTabs>
    </template>
  </div>
</template>
