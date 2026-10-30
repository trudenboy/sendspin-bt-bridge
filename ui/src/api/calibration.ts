import { api, unwrap } from './client'

export function playClick(deviceId: string) {
  return unwrap(api().POST('/api/v1/calibration/play', { body: { device_id: deviceId } }))
}

export async function getLatencyHistory(deviceId: string) {
  return (await unwrap(
    api().GET('/api/v1/devices/{device_id}/latency/history', { params: { path: { device_id: deviceId } } }),
  )) as unknown as { samples: TimingSample[] }
}

export interface TimingSample {
  timing_sampled_at?: string | null
  backend_output_latency_ms?: number | null
  buffered_audio_ms?: number | null
  playback_sync_error_ms?: number | null
  clock_uncertainty_ms?: number | null
  reanchor_count?: number | null
}
