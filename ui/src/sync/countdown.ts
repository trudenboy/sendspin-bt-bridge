import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

/**
 * A count of seconds left that ticks down locally between readings from the bridge (each reading
 * replaces it). ``null`` while there is nothing to count (the clicks do not play).
 */
export function useCountdown(read: () => number | null | undefined): Ref<number | null> {
  const left = ref<number | null>(null)
  let base: { at: number; value: number } | null = null
  let timer: ReturnType<typeof setInterval> | null = null

  function tick() {
    left.value = base ? Math.max(0, base.value - (Date.now() - base.at) / 1000) : null
  }
  watch(
    read,
    (value) => {
      base = value == null ? null : { at: Date.now(), value }
      tick()
      if (base && !timer) timer = setInterval(tick, 1000)
      if (!base && timer) {
        clearInterval(timer)
        timer = null
      }
    },
    { immediate: true },
  )
  onBeforeUnmount(() => {
    if (timer) clearInterval(timer)
    timer = null
  })
  return left
}
