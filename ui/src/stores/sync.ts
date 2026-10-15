import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '@/api/sync'
import { ApiError } from '@/api/client'
import type {
  PendingRestore,
  SessionMode,
  SessionPoint,
  SyncGroup,
  SyncProfile,
  SyncSession,
} from '@/api/types'

const ENDED = new Set<string>(['done', 'cancelled', 'failed'])
const DEBOUNCE_MS = 300

/**
 * Group sync tuning: the group on screen, its tuning session, its profiles and
 * the restores left over from a restart.
 *
 * The session is owned by the bridge: ``sync`` events are authoritative. Control
 * calls answer with the session too, but their answer only goes through
 * ``applyEvent`` (so an answer that is not this session changes nothing).
 */
export const useSyncStore = defineStore('sync', () => {
  const groups = ref<SyncGroup[]>([])
  const group = ref<SyncGroup | null>(null)
  const session = ref<SyncSession | null>(null)
  const profiles = ref<SyncProfile[]>([])
  const restores = ref<PendingRestore[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  const pending = new Map<string, ReturnType<typeof setTimeout>>()
  const latest = new Map<string, number>()

  function errorCode(e: unknown) {
    return e instanceof ApiError ? e.code : 'error'
  }

  /** Run a call, keeping its failure code in ``error``; rethrows so callers can react. */
  async function track<T>(call: () => Promise<T>): Promise<T> {
    try {
      const out = await call()
      error.value = null
      return out
    } catch (e) {
      error.value = errorCode(e)
      throw e
    }
  }

  function cancelPendingWrites() {
    for (const t of pending.values()) clearTimeout(t)
    pending.clear()
    latest.clear()
  }

  /* -- groups ---------------------------------------------------------------------- */

  async function fetchGroups() {
    groups.value = await track(() => api.getSyncGroups())
  }

  async function fetchGroup(groupId: string) {
    loading.value = true
    try {
      const next = await track(() => api.getSyncGroup(groupId))
      if (group.value?.id !== next.id) {
        cancelPendingWrites()
        if (session.value && session.value.group_id !== next.id) session.value = null
      }
      group.value = next
    } finally {
      loading.value = false
    }
  }

  /** Open a group's page: the group, and the session already tuning it (another tab, a reload). */
  async function open(groupId: string) {
    await fetchGroup(groupId)
    const sid = group.value?.session_id
    if (sid && session.value?.id !== sid) {
      try {
        applyEvent(await api.getSession(sid))
      } catch (e) {
        error.value = errorCode(e)
      }
    }
  }

  /** Member ``delay_ms`` follows what was just written, ahead of the next read. */
  function setMemberDelays(delays: Record<string, number>) {
    for (const m of group.value?.members ?? []) {
      const ms = delays[m.ma_player_id]
      if (ms !== undefined) m.delay_ms = ms
    }
  }

  /* -- restores -------------------------------------------------------------------- */

  /** Not through ``track``: the restore list must not clear or replace the group's error. */
  async function fetchRestores() {
    restores.value = await api.listRestores()
  }

  function restoreFor(groupId: string) {
    return restores.value.find((r) => r.group_id === groupId)
  }

  async function retryRestore(groupId: string) {
    try {
      const next = await api.retryRestore(groupId)
      restores.value = [...restores.value.filter((r) => r.session_id !== next.session_id), next]
      error.value = null
    } catch (e) {
      if (e instanceof ApiError && e.code === 'not_found') {
        // Restored in the meantime: nothing left to retry.
        restores.value = restores.value.filter((r) => r.group_id !== groupId)
        return
      }
      error.value = errorCode(e)
    }
  }

  /* -- session --------------------------------------------------------------------- */

  /** A ``sync`` event (or a control call's answer): taken when it is this session, or a live one of this group. */
  function applyEvent(next: SyncSession) {
    if (!next || typeof next !== 'object' || !next.id) return
    const held = session.value
    if (held) {
      if (next.id === held.id) session.value = next
      // A held session that ended gives way to a new live one of its group (another tab, a restart after timeout).
      else if (ENDED.has(held.state) && next.group_id === held.group_id && !ENDED.has(next.state)) session.value = next
      return
    }
    if (group.value && next.group_id === group.value.id && !ENDED.has(next.state)) session.value = next
  }

  /** Catch up after the event stream was down: ``sync`` events are not replayed. */
  async function resync() {
    const held = session.value
    if (held) {
      try {
        applyEvent(await api.getSession(held.id))
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          if (session.value?.id === held.id) session.value = null
        } else {
          error.value = errorCode(e)
        }
      }
    }
    const g = group.value
    if (g) {
      try {
        await open(g.id)
      } catch {
        /* the failure is kept in ``error`` */
      }
    }
  }

  /** After a failed live write, show what Music Assistant really holds. */
  async function rollBack(code: string) {
    const g = group.value
    if (g) {
      try {
        const fresh = await api.getSyncGroup(g.id)
        if (group.value?.id === fresh.id) group.value = fresh
      } catch {
        /* keep the optimistic value; the error below says the write failed */
      }
    }
    error.value = code
  }

  async function start(groupId: string, mode: SessionMode, point: SessionPoint = 'device') {
    const created = await track(() => api.createSession({ group_id: groupId, mode, point }))
    session.value = created
    return created
  }

  async function end() {
    const current = session.value
    if (!current) return
    await track(() => api.endSession(current.id))
    if (session.value?.id === current.id) session.value = null
  }

  /** A control call on the open session; its answer is fed through ``applyEvent``. */
  async function control(call: (id: string) => Promise<SyncSession>) {
    const current = session.value
    if (!current) return
    applyEvent(await track(() => call(current.id)))
  }

  const signal = (action: 'start' | 'stop') => control((id) => api.sessionSignal(id, action))
  const solo = (member: string | null) => control((id) => api.sessionSolo(id, member))
  const ab = (a: string, b: string) => control((id) => api.sessionAb(id, a, b))
  const reference = (member: string) => control((id) => api.sessionReference(id, member))
  const skip = (member: string) => control((id) => api.sessionSkip(id, member))
  const apply = (rows?: Record<string, number>, verify?: boolean) =>
    control((id) => api.applyProposal(id, rows, verify))
  const undo = () => control((id) => api.undoApply(id))
  const verify = () => control((id) => api.verifySession(id))

  /** Compute a proposal from the measurements; the session event carries it too. */
  async function propose() {
    const current = session.value
    if (!current) return null
    return track(() => api.computeProposal(current.id))
  }

  /** A live delay edit, written 300 ms after the last change of that member. */
  function setDelay(member: string, ms: number) {
    latest.set(member, ms)
    const t = pending.get(member)
    if (t) clearTimeout(t)
    pending.set(
      member,
      setTimeout(() => {
        pending.delete(member)
        const value = latest.get(member)
        latest.delete(member)
        if (value === undefined) return
        const s = session.value
        let call: Promise<unknown>
        if (s && s.mode === 'manual' && !ENDED.has(s.state)) {
          call = api.sessionDelay(s.id, member, value).then((answer) => applyEvent(answer))
        } else if (group.value) {
          call = api.writeGroupDelays(group.value.id, { [member]: value })
        } else {
          return
        }
        setMemberDelays({ [member]: value })
        void call.then(
          () => {
            error.value = null
          },
          (e) => rollBack(errorCode(e)),
        )
      }, DEBOUNCE_MS),
    )
  }

  /* -- profiles -------------------------------------------------------------------- */

  async function fetchProfiles(groupId: string) {
    profiles.value = await track(() => api.listProfiles(groupId))
  }

  /** Save the group's current delays; ``notes`` is sent only when there are some. */
  async function saveProfile(name: string, kind: 'device' | 'position' = 'device', notes?: string) {
    const g = group.value
    if (!g) return null
    const body: { name: string; kind: 'device' | 'position'; notes?: string } = { name, kind }
    if (notes) body.notes = notes
    const created = await track(() => api.captureProfile(g.id, body))
    profiles.value = [...profiles.value, created]
    return created
  }

  async function applyProfile(id: string) {
    const result = await track(() => api.applyProfile(id))
    setMemberDelays(result.applied)
    return result
  }

  async function renameProfile(id: string, name: string) {
    const updated = await track(() => api.patchProfile(id, { name }))
    profiles.value = profiles.value.map((p) => (p.id === id ? updated : p))
    return updated
  }

  async function removeProfile(id: string) {
    await track(() => api.deleteProfile(id))
    profiles.value = profiles.value.filter((p) => p.id !== id)
  }

  return {
    groups,
    group,
    session,
    profiles,
    restores,
    loading,
    error,
    fetchGroups,
    fetchGroup,
    open,
    resync,
    fetchRestores,
    restoreFor,
    retryRestore,
    applyEvent,
    start,
    end,
    signal,
    solo,
    ab,
    reference,
    skip,
    propose,
    apply,
    undo,
    verify,
    setDelay,
    fetchProfiles,
    saveProfile,
    applyProfile,
    renameProfile,
    removeProfile,
  }
})
