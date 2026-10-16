<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ENDED, errorCode, useSyncStore } from '@/stores/sync'
import { endSessionOnPageHide } from '@/api/sync'
import { confirmDialog } from '@/composables/useConfirm'
import { SbButton, SbCard, SbDialog, SbSpinner, SbTabs } from '@/kit'
import { ArrowLeft } from 'lucide-vue-next'
import GroupMembers from '@/components/sync/GroupMembers.vue'
import ManualTuning from '@/components/sync/ManualTuning.vue'
import MeasureWizard from '@/components/sync/MeasureWizard.vue'
import ProfilesPanel from '@/components/sync/ProfilesPanel.vue'

const { t, locale } = useI18n()
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
/** This group's session is restoring and the last attempt failed (Music Assistant unreachable). */
const sessionRestore = computed(() => {
  const s = sync.session
  return s && s.group_id === groupId.value && s.state === 'restoring' && s.restore_error ? s : null
})
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

/*
 * A pending restore publishes no ``sync`` events: poll until it clears, then read the group again.
 * One chain at a time (``pollTimer`` or ``polling`` while a read is in flight); nothing after unmount.
 */
const POLL_MS = 5000
let pollTimer: ReturnType<typeof setTimeout> | null = null
let polling = false
let disposed = false
const restorePending = computed(() => !!sync.restoreFor(groupId.value))

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}

function schedulePoll() {
  if (disposed || polling) return
  stopPolling()
  pollTimer = setTimeout(async () => {
    pollTimer = null
    polling = true
    try {
      await sync.fetchRestores()
    } catch {
      /* try again on the next tick */
    } finally {
      polling = false
    }
    if (!disposed && restorePending.value) schedulePoll()
  }, POLL_MS)
}

watch(
  () => [groupId.value, restorePending.value] as const,
  ([id, pending], prev) => {
    if (pending) {
      // Also when the page opens on a restore that was already pending (the store outlives the view).
      if (!pollTimer) schedulePoll()
      return
    }
    stopPolling()
    // The restore of the group on screen just finished: its mutes and members may have changed.
    if (prev && prev[1] && id && id === prev[0]) void sync.open(id).catch(() => {})
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  disposed = true
  stopPolling()
})

/** Step status, announced to screen readers as it changes. */
const status = computed(() => t(`sync.state.${sync.session?.state ?? 'idle'}`))

/*
 * Manual tuning is a session (mode "manual"): started when the Manual tab opens, ended when the
 * tab or the page is left, after offering to keep the changed delays as a profile. Ending it
 * restores the signal and the mutes; the delays stay as they are.
 */
const liveSession = computed(() => {
  const s = sync.session
  return s && s.group_id === groupId.value && !ENDED.has(s.state) ? s : null
})
/**
 * Another client tunes this group: its session is live (or could not be read) and this page did not
 * start it. Manual sessions stay usable from the Manual tab, as before.
 */
const readOnly = computed(() => {
  const g = group.value
  if (!g) return false
  if (g.session_id && !sync.session) return true
  const s = liveSession.value
  return !!s && s.mode !== 'manual' && !sync.owns(s.id)
})
const manualSession = computed(() => (liveSession.value?.mode === 'manual' ? liveSession.value : null))
/** A measurement already runs for this group: manual tuning must not take it over. */
const manualBusy = computed(() => !!liveSession.value && liveSession.value.mode !== 'manual')
const manualChanged = ref(false)
const manualError = ref('')
const manualStarting = ref(false)
/** The session ended under the open tab (timeout, another tab, a failure): say so, start again on request. */
const manualEnded = ref(false)
let leaving: Promise<void> | null = null
/** Shown while the leave runs, so the tuning controls go away at once. */
const isLeaving = ref(false)

async function enterManual() {
  if (leaving || manualStarting.value || manualSession.value || manualBusy.value || !group.value) return
  manualError.value = ''
  manualEnded.value = false
  manualStarting.value = true
  const id = groupId.value
  try {
    await sync.start(id, 'manual')
    manualChanged.value = false
  } catch (e) {
    manualError.value = errorCode(e)
    // Another tab may hold this group's session: show it rather than a dead end.
    await sync.open(id).catch(() => {})
  } finally {
    manualStarting.value = false
  }
  if (activeTab.value !== 'manual' || groupId.value !== id) await leaveManual()
}

/* "Save as profile?" → a name → save; resolves once the operator saved or skipped. */
const saveOpen = ref(false)
const profileName = ref('')
const nameMissing = ref(false)
const saveFailure = ref('')
const saving = ref(false)
let saveDone: (() => void) | null = null

function askProfileName(): Promise<void> {
  const date = new Intl.DateTimeFormat(locale.value, { dateStyle: 'short', timeStyle: 'short' }).format(new Date())
  profileName.value = t('sync.manual.defaultName', { date })
  nameMissing.value = false
  saveFailure.value = ''
  saveOpen.value = true
  return new Promise((resolve) => {
    saveDone = resolve
  })
}

function finishSave() {
  saveOpen.value = false
  const done = saveDone
  saveDone = null
  done?.()
}

async function saveManualProfile() {
  const name = profileName.value.trim()
  if (!name) {
    nameMissing.value = true
    return
  }
  saving.value = true
  try {
    await sync.saveProfile(name)
    finishSave()
  } catch (e) {
    saveFailure.value = t('sync.profiles.failed', { code: errorCode(e) })
  } finally {
    saving.value = false
  }
}

// Closed by Escape or the backdrop: the same as skipping.
watch(saveOpen, (open) => {
  if (!open) finishSave()
})

async function doLeave() {
  if (!manualSession.value) return
  // The last edit is written before the profile captures the delays, and never hits an ending session.
  await sync.flushDelays()
  if (manualChanged.value) {
    const keep = await confirmDialog({
      title: t('sync.manual.saveOnLeave'),
      message: t('sync.manual.saveOnLeaveHelp'),
      confirmLabel: t('sync.manual.saveConfirm'),
      cancelLabel: t('sync.manual.saveDecline'),
    })
    if (keep) await askProfileName()
  }
  await sync.flushDelays()
  try {
    await sync.end()
  } catch {
    /* the failure is kept in the store's error; the session times out on the bridge */
  }
  manualChanged.value = false
}

/** One leave at a time; coming back to the tab meanwhile starts a fresh session afterwards. */
function leaveManual(): Promise<void> {
  if (!leaving) {
    isLeaving.value = true
    leaving = doLeave().finally(() => {
      leaving = null
      isLeaving.value = false
      if (activeTab.value === 'manual' && !disposed) void enterManual()
    })
  }
  return leaving
}

watch(manualSession, (now, prev) => {
  if (now) manualEnded.value = false
  if (prev && !now && activeTab.value === 'manual' && !isLeaving.value && prev.group_id === groupId.value) {
    manualEnded.value = true
    manualChanged.value = false
  }
})

watch(activeTab, (tab, prev) => {
  if (tab === 'manual') {
    if (!manualEnded.value) void enterManual()
  } else if (prev === 'manual') {
    manualEnded.value = false
    void leaveManual()
  }
})
// The tab may be chosen before the group (and its session) are read.
watch(
  () => group.value?.id,
  (id) => {
    if (id && activeTab.value === 'manual') void enterManual()
  },
)

async function leaveRoute() {
  if (manualSession.value || leaving) await leaveManual()
}
onBeforeRouteLeave(leaveRoute)
onBeforeRouteUpdate((to, from) => (to.params.groupId !== from.params.groupId ? leaveRoute() : undefined))

/* Closing or reloading the tab: stop the clicks and the mutes now rather than at the timeout. */
function onPageHide() {
  const s = manualSession.value
  if (s) endSessionOnPageHide(s.id)
}
onMounted(() => window.addEventListener('pagehide', onPageHide))
onBeforeUnmount(() => window.removeEventListener('pagehide', onPageHide))

onBeforeUnmount(() => {
  // Not left through the router (the app went away): end without asking.
  if (manualSession.value && !leaving) void sync.end().catch(() => {})
})

async function load(id: string) {
  if (!id) return
  await Promise.allSettled([sync.open(id), sync.fetchRestores()])
}

/** The id of the other client's session: the store's when it could be read, else the group's. */
const foreignId = computed(() => {
  const s = liveSession.value
  if (s && !sync.owns(s.id)) return s.id
  return group.value?.session_id ?? null
})
const endingForeign = ref(false)
const endForeignError = ref('')

/** End another client's session after confirming, so nobody stays locked out (e.g. a phone killed the page). */
async function endForeign() {
  const id = foreignId.value
  if (!id || endingForeign.value) return
  const ok = await confirmDialog({
    title: t('sync.endForeignTitle'),
    message: t('sync.endForeignMessage'),
    confirmLabel: t('sync.endForeign'),
    cancelLabel: t('sync.profiles.cancel'),
    danger: true,
  })
  if (!ok) return
  endingForeign.value = true
  endForeignError.value = ''
  try {
    await sync.endForeign(id)
  } catch (e) {
    endForeignError.value = t('sync.profiles.failed', { code: errorCode(e) })
  } finally {
    endingForeign.value = false
  }
}

async function retrySession() {
  retrying.value = true
  try {
    await sync.retrySessionRestore()
  } finally {
    retrying.value = false
  }
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

    <div
      v-if="sessionRestore"
      role="alert"
      class="mb-4 flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      <div class="min-w-0 flex-1">
        <p class="font-medium">{{ t('sync.restoreFailed') }}</p>
        <p class="text-text-secondary">{{ t('sync.restoreRetrying', { code: sessionRestore.restore_error }) }}</p>
      </div>
      <SbButton variant="outline" size="sm" data-retry-session :loading="retrying" @click="retrySession">{{ t('common.retry') }}</SbButton>
    </div>

    <div
      v-if="readOnly"
      data-read-only
      role="status"
      class="mb-4 rounded-(--radius-card) border border-border bg-surface-secondary px-4 py-3 text-sm text-text-primary"
    >
      <div class="flex flex-wrap items-center gap-3">
        <div class="min-w-0 flex-1">
          <p class="font-medium">{{ t('sync.readOnly') }}</p>
          <p class="text-text-secondary">{{ t('sync.readOnlyHelp') }}</p>
        </div>
        <SbButton variant="outline" size="sm" data-end-foreign :loading="endingForeign" @click="endForeign">
          {{ t('sync.endForeign') }}
        </SbButton>
      </div>
      <p v-if="endForeignError" role="alert" class="mt-1 text-error">{{ endForeignError }}</p>
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
                    :disabled="readOnly"
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
              <ProfilesPanel :group-id="group.id" :read-only="readOnly" />
            </SbCard>
          </div>
        </template>
        <template #manual>
          <p class="mt-4 text-sm text-text-secondary">{{ t('sync.manualIntro') }}</p>
          <ManualTuning
            v-if="activeTab === 'manual' && manualSession && !isLeaving"
            :key="manualSession.id"
            @changed="manualChanged = $event"
          />
          <div
            v-else-if="manualEnded"
            data-manual-ended
            role="status"
            class="mt-4 flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-border bg-surface-secondary px-4 py-3 text-sm text-text-primary"
          >
            <span class="min-w-0 flex-1">{{ t('sync.manual.ended') }}</span>
            <SbButton variant="outline" size="sm" data-manual-restart @click="enterManual">{{ t('sync.manual.startAgain') }}</SbButton>
          </div>
          <p
            v-else-if="manualBusy"
            data-manual-busy
            role="status"
            class="mt-4 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
          >
            {{ t('sync.manual.busy') }}
          </p>
          <div v-else-if="manualStarting" class="mt-4 flex items-center gap-2 text-sm text-text-secondary" role="status">
            <SbSpinner size="sm" />{{ t('sync.manual.starting') }}
          </div>
          <div
            v-else-if="manualError"
            data-manual-error
            role="alert"
            class="mt-4 flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
          >
            <span class="min-w-0 flex-1">{{ t('sync.manual.startFailed', { code: manualError }) }}</span>
            <SbButton variant="outline" size="sm" @click="enterManual">{{ t('common.retry') }}</SbButton>
          </div>
        </template>
        <template #measure>
          <p class="mt-4 text-sm text-text-secondary">{{ t('sync.measureIntro') }}</p>
          <p class="mt-1 text-sm text-text-primary" :data-measure-mode="measureMode">
            {{ t(`sync.measureMode.${measureMode}`) }}
          </p>
          <!-- Mounted only while the tab is open: leaving it closes the microphone and ends the measurement. -->
          <MeasureWizard v-if="activeTab === 'measure' && !readOnly" :key="measureMode" :group-id="group.id" :mode="measureMode" />
        </template>
      </SbTabs>
    </template>

    <SbDialog v-model="saveOpen" :title="t('sync.manual.saveTitle')">
      <div class="flex flex-col gap-3">
        <div class="flex flex-col gap-1">
          <label for="sync-manual-profile-name" class="text-sm font-medium text-text-primary">{{ t('sync.profiles.name') }}</label>
          <input
            id="sync-manual-profile-name"
            v-model="profileName"
            data-manual-profile-name
            type="text"
            required
            maxlength="100"
            :aria-invalid="nameMissing || undefined"
            :aria-describedby="nameMissing ? 'sync-manual-profile-name-error' : undefined"
            :class="[
              'rounded-(--radius-input) border border-border-strong bg-surface-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-primary/40',
              nameMissing && 'border-error',
            ]"
            @input="nameMissing = false"
            @keydown.enter.prevent="saveManualProfile"
          />
          <p v-if="nameMissing" id="sync-manual-profile-name-error" role="alert" class="text-xs text-error">
            {{ t('sync.profiles.nameRequired') }}
          </p>
        </div>
        <p v-if="saveFailure" role="alert" class="text-sm text-error">{{ saveFailure }}</p>
        <div class="flex justify-end gap-3">
          <SbButton type="button" variant="outline" data-manual-profile-skip @click="finishSave">
            {{ t('sync.manual.skip') }}
          </SbButton>
          <SbButton type="button" data-manual-profile-save :loading="saving" @click="saveManualProfile">
            {{ t('sync.profiles.save') }}
          </SbButton>
        </div>
      </div>
    </SbDialog>
  </div>
</template>
