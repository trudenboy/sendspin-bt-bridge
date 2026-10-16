import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as api from '@/api/sync'
import { ApiError } from '@/api/client'
import type {
  PendingRestore,
  SessionMode,
  SessionPoint,
  SyncGroup,
  SyncProfile,
  SyncSession,
  SyncSound,
} from '@/api/types'

/** Session states after which nothing more happens. */
export const ENDED: ReadonlySet<string> = new Set<string>(['done', 'cancelled', 'failed'])
const DEBOUNCE_MS = 300

/** The problem ``code`` of an API failure, ``error`` for anything else. */
export function errorCode(e: unknown): string {
  return e instanceof ApiError ? e.code : 'error'
}

/** The member a problem is about (``sync_mute_unsupported`` names it), if any. */
export function errorMember(e: unknown): string | null {
  const member = e instanceof ApiError ? e.problem.member : null
  return typeof member === 'string' ? member : null
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
  /**
   * The group this page is starting a session for, while the POST is under way. The bridge's own
   * ``sync`` event for the new session may arrive before the answer: a live session of that group
   * adopted meanwhile is this page's (one session per group, so it cannot be anyone else's).
   */
  const startingGroup = ref<string | null>(null)
  const starting = computed(() => startingGroup.value !== null)
  /** Sessions adopted (and owned) while a start was in flight: kept only if one is the started one. */
  let adoptedDuringStart = new Set<string>()
  /** Sessions that are never the page's session (a quick listen): their events are dropped. */
  const ignored = new Set<string>()
  /** While > 0, no other session is adopted (a quick listen is being created, its id not known yet). */
  let holds = 0
  /** The session that was held when a hold began: an event adopted meanwhile is undone on ignore. */
  let heldBefore: SyncSession | null = null

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

  /** The group's ``session_id`` is only read with the group: forget it once that session is gone. */
  function forgetSessionId(id: string) {
    const g = group.value
    if (g && g.session_id === id) group.value = { ...g, session_id: null }
  }

  /** A ``sync`` event (or a control call's answer): taken when it is this session, or a live one of this group. */
  function applyEvent(next: SyncSession) {
    if (!next || typeof next !== 'object' || !next.id) return
    if (ignored.has(next.id)) return
    if (ENDED.has(next.state)) forgetSessionId(next.id)
    const held = session.value
    if (holds > 0 && held?.id !== next.id) return
    if (held) {
      if (next.id === held.id) session.value = next
      // A held session that ended gives way to a new live one of its group (another tab, a restart after timeout).
      else if (ENDED.has(held.state) && next.group_id === held.group_id && !ENDED.has(next.state)) adopt(next)
      return
    }
    if (group.value && next.group_id === group.value.id && !ENDED.has(next.state)) adopt(next)
  }

  function adopt(next: SyncSession) {
    if (startingGroup.value === next.group_id && !owned.value.has(next.id)) {
      owned.value = new Set([...owned.value, next.id])
      adoptedDuringStart.add(next.id)
    }
    session.value = next
  }

  /** The start is answered: give up every session adopted meanwhile except the one created (I1). */
  function settleAdopted(createdId: string | null) {
    const drop = [...adoptedDuringStart].filter((id) => id !== createdId)
    adoptedDuringStart = new Set()
    if (drop.length) owned.value = new Set([...owned.value].filter((id) => !drop.includes(id)))
  }

  /** Catch up after the event stream was down: ``sync`` events are not replayed. */
  async function resync() {
    const held = session.value
    if (held) {
      try {
        applyEvent(await api.getSession(held.id))
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          forgetSessionId(held.id)
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
    startingGroup.value = groupId
    adoptedDuringStart = new Set()
    let createdId: string | null = null
    try {
      const created = await track(() => api.createSession({ group_id: groupId, mode, point }))
      createdId = created.id
      owned.value = new Set([...owned.value, created.id])
      session.value = created
      return created
    } finally {
      if (startingGroup.value === groupId) startingGroup.value = null
      settleAdopted(createdId)
    }
  }

  async function end() {
    const current = session.value
    if (!current) return
    await track(() => api.endSession(current.id))
    forgetSessionId(current.id)
    if (session.value?.id === current.id) session.value = null
  }

  /** Hold the page's session while a side session (quick listen) is created; returns the release. */
  function holdSession(): () => void {
    if (holds === 0) heldBefore = session.value
    holds++
    let released = false
    return () => {
      if (released) return
      released = true
      holds--
    }
  }

  /** Never take this session as the page's session (and undo it if it was taken). */
  function ignoreSession(id: string) {
    ignored.add(id)
    if (session.value?.id === id) session.value = heldBefore && heldBefore.id !== id ? heldBefore : null
  }

  /** End another client's session (the operator confirmed): nobody stays locked out. */
  async function endForeign(id: string) {
    await track(() => api.endSession(id))
    forgetSessionId(id)
    if (session.value?.id === id) session.value = null
    const g = group.value
    if (g) await fetchGroup(g.id).catch(() => {})
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

  /** ``start``: the clicker; ``stop``: whatever the group plays; ``stop_clicker``: only our clicker. */
  const signal = (action: 'start' | 'stop' | 'stop_clicker') => control((id) => api.sessionSignal(id, action))
  /** The manual editor's listening sound (clicks, accent, noise); a playing clicker starts over with it. */
  const sound = (s: SyncSound) => control((id) => api.sessionSound(id, s))
  const volume = (member: string, level: number) => control((id) => api.sessionVolume(id, member, level))
  const solo = (member: string | null) => control((id) => api.sessionSolo(id, member))
  const ab = (a: string, b: string) => control((id) => api.sessionAb(id, a, b))
  const reference = (member: string) => control((id) => api.sessionReference(id, member))
  const skip = (member: string) => control((id) => api.sessionSkip(id, member))
  const apply = (rows?: Record<string, number>) => control((id) => api.applyProposal(id, rows))

  /** Finish the open session: the delays stay, the group is restored (``done``). */
  async function finish() {
    const current = session.value
    if (!current) return
    applyEvent(await track(() => api.finishSession(current.id)))
  }

  /** Take over a session another tab or device started (the operator chose to join it). */
  function join(id: string) {
    owned.value = new Set([...owned.value, id])
  }

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
    // Live through the session while it edits by ear (manual, or semi-automatic after the apply).
    if (s && !ENDED.has(s.state) && (s.mode === 'manual' || s.state === 'tuning')) {
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
    starting,
    holdSession,
    ignoreSession,
    endForeign,
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
    sound,
    volume,
    solo,
    ab,
    reference,
    skip,
    propose,
    apply,
    finish,
    join,
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
