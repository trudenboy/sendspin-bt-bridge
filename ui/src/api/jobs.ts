import { api, unwrap } from './client'

export function getJob(jobId: string) {
  return unwrap(api().GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: jobId } } }))
}

export function cancelJob(jobId: string) {
  return unwrap(api().DELETE('/api/v1/jobs/{job_id}', { params: { path: { job_id: jobId } } }))
}
