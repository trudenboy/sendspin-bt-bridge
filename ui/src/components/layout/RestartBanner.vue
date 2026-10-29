<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useRouter } from 'vue-router'
import LongOperation from '@/components/LongOperation.vue'

const { t } = useI18n()
const bridge = useBridgeStore()
const router = useRouter()

let autoHideTimer: ReturnType<typeof setTimeout> | null = null

const visible = computed(() => bridge.restartState !== 'idle')

function clearAutoHide() {
  if (autoHideTimer) {
    clearTimeout(autoHideTimer)
    autoHideTimer = null
  }
}

/** An update that neither drops the event stream nor changes the version in this time did not finish. */
const UPDATE_TIMEOUT_MS = 3 * 60 * 1000
const timedOut = ref(false)
let updateTimer: ReturnType<typeof setTimeout> | null = null

function clearUpdateTimer() {
  if (updateTimer) clearTimeout(updateTimer)
  updateTimer = null
}

watch(
  () => [bridge.restartState, bridge.restartReason, bridge.restartStartedAt] as const,
  ([state, reason, startedAt]) => {
    clearUpdateTimer()
    timedOut.value = false
    if (reason !== 'update' || (state !== 'stopping' && state !== 'restarting')) return
    const left = Math.max(0, UPDATE_TIMEOUT_MS - (Date.now() - (startedAt ?? Date.now())))
    updateTimer = setTimeout(() => (timedOut.value = true), left)
  },
  { immediate: true },
)

watch(() => bridge.restartState, (state) => {
  clearAutoHide()
  if (state === 'ready') {
    autoHideTimer = setTimeout(() => bridge.dismissRestart(), 5000)
  }
})

onUnmounted(() => {
  clearAutoHide()
  clearUpdateTimer()
})

const isUpdate = computed(() => bridge.restartReason === 'update')

const label = computed(() => {
  const state = bridge.restartState
  if (isUpdate.value) {
    if (timedOut.value) return t('restart.updateTimedOut')
    if (state === 'ready') return t('restart.updated')
    if (state === 'error') return t('restart.updateFailed')
    return t('restart.updating')
  }
  const map: Record<string, string> = {
    stopping: 'restart.stopping',
    restarting: 'restart.restarting',
    ready: 'restart.ready',
    error: 'restart.error',
  }
  return map[state] ? t(map[state]!) : ''
})

const status = computed<'running' | 'done' | 'error'>(() =>
  bridge.restartState === 'ready' ? 'done' : bridge.restartState === 'error' || timedOut.value ? 'error' : 'running',
)
const nextStep = computed(() => (timedOut.value ? t('restart.updateTimedOutNext') : t('restart.errorNext')))

function openLogs() {
  void router.push({ path: '/diagnostics', query: { tab: 'logs' } })
}
</script>

<template>
  <Transition
    enter-active-class="transition-all duration-300 ease-out"
    leave-active-class="transition-all duration-200 ease-in"
    enter-from-class="opacity-0 max-h-0"
    enter-to-class="opacity-100 max-h-12"
    leave-from-class="opacity-100 max-h-12"
    leave-to-class="opacity-0 max-h-0"
  >
    <div v-if="visible" class="overflow-hidden">
      <LongOperation
        variant="banner"
        :label="label"
        :status="status"
        :started-at="bridge.restartStartedAt"
        :next-step="nextStep"
      >
        <template #actions>
          <button
            type="button"
            data-restart-logs
            class="cursor-pointer rounded-(--radius-button) px-2 py-1 text-sm font-medium underline underline-offset-2"
            @click="openLogs"
          >
            {{ t('restart.openLogs') }}
          </button>
          <button
            type="button"
            data-restart-dismiss
            class="cursor-pointer rounded-(--radius-button) px-2 py-1 text-sm font-medium"
            @click="bridge.dismissRestart()"
          >
            {{ t('restart.dismiss') }}
          </button>
        </template>
      </LongOperation>
    </div>
  </Transition>
</template>
