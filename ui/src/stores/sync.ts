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

/** Session states after which nothing more happens. */
export const ENDED: ReadonlySet<string> = new Set<string>(['done', 'cancelled', 'failed'])
const DEBOUNCE_MS = 300

/** The problem ``code`` of an API failure, ``error`` for anything else. */
export function errorCode(e: unknown): string {
  return e instanceof ApiError ? e.code : 'error'
}

/** The last live delay write that failed: which member, why. ``seq`` tells repeats apart. */
export interface WriteFailure {
  member: string
  code: string
  seq: number
}

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

  const writeFailure = ref<WriteFailure | null>(null)
  /** Sessions this page started; any other live session of the group belongs to another client. */
  const owned = ref<Set<string>>(new Set())

  const pending = new Map<string, ReturnType<typeof setTimeout>>()
  const latest = new Map<string, number>()
  /** Writes sent and not answered yet, per member. */
  const inFlight = new Map<string, Set<Promise<void>>>()
  let failureSeq = 0

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

  /**
   * Member ``delay_ms`` follows what was just written, ahead of the next read.
   * Members are replaced, not edited in place: the session's members may be the same objects.
   */
  function setMemberDelays(delays: Record<string, number>) {
    const g = group.value
    if (!g) return
    if (!g.members.some((m) => delays[m.ma_player_id] !== undefined)) return
    group.value = {
      ...g,
      members: g.members.map((m) => {
        const ms = delays[m.ma_player_id]
        return ms === undefined ? m : { ...m, delay_ms: ms }
      }),
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

  /** After a failed live write, show what Music Assistant really holds (or what was there before). */
  async function rollBack(member: string, before: number | null | undefined, code: string) {
    const g = group.value
    if (g) {
      try {
        const fresh = await api.getSyncGroup(g.id)
        if (group.value?.id === fresh.id) group.value = fresh
      } catch {
        // Unreadable: the optimistic value is surely wrong, the one before it is the best guess.
        if (group.value?.id === g.id && before != null && !hasPendingWrite(member)) setMemberDelays({ [member]: before })
      }
    }
    error.value = code
    writeFailure.value = { member, code, seq: ++failureSeq }
  }

  async function start(groupId: string, mode: SessionMode, point: SessionPoint = 'device') {
    const created = await track(() => api.createSession({ group_id: groupId, mode, point }))
    owned.value = new Set([...owned.value, created.id])
    session.value = created
    return created
  }

  async function end() {
    const current = session.value
    if (!current) return
    await track(() => api.endSession(current.id))
    if (session.value?.id === current.id) session.value = null
  }

  function owns(id: string) {
    return owned.value.has(id)
  }

  /**
   * "Retry" on a session whose restore keeps failing: ask again (a DELETE never changes the outcome of
   * a restoring or finished session; the bridge keeps retrying anyway), then read it back.
   */
  async function retrySessionRestore() {
    const current = session.value
    if (!current) return
    try {
      await api.endSession(current.id)
    } catch {
      /* the read below shows where it stands */
    }
    try {
      applyEvent(await api.getSession(current.id))
      error.value = null
    } catch (e) {
      error.value = errorCode(e)
    }
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

  /** A local edit of this member is waiting for its debounce or for Music Assistant's answer. */
  function hasPendingWrite(member: string) {
    return pending.has(member) || (inFlight.get(member)?.size ?? 0) > 0
  }

  /** Send this member's latest edit now (the debounce timer has fired or is flushed). */
  function sendWrite(member: string): Promise<void> | null {
    const value = latest.get(member)
    latest.delete(member)
    if (value === undefined) return null
    const s = session.value
    let call: Promise<unknown>
    if (s && s.mode === 'manual' && !ENDED.has(s.state)) {
      call = api.sessionDelay(s.id, member, value).then((answer) => applyEvent(answer))
    } else if (group.value) {
      call = api.writeGroupDelays(group.value.id, { [member]: value })
    } else {
      return null
    }
    const before = group.value?.members.find((m) => m.ma_player_id === member)?.delay_ms
    setMemberDelays({ [member]: value })
    const set = inFlight.get(member) ?? new Set<Promise<void>>()
    inFlight.set(member, set)
    const done: Promise<void> = call
      .then(
        () => {
          error.value = null
        },
        (e) => {
          set.delete(done)
          return rollBack(member, before, errorCode(e))
        },
      )
      .finally(() => {
        set.delete(done)
        if (set.size === 0 && inFlight.get(member) === set) inFlight.delete(member)
      })
    set.add(done)
    return done
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
        void sendWrite(member)
      }, DEBOUNCE_MS),
    )
  }

  /** Send every waiting edit now and wait until Music Assistant answered them all (failures included). */
  async function flushDelays(): Promise<void> {
    for (const [member, t] of [...pending]) {
      clearTimeout(t)
      pending.delete(member)
      sendWrite(member)
    }
    const all = [...inFlight.values()].flatMap((set) => [...set])
    await Promise.allSettled(all)
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
    writeFailure,
    owns,
    retrySessionRestore,
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
    flushDelays,
    hasPendingWrite,
    fetchProfiles,
    saveProfile,
    applyProfile,
    renameProfile,
    removeProfile,
  }
})
