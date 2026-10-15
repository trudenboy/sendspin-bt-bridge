/* Group sync tuning: Music Assistant groups, tuning sessions, profiles, restores. */
import { api, apiUrl, ApiError, getCsrfToken, unwrap, type Problem } from './client'
import type {
  PendingRestore,
  SessionMode,
  SessionPoint,
  SyncGroup,
  SyncMeasurement,
  SyncProfile,
  SyncProfileApplied,
  SyncProposal,
  SyncSession,
} from './types'

const sessionPath = (id: string) => ({ params: { path: { session_id: id } } })

/* -- groups -------------------------------------------------------------------------- */

/** Every Music Assistant sync group; a group whose members could not be read carries ``error``. */
export function getSyncGroups(): Promise<SyncGroup[]> {
  return unwrap(api().GET('/api/v1/sync/groups'))
}

export function getSyncGroup(groupId: string): Promise<SyncGroup> {
  return unwrap(api().GET('/api/v1/sync/groups/{group_id}', { params: { path: { group_id: groupId } } }))
}

/** Write delays (0–5000 ms) outside a session: all are checked before any is written. */
export function writeGroupDelays(groupId: string, delays: Record<string, number>): Promise<SyncGroup> {
  return unwrap(
    api().PUT('/api/v1/sync/groups/{group_id}/delays', { params: { path: { group_id: groupId } }, body: { delays } }),
  )
}

/* -- restores left from before a restart ---------------------------------------------- */

export function listRestores(): Promise<PendingRestore[]> {
  return unwrap(api().GET('/api/v1/sync/restores'))
}

/** Try the group's pending restore now (404 ``not_found`` when there is none any more). */
export function retryRestore(groupId: string): Promise<PendingRestore> {
  return unwrap(api().POST('/api/v1/sync/groups/{group_id}/restore/retry', { params: { path: { group_id: groupId } } }))
}

/* -- sessions ------------------------------------------------------------------------ */

export function createSession(body: { group_id: string; mode: SessionMode; point?: SessionPoint }): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions', { body }))
}

export function getSession(id: string): Promise<SyncSession> {
  return unwrap(api().GET('/api/v1/sync/sessions/{session_id}', sessionPath(id)))
}

export async function endSession(id: string): Promise<void> {
  await unwrap(api().DELETE('/api/v1/sync/sessions/{session_id}', sessionPath(id)))
}

export function sessionSignal(id: string, action: 'start' | 'stop'): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/signal', { ...sessionPath(id), body: { action } }))
}

/** Hear one member alone; ``null`` unmutes everyone. */
export function sessionSolo(id: string, member: string | null): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/solo', { ...sessionPath(id), body: { member } }))
}

export function sessionAb(id: string, a: string, b: string): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/ab', { ...sessionPath(id), body: { a, b } }))
}

export function sessionReference(id: string, member: string): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/reference', { ...sessionPath(id), body: { member } }))
}

export function sessionDelay(id: string, member: string, ms: number): Promise<SyncSession> {
  return unwrap(
    api().PUT('/api/v1/sync/sessions/{session_id}/delays/{member}', {
      params: { path: { session_id: id, member } },
      body: { ms },
    }),
  )
}

export function sessionSkip(id: string, member: string): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/skip', { ...sessionPath(id), body: { member } }))
}

/** The session's current proposal, read-only (409 ``no_measurement`` before there is one). */
export function getProposal(id: string): Promise<SyncProposal> {
  return unwrap(api().GET('/api/v1/sync/sessions/{session_id}/proposal', sessionPath(id)))
}

/** Compute a proposal from the measurements so far. */
export function computeProposal(id: string): Promise<SyncProposal> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/proposal', sessionPath(id)))
}

/** Write the proposal, or ``rows`` instead of it; ``verify`` measures again afterwards. */
export function applyProposal(id: string, rows?: Record<string, number>, verify?: boolean): Promise<SyncSession> {
  const body: { rows?: Record<string, number>; verify?: boolean } = {}
  if (rows) body.rows = rows
  if (verify !== undefined) body.verify = verify
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/apply', { ...sessionPath(id), body }))
}

export function undoApply(id: string): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/undo', sessionPath(id)))
}

export function verifySession(id: string): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/verify', sessionPath(id)))
}

/** One capture as int16 little-endian PCM (raw body, so not through the JSON client). */
export async function uploadRecording(
  id: string,
  member: string,
  startTimeS: number,
  sampleRate: number,
  pcm: Int16Array,
): Promise<SyncMeasurement> {
  const query = new URLSearchParams({ member, start_time_s: String(startTimeS), sample_rate: String(sampleRate) })
  const res = await fetch(apiUrl(`/api/v1/sync/sessions/${encodeURIComponent(id)}/recordings?${query}`), {
    method: 'POST',
    headers: { 'content-type': 'application/octet-stream', 'X-CSRF-Token': getCsrfToken() },
    credentials: 'same-origin',
    body: pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength) as ArrayBuffer,
  })
  if (!res.ok) {
    let problem: Problem
    try {
      problem = (await res.json()) as Problem
    } catch {
      problem = { status: res.status, code: 'http_error', title: res.statusText || 'Request failed' }
    }
    throw new ApiError(problem)
  }
  return (await res.json()) as SyncMeasurement
}

/* -- profiles ------------------------------------------------------------------------ */

export function listProfiles(groupId: string): Promise<SyncProfile[]> {
  return unwrap(api().GET('/api/v1/sync/profiles', { params: { query: { group_id: groupId } } }))
}

export function createProfile(body: {
  group_id: string
  name: string
  delays: Record<string, number>
  kind?: 'device' | 'position'
  notes?: string
}): Promise<SyncProfile> {
  return unwrap(api().POST('/api/v1/sync/profiles', { body }))
}

export function patchProfile(id: string, body: { name?: string; notes?: string }): Promise<SyncProfile> {
  return unwrap(api().PATCH('/api/v1/sync/profiles/{profile_id}', { params: { path: { profile_id: id } }, body }))
}

export async function deleteProfile(id: string): Promise<void> {
  await unwrap(api().DELETE('/api/v1/sync/profiles/{profile_id}', { params: { path: { profile_id: id } } }))
}

export function applyProfile(id: string): Promise<SyncProfileApplied> {
  return unwrap(api().POST('/api/v1/sync/profiles/{profile_id}/apply', { params: { path: { profile_id: id } } }))
}

/** Save the group's current delays as a profile. */
export function captureProfile(
  groupId: string,
  body: { name: string; kind?: 'device' | 'position'; notes?: string },
): Promise<SyncProfile> {
  return unwrap(
    api().POST('/api/v1/sync/groups/{group_id}/capture', { params: { path: { group_id: groupId } }, body }),
  )
}
