import { ref, onUnmounted } from 'vue'
import type { AsyncJob } from '@/api/types'

/**
 * Generic composable for polling async job results.
 *
 * Pattern: POST to start → receive job_id → GET result until completed/failed.
 */
export function useAsyncJob<T>() {
  const loading = ref(false)
  const jobId = ref<string | null>(null)
  const result = ref<T | null>(null) as { value: T | null }
  const error = ref<string | null>(null)

  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function start(
    startFn: () => Promise<AsyncJob>,
    pollFn: (id: string) => Promise<AsyncJob<T>>,
    interval = 1000,
  ) {
    loading.value = true
    error.value = null
    result.value = null
    stopPolling()

    try {
      const job = await startFn()
      jobId.value = job.job_id

      pollTimer = setInterval(async () => {
        try {
          const res = await pollFn(jobId.value!)
          if (res.status === 'completed') {
            result.value = res.result ?? null
            loading.value = false
            stopPolling()
          } else if (res.status === 'failed') {
            error.value = res.error || 'Job failed'
            loading.value = false
            stopPolling()
          }
        } catch (e) {
          error.value = String(e)
          loading.value = false
          stopPolling()
        }
      }, interval)
    } catch (e) {
      error.value = String(e)
      loading.value = false
    }
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  onUnmounted(stopPolling)

  return { loading, jobId, result, error, start, cancel: stopPolling }
}
