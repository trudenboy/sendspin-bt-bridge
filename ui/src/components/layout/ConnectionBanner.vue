<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { WifiOff } from 'lucide-vue-next'

/**
 * Tells the user when the live connection to the bridge is gone, so the
 * page is not silently showing stale state. The event stream reconnects by
 * design every so often; only a gap longer than GRACE_MS counts. A restart
 * has its own banner.
 */
const GRACE_MS = 4000

const { t } = useI18n()
const bridge = useBridgeStore()
const lost = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

const restarting = computed(() => bridge.restartState === 'stopping' || bridge.restartState === 'restarting')

function clear() {
  if (timer) clearTimeout(timer)
  timer = null
}

watch(
  () => bridge.sseConnected,
  (connected) => {
    clear()
    if (connected) {
      lost.value = false
      return
    }
    timer = setTimeout(() => (lost.value = true), GRACE_MS)
  },
)

onBeforeUnmount(clear)
</script>

<template>
  <div
    v-if="lost && !restarting"
    class="sticky top-16 z-20 flex items-center justify-center gap-2 border-b border-warning/40 bg-warning/15 px-4 py-2 text-sm text-text-primary"
    role="status"
  >
    <WifiOff class="size-4 shrink-0 text-warning" aria-hidden="true" />
    {{ t('connection.lost') }}
  </div>
</template>
