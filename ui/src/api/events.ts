import { apiGet } from './client'
import type { EventRecord, EventStoreStats } from './types'

export function queryEvents(params?: {
  player_id?: string
  type?: string
  since?: string
  limit?: number
}) {
  const query = new URLSearchParams()
  if (params?.player_id) query.set('player_id', params.player_id)
  if (params?.type) query.set('type', params.type)
  if (params?.since) query.set('since', params.since)
  if (params?.limit) query.set('limit', String(params.limit))

  const qs = query.toString()
  return apiGet<EventRecord[]>(`/api/events${qs ? `?${qs}` : ''}`)
}

export function getEventStats() {
  return apiGet<EventStoreStats>('/api/events/stats')
}
