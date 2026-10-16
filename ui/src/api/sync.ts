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

/**
 * Best effort while the page goes away (``pagehide``): a keepalive DELETE, so the clicker and
 * the mutes stop promptly instead of at the inactivity timeout. Never throws.
 */
export function endSessionOnPageHide(id: string): void {
  const headers: Record<string, string> = {}
  const csrf = getCsrfToken()
  if (csrf) headers['X-CSRF-Token'] = csrf
  try {
    void fetch(apiUrl(`/api/v1/sync/sessions/${encodeURIComponent(id)}`), {
      method: 'DELETE',
      headers,
      credentials: 'same-origin',
      keepalive: true,
    }).catch(() => {})
  } catch {
    /* the page is going away; the bridge's timeout ends the session */
  }
}

/**
 * ``start``: play the clicker on the group (409 ``group_playing`` while it plays anything else; again while
 * it plays starts it over). ``stop``: stop whatever the group plays (the preparation's "Stop group").
 * ``stop_clicker``: stop the group only while it plays this session's click track.
 */
export function sessionSignal(id: string, action: 'start' | 'stop' | 'stop_clicker'): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/signal', { ...sessionPath(id), body: { action } }))
}

/** Set one member's volume (0–100). */
export function sessionVolume(id: string, member: string, level: number): Promise<SyncSession> {
  return unwrap(
    api().PUT('/api/v1/sync/sessions/{session_id}/volumes/{member}', {
      params: { path: { session_id: id, member } },
      body: { level },
    }),
  )
}

/**
 * Hear one member alone (every other member is muted); ``null`` unmutes everyone. 409 ``clicker_stopped``
 * while the clicker does not play; 409 ``sync_mute_unsupported`` names in ``member`` a player that cannot be muted.
 */
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

/**
 * Write the proposal, or ``rows`` instead of it. The session then goes on as the manual editor
 * (state ``tuning``): the clicks keep playing, solo and A/B work, delays are written live.
 */
export function applyProposal(id: string, rows?: Record<string, number>): Promise<SyncSession> {
  const body: { rows?: Record<string, number> } = {}
  if (rows) body.rows = rows
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/apply', { ...sessionPath(id), body }))
}

/** Put back every delay this session wrote (applied or edited) to the value it had at its start. */
export function resetSession(id: string): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/reset', sessionPath(id)))
}

/** Finish: the delays stay as they are, the group is restored and the session ends as ``done``. */
export function finishSession(id: string): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/finish', sessionPath(id)))
}

/**
 * A link to the phone capture page of this measurement (its token allows only reading the capture
 * state and uploading a recording; a new link revokes the previous one).
 */
export function captureLink(
  id: string,
  lang?: 'en' | 'ru',
): Promise<{ url: string; http_url: string; secure: boolean; token: string; expires_in_s: number }> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/capture-link', { ...sessionPath(id), body: lang ? { lang } : {} }))
}

/** Play the sequence for the phone: every member alone in turn while the phone records once. */
export function startSequence(id: string, members: string[]): Promise<SyncSession> {
  return unwrap(api().POST('/api/v1/sync/sessions/{session_id}/sequence', { ...sessionPath(id), body: { members } }))
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
  const headers: Record<string, string> = { 'content-type': 'application/octet-stream' }
  const csrf = getCsrfToken()
  if (csrf) headers['X-CSRF-Token'] = csrf
  const res = await fetch(apiUrl(`/api/v1/sync/sessions/${encodeURIComponent(id)}/recordings?${query}`), {
    method: 'POST',
    headers,
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
