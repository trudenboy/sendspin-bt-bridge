import { getCurrentInstance, onBeforeUnmount, onMounted, readonly, ref, type Ref } from 'vue'

const TICK_MS = 10_000

/**
 * The current time in ms for relative labels (standby minutes, stale signal
 * readings), refreshed every 10 s while the calling component is mounted.
 */
export function useNow(): Readonly<Ref<number>> {
  const now = ref(Date.now())
  if (getCurrentInstance()) {
    let timer: ReturnType<typeof setInterval> | null = null
    onMounted(() => {
      now.value = Date.now()
      timer = setInterval(() => (now.value = Date.now()), TICK_MS)
    })
    onBeforeUnmount(() => {
      if (timer) clearInterval(timer)
    })
  }
  return readonly(now)
}
