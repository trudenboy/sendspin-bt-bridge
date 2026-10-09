import { api, unwrap } from './client'

export function playClick(deviceId: string) {
  return unwrap(api().POST('/api/v1/calibration/play', { body: { device_id: deviceId } }))
}

export function setMetronome(deviceId: string, action: 'start' | 'stop') {
  return unwrap(api().POST('/api/v1/calibration/metronome', { body: { device_id: deviceId, action } }))
}

export function createSession() {
  return unwrap(api().POST('/api/v1/calibration/sessions'))
}

export interface CalibrationResult {
  status: 'waiting_for_other_recording' | 'complete'
  valid?: boolean
  estimate?: { delay_ms: number | null; confidence: number; valid: boolean; reason?: string | null }
  error?: string
}

export async function uploadRecording(
  sessionId: string,
  role: 'reference' | 'target',
  recording: { samples: number[]; sample_rate: number },
) {
  return (await unwrap(
    api().POST('/api/v1/calibration/sessions/{session_id}/audio', {
      params: { path: { session_id: sessionId } },
      body: { role, ...recording },
    }),
  )) as unknown as CalibrationResult
}

export function endSession(sessionId: string) {
  return unwrap(api().DELETE('/api/v1/calibration/sessions/{session_id}', { params: { path: { session_id: sessionId } } }))
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
