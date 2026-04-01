<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { Loader2, CheckCircle2, AlertTriangle } from 'lucide-vue-next'

const { t } = useI18n()
const bridge = useBridgeStore()

const elapsed = ref(0)
let elapsedTimer: ReturnType<typeof setInterval> | null = null
let autoHideTimer: ReturnType<typeof setTimeout> | null = null

const visible = computed(() => bridge.restartState !== 'idle')

function startElapsedTimer() {
  stopElapsedTimer()
  elapsed.value = 0
  elapsedTimer = setInterval(() => {
    if (bridge.restartStartedAt) {
      elapsed.value = Math.floor((Date.now() - bridge.restartStartedAt) / 1000)
    }
  }, 1000)
}

function stopElapsedTimer() {
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function clearAutoHide() {
  if (autoHideTimer) {
    clearTimeout(autoHideTimer)
    autoHideTimer = null
  }
}

watch(() => bridge.restartState, (state) => {
  if (state === 'stopping') {
    startElapsedTimer()
    clearAutoHide()
  } else if (state === 'ready') {
    stopElapsedTimer()
    clearAutoHide()
    autoHideTimer = setTimeout(() => {
      bridge.dismissRestart()
    }, 5000)
  } else if (state === 'idle') {
    stopElapsedTimer()
    clearAutoHide()
  } else if (state === 'error') {
    stopElapsedTimer()
    clearAutoHide()
  }
})

onUnmounted(() => {
  stopElapsedTimer()
  clearAutoHide()
})

const bannerClass = computed(() => {
  switch (bridge.restartState) {
    case 'stopping':
    case 'restarting':
      return 'bg-info/10 border-info/20 text-info'
    case 'ready':
      return 'bg-success/10 border-success/20 text-success'
    case 'error':
      return 'bg-warning/10 border-warning/20 text-warning'
    default:
      return ''
  }
})

const messageKey = computed(() => {
  const map: Record<string, string> = {
    stopping: 'restart.stopping',
    restarting: 'restart.restarting',
    ready: 'restart.ready',
    error: 'restart.error',
  }
  return map[bridge.restartState] ?? ''
})

const iconComponent = computed(() => {
  switch (bridge.restartState) {
    case 'stopping':
    case 'restarting':
      return Loader2
    case 'ready':
      return CheckCircle2
    case 'error':
      return AlertTriangle
    default:
      return null
  }
})

const showSpinner = computed(() =>
  bridge.restartState === 'stopping' || bridge.restartState === 'restarting',
)

const showElapsed = computed(() =>
  bridge.restartState === 'stopping' || bridge.restartState === 'restarting',
)
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
    <div
      v-if="visible"
      class="overflow-hidden border-b"
      :class="bannerClass"
    >
      <div class="flex items-center justify-center gap-3 px-4 py-2.5">
        <component
          :is="iconComponent"
          v-if="iconComponent"
          class="h-4 w-4 shrink-0"
          :class="{ 'animate-spin': showSpinner }"
        />
        <span class="text-sm font-medium">{{ t(messageKey) }}</span>
        <span
          v-if="showElapsed"
          class="text-sm opacity-70"
        >
          {{ t('restart.elapsed', { seconds: elapsed }) }}
        </span>
      </div>
    </div>
  </Transition>
</template>
