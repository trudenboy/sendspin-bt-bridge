import { ref } from 'vue'
import { ApiError } from '@/api/client'
import type { Job } from '@/api/types'
import { useJobsStore } from '@/stores/jobs'

/**
 * Start a long-running operation and follow it to the end.
 *
 * ``start`` resolves to the job's result, or ``null`` when it failed
 * (``error`` then holds the reason).
 */
export function useAsyncJob<T>() {
  const loading = ref(false)
  const job = ref<Job | null>(null)
  const result = ref<T | null>(null) as { value: T | null }
  const error = ref<string | null>(null)

  async function start(startFn: () => Promise<Job>): Promise<T | null> {
    loading.value = true
    error.value = null
    result.value = null
    try {
      const started = await startFn()
      job.value = started
      const finished = await useJobsStore().waitFor(started)
      job.value = finished
      if (finished.status === 'succeeded') {
        result.value = finished.result as T
        return result.value
      }
      error.value = finished.error?.detail || finished.error?.code || `Job ${finished.status}`
      return null
    } catch (e) {
      error.value = e instanceof ApiError ? e.message : String(e)
      return null
    } finally {
      loading.value = false
    }
  }

  return { loading, job, result, error, start }
}
