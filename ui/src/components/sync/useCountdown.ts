import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

/**
 * Whole seconds left of a countdown the bridge reports (e.g. ``music_returns_in_s``), counted down
 * here once a second between reports; ``null`` while there is nothing to count.
 */
export function useCountdown(seconds: () => number | null | undefined): Ref<number | null> {
  const left = ref<number | null>(null)
  let deadline: number | null = null
  let timer: ReturnType<typeof setInterval> | null = null

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
  }

  function tick() {
    if (deadline == null) {
      left.value = null
      return
    }
    left.value = Math.max(0, Math.ceil((deadline - Date.now()) / 1000 - 1e-6))
    if (left.value <= 0) stop()
  }

  watch(
    seconds,
    (s) => {
      if (s == null) {
        deadline = null
        stop()
        tick()
        return
      }
      deadline = Date.now() + s * 1000
      tick()
      if (!timer && (left.value ?? 0) > 0) timer = setInterval(tick, 1000)
    },
    { immediate: true },
  )
  onBeforeUnmount(stop)
  return left
}
