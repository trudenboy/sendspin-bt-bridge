import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getJob } from '@/api/jobs'
import type { Job } from '@/api/types'

/** Every job this page has seen, kept current by ``job`` events (or polling). */
export const useJobsStore = defineStore('jobs', () => {
  const byId = ref<Record<string, Job>>({})
  const waiters = new Map<string, ((job: Job) => void)[]>()

  function update(job: Job) {
    byId.value[job.id] = job
    if (job.status !== 'running') {
      for (const resolve of waiters.get(job.id) ?? []) resolve(job)
      waiters.delete(job.id)
    }
  }

  /** Resolves with the finished job; polls in case no event stream is open. */
  function waitFor(job: Job, pollMs = 1500): Promise<Job> {
    update(job)
    const current = byId.value[job.id]
    if (current && current.status !== 'running') return Promise.resolve(current)
    return new Promise((resolve) => {
      const list = waiters.get(job.id) ?? []
      let timer: ReturnType<typeof setInterval> | null = null
      const done = (finished: Job) => {
        if (timer) clearInterval(timer)
        resolve(finished)
      }
      list.push(done)
      waiters.set(job.id, list)
      timer = setInterval(async () => {
        try {
          update(await getJob(job.id))
        } catch {
          /* expired or unreachable: the next tick or event decides */
        }
      }, pollMs)
    })
  }

  return { byId, update, waitFor }
})
