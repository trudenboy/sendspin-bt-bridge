<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  applyProposal,
  captureProfile,
  computeProposal,
  createSession,
  endSession,
  endSessionOnPageHide,
  getSession,
  sessionSignal,
  sessionSolo,
  undoApply,
  uploadRecording,
} from '@/api/sync'
import { ApiError } from '@/api/client'
import type { SessionPoint, SyncMeasurement, SyncProposal, SyncSession } from '@/api/types'
import { ENDED, errorCode, useSyncStore } from '@/stores/sync'
import { SOLO_SETTLE_MS, SyncCaptureError, SyncRecorder, type Capture } from '@/sync/capture'
import { SbButton, SbSpinner } from '@/kit'
import { formatMs } from '@/sync/format'
import StepPrepare from './StepPrepare.vue'
import StepLevel from './StepLevel.vue'
import StepMembers, { type MemberPhase } from './StepMembers.vue'
import StepProposal from './StepProposal.vue'
import StepDone from './StepDone.vue'

/**
 * The measurement wizard: preparation, level check, speaker by speaker, the proposal
 * (applied by itself in automatic mode, with an undo), an optional check round, and done.
 *
 * The wizard owns the microphone: it is opened from the first "Next" (a user gesture) and
 * closed on cancel, on unmount and when the session ends. Captures share one clock only
 * within one recorder epoch; when the epoch breaks (the audio context stopped, or the
 * microphone was reopened) every member recorded before must be recorded again, so one
 * round never mixes clocks.
 */
const props = defineProps<{
  groupId: string
  mode: 'semi' | 'auto'
  /** For tests: the recorder to use instead of the browser's microphone. */
  recorderFactory?: () => SyncRecorder
}>()
const emit = defineEmits<{
  /** Every member is measured (or skipped): the proposal step takes over. */
  proposal: []
  /** The operator cancelled (the session is ended), or left the done step. */
  done: []
}>()

type Step = 'prepare' | 'level' | 'members' | 'proposal' | 'done'
const STEPS: Step[] = ['prepare', 'level', 'members', 'proposal', 'done']
const QUICK_LISTEN_MS = 10_000

const { t } = useI18n()
const sync = useSyncStore()

const step = ref<Step>('prepare')
const point = ref<SessionPoint>('device')
const starting = ref(false)
const startError = ref('')
/** The session this wizard started; null before the start and after it ended. */
const sessionId = ref<string | null>(null)
const ended = ref(false)

let recorder: SyncRecorder | null = null
let unsubscribers: (() => void)[] = []
let disposed = false
let settleTimer: ReturnType<typeof setTimeout> | null = null
/** Ends the settle wait early (on close), so the waiting step settles and exits. */
let settleDone: (() => void) | null = null
let progressTimer: ReturnType<typeof setInterval> | null = null
const PROGRESS_TICK_MS = 100

const supported = props.recorderFactory ? true : SyncRecorder.supported()

/* -- members ------------------------------------------------------------------------ */

const session = computed(() => (sync.session && sync.session.id === sessionId.value ? sync.session : null))
const allMembers = computed(() => session.value?.members ?? sync.group?.members ?? [])
const adjustable = computed(() => allMembers.value.filter((m) => m.adjustable))
const notMeasured = computed(() => allMembers.value.filter((m) => !m.adjustable).map((m) => m.name))
/** Members that left the group: as the bridge says, and as a refused call just told us. */
const gone = ref<string[]>([])
const departed = computed(() => [...new Set([...(session.value?.departed ?? []), ...gone.value])])

/** The measuring order, editable; members joining later go to the end. */
const order = ref<string[]>([])
watch(
  () => adjustable.value.map((m) => m.ma_player_id),
  (ids) => {
    const kept = order.value.filter((id) => ids.includes(id))
    for (const id of ids) if (!kept.includes(id)) kept.push(id)
    order.value = kept
  },
  { immediate: true },
)
const ordered = computed(() =>
  order.value.map((id) => adjustable.value.find((m) => m.ma_player_id === id)!).filter(Boolean),
)
const nameOf = (id: string) => allMembers.value.find((m) => m.ma_player_id === id)?.name ?? id

const results = reactive<Record<string, SyncMeasurement>>({})
/** The recorder epoch each result was captured in. */
const epochOf: Record<string, number | undefined> = {}
const stale = ref<string[]>([])
const errors = reactive<Record<string, string>>({})
const active = ref<{ member: string; phase: MemberPhase } | null>(null)
/** Share of the running recording done, 0..1. */
const progress = ref(0)

function stopProgress() {
  if (progressTimer) clearInterval(progressTimer)
  progressTimer = null
}

function startProgress(durationS: number) {
  stopProgress()
  progress.value = 0
  let ticks = 0
  const total = (durationS * 1000) / PROGRESS_TICK_MS
  progressTimer = setInterval(() => {
    ticks++
    progress.value = Math.min(1, ticks / total)
  }, PROGRESS_TICK_MS)
}

const complete = computed(
  () =>
    ordered.value.length > 0 &&
    ordered.value.every((m) => {
      const id = m.ma_player_id
      if (departed.value.includes(id)) return true
      const r = results[id]
      return !!r && (r.status === 'ok' || r.status === 'skipped') && !stale.value.includes(id)
    }),
)

function measurement(member: string, status: SyncMeasurement['status'], reason = ''): SyncMeasurement {
  return { member, status, reason, confidence: 0, clicks: 0, phase_ms: null, spread_ms: null }
}

function resetMeasurements() {
  for (const id of Object.keys(results)) delete results[id]
  for (const id of Object.keys(errors)) delete errors[id]
  for (const id of Object.keys(epochOf)) delete epochOf[id]
  stale.value = []
}

/** Every recorded (not skipped) member must be recorded again. */
function markAllStale() {
  const ids = Object.keys(results).filter((id) => results[id]!.status !== 'skipped')
  stale.value = [...new Set([...stale.value, ...ids])]
}

/* -- announcements -------------------------------------------------------------------- */

const message = ref('')
const stepLine = computed(() => {
  const name = step.value === 'members' && verifying.value ? t('sync.wizard.verifying') : t(`sync.wizard.steps.${step.value}`)
  return t('sync.wizard.stepOf', { n: STEPS.indexOf(step.value) + 1, total: STEPS.length, name })
})
const heading = ref<HTMLElement | null>(null)
const root = ref<HTMLElement | null>(null)

async function goTo(next: Step) {
  step.value = next
  message.value = ''
  await nextTick()
  heading.value?.focus()
}

function resultText(id: string, r: SyncMeasurement) {
  const label =
    r.status === 'ok'
      ? `${t('sync.wizard.heard')} ${t('sync.wizard.confidence', { pct: Math.round(r.confidence * 100) })}`
      : t(`sync.wizard.${r.status === 'not_heard' ? 'notHeard' : r.status}`)
  return t('sync.wizard.result', { name: nameOf(id), result: label })
}

/* -- microphone ------------------------------------------------------------------------ */

const interrupted = ref(false)
const micLost = ref(false)
const reopenError = ref('')
const resumeError = ref('')

function closeRecorder() {
  for (const off of unsubscribers) off()
  unsubscribers = []
  if (settleTimer) clearTimeout(settleTimer)
  settleTimer = null
  const done = settleDone
  settleDone = null
  done?.()
  stopProgress()
  recorder?.close()
  recorder = null
  interrupted.value = false
  micLost.value = false
}

function interrupt() {
  if (interrupted.value) return
  interrupted.value = true
  markAllStale()
  message.value = t('sync.wizard.restartRound')
}

function lostMic() {
  if (micLost.value) return
  micLost.value = true
  markAllStale()
  message.value = t('sync.wizard.micLost')
}

function captureText(e: unknown) {
  return e instanceof SyncCaptureError ? t(e.messageKey) : t('sync.wizard.failed', { code: errorCode(e) })
}

/** "Tap to continue": resume the audio context from the tap. */
async function resumeTap() {
  const rec = recorder
  if (!rec) return
  resumeError.value = ''
  try {
    await rec.resume?.()
  } catch (e) {
    if (rec !== recorder) return
    if (e instanceof SyncCaptureError && e.code === 'not_open') return lostMic()
    resumeError.value = captureText(e)
    message.value = resumeError.value
    return
  }
  if (rec !== recorder) return
  if (!rec.epochInvalidated && !rec.needsResume) {
    interrupted.value = false
    message.value = ''
  }
}

/** Open the microphone again after it was lost (a new epoch: earlier members are redone). */
async function reopenTap() {
  const rec = recorder
  if (!rec) return
  reopenError.value = ''
  try {
    await rec.open()
  } catch (e) {
    reopenError.value = captureText(e)
    return
  }
  if (rec !== recorder) return
  micLost.value = false
  interrupted.value = false
  message.value = ''
  if (rec.epochInvalidated || rec.needsResume) interrupt()
}

/* -- steps ---------------------------------------------------------------------------- */

function attach(rec: SyncRecorder) {
  unsubscribers = [rec.onEpochInvalidated?.(() => interrupt()), rec.onMicLost?.(() => lostMic())].filter(
    (off): off is () => void => typeof off === 'function',
  )
}

/**
 * The microphone again, for a check round (or back from a failed proposal): opened right away (the
 * Apply click was the gesture); when the browser wants a tap, the reopen / "Tap to continue" banner asks.
 */
async function reopenRecorder() {
  if (recorder) return
  const rec = props.recorderFactory?.() ?? new SyncRecorder()
  recorder = rec
  attach(rec)
  try {
    await rec.open()
  } catch {
    if (rec === recorder) lostMic()
    return
  }
  if (rec !== recorder) return
  if (rec.epochInvalidated || rec.needsResume) interrupt()
}

/** Step 1 → 2: microphone first (still inside the tap), then the session, which starts the clicks. */
async function begin() {
  if (starting.value) return
  startError.value = ''
  ended.value = false
  starting.value = true
  const rec = props.recorderFactory?.() ?? new SyncRecorder()
  recorder = rec
  try {
    try {
      await rec.open()
    } catch (e) {
      startError.value = `${t('sync.wizard.micDenied')} ${captureText(e)}`
      closeRecorder()
      return
    }
    if (disposed || rec !== recorder) return
    let id: string
    try {
      id = (await sync.start(props.groupId, props.mode, point.value)).id
    } catch (e) {
      startError.value = t('sync.wizard.startFailed', { code: errorCode(e) })
      if (rec === recorder) closeRecorder()
      return
    }
    if (disposed) {
      void sync.end().catch(() => {})
      return
    }
    sessionId.value = id
    trackedId.value = id
    attach(rec)
    resetMeasurements()
    await goTo('level')
    // iOS may leave a fresh context suspended until a tap: offer it right away.
    if (rec.needsResume) interrupt()
  } finally {
    starting.value = false
  }
}

async function toMembers() {
  await goTo('members')
}

async function backToLevel() {
  await goTo('level')
  // Every speaker plays again for the level check.
  sync.solo(null).catch(() => {})
}

function move(member: string, by: -1 | 1) {
  const i = order.value.indexOf(member)
  const j = i + by
  if (i < 0 || j < 0 || j >= order.value.length) return
  const next = [...order.value]
  ;[next[i], next[j]] = [next[j]!, next[i]!]
  order.value = next
}

function setResult(member: string, r: SyncMeasurement, epoch: number | undefined) {
  results[member] = r
  epochOf[member] = epoch
  // Never one round from two clocks: a capture in a newer epoch makes the older captures redo.
  // A skip carries no capture, so it compares nothing.
  const others =
    epoch === undefined
      ? []
      : Object.keys(results).filter(
          (id) => id !== member && epochOf[id] !== undefined && epochOf[id] !== epoch,
        )
  stale.value = [...new Set([...stale.value.filter((id) => id !== member), ...others])]
  message.value = resultText(member, r)
}

/** Solo the member, let the mutes settle, record, upload. */
async function record(member: string) {
  const rec = recorder
  const sid = sessionId.value
  if (active.value || !rec || !sid) return
  errors[member] = ''
  if (rec.needsReopen) return lostMic()
  if (rec.epochInvalidated || rec.needsResume) return interrupt()
  active.value = { member, phase: 'soloing' }
  try {
    try {
      // Straight to the API (one hop less than the store): the settle time counts from the bridge's
      // answer, i.e. once the mutes are applied. The answer still goes through ``applyEvent``.
      const answer = await sessionSolo(sid, member)
      // A late answer must not revive a session that ended (or was left) meanwhile.
      if (disposed || rec !== recorder || sid !== sessionId.value) return
      sync.applyEvent(answer)
    } catch (e) {
      if (disposed || sid !== sessionId.value) return
      if (errorCode(e) === 'member_gone') return markGone(member, sid)
      errors[member] = t('sync.wizard.failed', { code: errorCode(e) })
      return
    }
    await new Promise<void>((resolve) => {
      settleDone = resolve
      settleTimer = setTimeout(() => {
        settleTimer = null
        settleDone = null
        resolve()
      }, SOLO_SETTLE_MS)
    })
    if (disposed || rec !== recorder || sid !== sessionId.value) return
    active.value = { member, phase: 'recording' }
    const durationS = point.value === 'device' ? 6 : 12
    let cap: Capture
    try {
      startProgress(durationS)
      cap = await rec.record(durationS)
    } catch (e) {
      if (rec !== recorder) return
      if (e instanceof SyncCaptureError && e.code === 'interrupted') interrupt()
      else if (e instanceof SyncCaptureError && e.code === 'mic_lost') lostMic()
      else errors[member] = captureText(e)
      return
    }
    if (disposed || sid !== sessionId.value) return
    let r: SyncMeasurement
    try {
      r = await uploadRecording(sid, member, cap.startTimeS, cap.sampleRate, cap.pcm)
    } catch (e) {
      if (!(e instanceof ApiError)) {
        errors[member] = t('sync.wizard.failed', { code: errorCode(e) })
        return
      }
      if (e.code === 'member_gone') return markGone(member, sid)
      r = measurement(member, 'not_heard', e.code)
    }
    if (disposed || sid !== sessionId.value) return
    setResult(member, r, cap.epoch)
  } finally {
    stopProgress()
    active.value = null
    if (!disposed) void focusRow(member)
  }
}

/**
 * The row's button was disabled while the step ran (which drops focus in browsers):
 * give keyboard focus back to the row's action, unless the operator moved elsewhere.
 */
async function focusRow(member: string) {
  const before = document.activeElement
  const inside = !before || before === document.body || !!root.value?.contains(before)
  if (!inside) return
  await nextTick()
  const el = root.value?.querySelector<HTMLElement>(
    `[data-repeat="${CSS.escape(member)}"], [data-record="${CSS.escape(member)}"]`,
  )
  el?.focus()
}

async function skip(member: string) {
  if (active.value || !sessionId.value) return
  errors[member] = ''
  try {
    await sync.skip(member)
  } catch (e) {
    if (errorCode(e) === 'member_gone') return markGone(member, sessionId.value)
    errors[member] = t('sync.wizard.failed', { code: errorCode(e) })
    return
  }
  setResult(member, measurement(member, 'skipped'), undefined)
  void focusRow(member)
}

/** The member left the group mid-session: its step is marked and the order goes on without it. */
function markGone(member: string, sid: string | null) {
  if (!gone.value.includes(member)) gone.value = [...gone.value, member]
  delete results[member]
  stale.value = stale.value.filter((id) => id !== member)
  errors[member] = t('sync.wizard.memberGone', { name: nameOf(member) })
  message.value = errors[member]!
  if (sid) void refresh(sid)
}

/* -- proposal, apply, undo, check round, done ------------------------------------------ */

const proposal = ref<SyncProposal | null>(null)
const proposing = ref(false)
const proposalError = ref('')
const applying = ref(false)
const applyError = ref('')
/** Music Assistant refused the write: the proposal stays, with the values to copy. */
const forbidden = ref(false)
/** The members step is a check round after an apply. */
const verifying = ref(false)
/** Something was written: a restore from now on ends in the done step, not "ended". */
const applied = ref(false)
/** The measuring session as last seen (it outlives ``sessionId`` in the done step). */
const lastSession = ref<SyncSession | null>(null)
/** The spread of the first round, before anything was applied. */
const firstSpread = ref<number | null>(null)
const undone = ref(false)
const undoing = ref(false)
const undoError = ref('')
const savedName = ref<string | null>(null)
const saveError = ref('')

/** The measuring session, for following its state to the end (``sessionId`` is cleared earlier). */
const trackedId = ref<string | null>(null)
/** Bumped when the flow starts over (cancel, measure again, the session ended under it). */
let flow = 0

const stale$ = (sid: string) => disposed || sid !== sessionId.value

/** Read the session again (control answers and events may lag); null when it is not ours any more. */
async function refresh(sid: string): Promise<SyncSession | null> {
  try {
    const s = await getSession(sid)
    if (stale$(sid)) return null
    sync.applyEvent(s)
    return s.id === sid ? s : null
  } catch {
    return session.value
  }
}

function lastHistory(s: SyncSession | null) {
  const h = s?.history ?? []
  return (h[h.length - 1] ?? null) as { spread_before_ms?: number; spread_after_ms?: number } | null
}

const proposalSession = computed<SyncSession | null>(() => {
  const s = session.value ?? lastSession.value
  if (!s) return null
  return { ...s, departed: departed.value, error: forbidden.value ? 'ma_forbidden' : s.error }
})

async function toProposal() {
  if (!complete.value) return
  // Nothing is recorded on the proposal: the microphone goes (a check round opens it again).
  closeRecorder()
  await goTo('proposal')
  message.value = t('sync.wizard.computing')
  emit('proposal')
  await compute()
}

/** POST the proposal (a GET may answer an earlier round's): it ends the round on the bridge. */
async function compute() {
  const sid = sessionId.value
  if (!sid || proposing.value) return
  const checkRound = verifying.value
  proposing.value = true
  proposalError.value = ''
  applyError.value = ''
  forbidden.value = false
  let p: SyncProposal
  try {
    p = await computeProposal(sid)
  } catch (e) {
    if (stale$(sid)) return
    const code = errorCode(e)
    // In a check round the bridge may have planned a refinement and failed to write it.
    const s = checkRound ? await refresh(sid) : null
    if (s && s.state === 'proposed' && s.proposal) {
      verifying.value = false
      proposal.value = s.proposal
      if (code === 'ma_forbidden') forbidden.value = true
      else applyError.value = t('sync.wizard.applyFailed', { code })
      message.value = forbidden.value ? t('sync.proposal.forbidden') : applyError.value
      return
    }
    proposalError.value = t('sync.wizard.proposalFailed', { code })
    message.value = proposalError.value
    return
  } finally {
    proposing.value = false
  }
  if (stale$(sid)) return
  const s = await refresh(sid)
  if (stale$(sid)) return
  proposal.value = p
  if (!checkRound) {
    firstSpread.value = lastHistory(s)?.spread_before_ms ?? firstSpread.value
    message.value = t('sync.state.proposed')
    if (props.mode === 'auto') await apply(undefined, undefined)
    return
  }
  if (s?.state === 'verifying') {
    // Automatic mode: the bridge applied a refinement and checks once more.
    await startCheckRound(t('sync.wizard.refined'))
    return
  }
  await toDone()
}

/** Write the proposal (``rows``: without members that left). Semi-automatic: ``verify`` asks for a check round. */
async function apply(rows: Record<string, number> | undefined, verify: boolean | undefined): Promise<boolean> {
  const sid = sessionId.value
  if (!sid || applying.value) return false
  const gen = flow
  // The bridge may report the end (done) before its answer: that is not a reason to drop the answer.
  const gone$ = () => disposed || gen !== flow
  applying.value = true
  applyError.value = ''
  message.value = t('sync.wizard.applying')
  try {
    const s = await applyProposal(sid, rows, verify)
    if (gone$()) return false
    forbidden.value = false
    applied.value = true
    sync.applyEvent(s)
    if (s.id === sid) lastSession.value = s
    trackUndo(s)
    if (s.state === 'verifying') await startCheckRound(t('sync.wizard.verifyingHelp'))
    else if (s.state !== 'proposed') await toDone()
    return true
  } catch (e) {
    if (gone$()) return false
    const code = errorCode(e)
    if (code === 'ma_forbidden') {
      forbidden.value = true
      message.value = t('sync.proposal.forbidden')
    } else {
      if (code === 'member_gone') await refresh(sid)
      applyError.value = t('sync.wizard.applyFailed', { code })
      message.value = applyError.value
    }
    return false
  } finally {
    applying.value = false
  }
}

/** Apply, then save the group's delays as a profile, noting the spread before → after. */
async function applyAndSave(name: string, rows: Record<string, number> | undefined, verify: boolean) {
  const before = lastHistory(session.value)?.spread_before_ms ?? firstSpread.value
  const after = proposal.value?.predicted_spread_ms
  const body: { name: string; kind: SessionPoint; notes?: string } = { name, kind: point.value }
  if (before != null && after != null) body.notes = `spread ${formatMs(before)} → ${formatMs(after)} ms`
  const gen = flow
  if (!(await apply(rows, props.mode === 'auto' ? undefined : verify))) return
  if (disposed || gen !== flow) return
  saveError.value = ''
  try {
    const created = await captureProfile(props.groupId, body)
    savedName.value = created?.name ?? name
    if (sync.group?.id === props.groupId && created) sync.profiles = [...sync.profiles, created]
  } catch (e) {
    saveError.value = t('sync.done.saveFailed', { code: errorCode(e) })
  }
}

/** A check round: every member is measured once more with the new delays. */
async function startCheckRound(note: string) {
  verifying.value = true
  resetMeasurements()
  for (const id of gone.value) errors[id] = t('sync.wizard.memberGone', { name: nameOf(id) })
  await goTo('members')
  message.value = note
  await reopenRecorder()
}

/** Step 5. The microphone goes now: nothing is recorded any more. */
async function toDone() {
  if (step.value === 'done') return
  verifying.value = false
  active.value = null
  closeRecorder()
  await goTo('done')
}

const doneState = computed(() => (session.value ?? lastSession.value)?.state ?? null)
const doneBefore = computed(() => firstSpread.value ?? lastHistory(lastSession.value)?.spread_before_ms ?? null)
const measuredAfter = computed(() => lastHistory(session.value ?? lastSession.value)?.spread_after_ms ?? null)
const doneAfter = computed(() => measuredAfter.value ?? proposal.value?.predicted_spread_ms ?? null)
const doneLine = computed(() => (doneState.value && ENDED.has(doneState.value) ? t('sync.done.restored') : t('sync.done.restoring')))

/* -- undo: the window is the bridge's (``undo_until_s``), counted down here ------------- */

const undoDeadline = ref<number | null>(null)
const undoLeft = ref(0)
let undoTimer: ReturnType<typeof setInterval> | null = null

function stopUndoTimer() {
  if (undoTimer) clearInterval(undoTimer)
  undoTimer = null
}

function tickUndo() {
  if (undoDeadline.value == null) return
  undoLeft.value = Math.max(0, Math.ceil((undoDeadline.value - Date.now()) / 1000 - 1e-6))
  if (undoLeft.value <= 0) stopUndoTimer()
}

function trackUndo(s: SyncSession) {
  if (s.undo_until_s == null) {
    if (undoDeadline.value != null) undoLeft.value = 0
    stopUndoTimer()
    return
  }
  undoDeadline.value = Date.now() + s.undo_until_s * 1000
  tickUndo()
  if (undoLeft.value > 0 && !undoTimer) undoTimer = setInterval(tickUndo, 1000)
}

async function undo() {
  const id = session.value?.id ?? lastSession.value?.id
  if (!id || undoing.value || undoLeft.value <= 0) return
  undoing.value = true
  undoError.value = ''
  try {
    const s = await undoApply(id)
    if (disposed) return
    // A recording or upload under way belongs to the undone round: drop it.
    sessionId.value = null
    sync.applyEvent(s)
    if (s.id === id) lastSession.value = s
    undone.value = true
    undoLeft.value = 0
    stopUndoTimer()
    await toDone()
    message.value = t('sync.done.undone')
    // Undone on the proposal (a refused refinement): the session is still live; end it so the group restores.
    if (!ENDED.has(s.state) && s.state !== 'restoring') {
      try {
        await endSession(id)
      } catch {
        /* the bridge times the session out */
      }
      void refreshTracked(id)
    }
  } catch (e) {
    undoError.value = t('sync.wizard.undoFailed', { code: errorCode(e) })
    message.value = undoError.value
  } finally {
    undoing.value = false
  }
}

async function refreshTracked(id: string) {
  try {
    const s = await getSession(id)
    if (!disposed && trackedId.value === id) {
      lastSession.value = s
      sync.applyEvent(s)
    }
  } catch {
    /* events follow anyway */
  }
}

/* -- quick listen: clicks on the whole group for 10 s, in a session of its own ---------- */

const listening = ref(false)
const listenError = ref('')
let listenId: string | null = null
let listenTimer: ReturnType<typeof setTimeout> | null = null

async function stopListen(id: string) {
  if (listenTimer) clearTimeout(listenTimer)
  listenTimer = null
  try {
    await sessionSignal(id, 'stop')
  } catch {
    /* the end below stops the signal too */
  }
  try {
    await endSession(id)
  } catch {
    /* the bridge times the session out */
  }
  if (listenId === id) listenId = null
  if (!disposed) listening.value = false
}

async function quickListen() {
  if (listening.value) return
  listening.value = true
  listenError.value = ''
  let id: string | null = null
  // Never the page's session: the header keeps the measurement's outcome, the Manual tab cannot take it.
  const release = sync.holdSession()
  try {
    id = (await createSession({ group_id: props.groupId, mode: 'manual' })).id
    sync.ignoreSession(id)
    release()
    listenId = id
    if (disposed) return void stopListen(id)
    await sessionSignal(id, 'start')
    if (disposed) return void stopListen(id)
    const started = id
    listenTimer = setTimeout(() => void stopListen(started), QUICK_LISTEN_MS)
  } catch (e) {
    release()
    listenError.value = t('sync.done.listenFailed', { code: errorCode(e) })
    if (id) await stopListen(id)
    listening.value = false
  }
}

function resetFlow() {
  flow++
  trackedId.value = null
  proposal.value = null
  proposalError.value = ''
  applyError.value = ''
  forbidden.value = false
  verifying.value = false
  applied.value = false
  lastSession.value = null
  firstSpread.value = null
  undone.value = false
  undoError.value = ''
  savedName.value = null
  saveError.value = ''
  gone.value = []
  undoDeadline.value = null
  undoLeft.value = 0
  stopUndoTimer()
}

function clearAll() {
  resetMeasurements()
  resetFlow()
  active.value = null
  reopenError.value = ''
}

/** Leave the done step: back to the start, ready for another measurement. */
async function again() {
  sessionId.value = null
  clearAll()
  listenError.value = ''
  await goTo('prepare')
  emit('done')
}

async function backToMembers() {
  proposalError.value = ''
  await goTo('members')
  await reopenRecorder()
}

async function cancel() {
  const id = sessionId.value
  sessionId.value = null
  closeRecorder()
  clearAll()
  await goTo('prepare')
  if (id && sync.session?.id === id && !ENDED.has(sync.session.state)) {
    try {
      await sync.end()
    } catch {
      /* kept in the store's error; the bridge times the session out */
    }
  }
  emit('done')
}

/* The session ended under the wizard (timeout, another tab, a failure): the microphone goes too. */
watch(
  () => sync.session,
  (s) => {
    if (s && trackedId.value && s.id === trackedId.value) {
      lastSession.value = s
      trackUndo(s)
    }
    const id = sessionId.value
    if (!id) return
    const ours = s && s.id === id ? s : null
    if (ours && !ENDED.has(ours.state)) {
      // After an apply, a restore is the end of the measurement, not an interruption.
      if (ours.state === 'restoring' && applied.value) void toDone()
      return
    }
    if (step.value === 'done' || ours?.state === 'done' || (ours && applied.value)) {
      // Finished: the session keeps its outcome (a finished one is never shown as ended or cancelled).
      sessionId.value = null
      void toDone()
      return
    }
    sessionId.value = null
    closeRecorder()
    clearAll()
    ended.value = true
    void goTo('prepare')
    message.value = t('sync.wizard.ended')
  },
)

function onPageHide() {
  if (sessionId.value) endSessionOnPageHide(sessionId.value)
  if (listenId) endSessionOnPageHide(listenId)
}
onMounted(() => window.addEventListener('pagehide', onPageHide))

onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('pagehide', onPageHide)
  const id = sessionId.value
  sessionId.value = null
  closeRecorder()
  stopUndoTimer()
  if (listenTimer) clearTimeout(listenTimer)
  listenTimer = null
  if (listenId) void endSession(listenId).catch(() => {})
  listenId = null
  if (id && sync.session?.id === id && !ENDED.has(sync.session.state)) void sync.end().catch(() => {})
})

const readLevel = () => recorder?.level() ?? 0
const signalStarting = computed(() => !session.value?.signal_running)
</script>

<template>
  <section ref="root" class="mt-4 flex flex-col gap-4">
    <h2 ref="heading" tabindex="-1" class="text-base font-semibold text-text-primary outline-none">{{ stepLine }}</h2>
    <p data-step-status role="status" aria-live="polite" class="sr-only">
      {{ stepLine }}<template v-if="step === 'done'">. {{ doneLine }}</template><template v-if="message">. {{ message }}</template>
    </p>

    <div
      v-if="undoDeadline != null && !undone && step !== 'prepare' && step !== 'level'"
      class="flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-border bg-surface-secondary px-4 py-3 text-sm text-text-primary"
    >
      <span class="min-w-0 flex-1">{{ t('sync.wizard.undoHelp') }}</span>
      <SbButton
        type="button"
        variant="outline"
        size="sm"
        data-undo
        :loading="undoing"
        :disabled="undoLeft <= 0"
        @click="undo"
      >
        {{ undoLeft > 0 ? t('sync.wizard.undo', { s: undoLeft }) : t('sync.wizard.undoClosed') }}
      </SbButton>
      <p v-if="undoError" role="alert" class="w-full text-error">{{ undoError }}</p>
    </div>

    <div
      v-if="ended && step === 'prepare'"
      data-ended
      class="rounded-(--radius-card) border border-border bg-surface-secondary px-4 py-3 text-sm text-text-primary"
    >
      {{ t('sync.wizard.ended') }}
    </div>

    <div
      v-if="micLost && step !== 'prepare'"
      class="flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
    >
      <span class="min-w-0 flex-1">{{ t('sync.capture.errors.mic_lost') }}</span>
      <SbButton type="button" variant="outline" size="sm" data-reopen @click="reopenTap">
        {{ t('sync.wizard.reopen') }}
      </SbButton>
      <p v-if="reopenError" role="alert" class="w-full text-error">{{ reopenError }}</p>
    </div>
    <div
      v-else-if="interrupted && step !== 'prepare'"
      class="flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      <span class="min-w-0 flex-1">{{ t('sync.wizard.restartRound') }}</span>
      <SbButton type="button" size="sm" data-resume @click="resumeTap">{{ t('sync.wizard.tapToContinue') }}</SbButton>
      <p v-if="resumeError" role="alert" class="w-full text-error">{{ resumeError }}</p>
    </div>

    <StepPrepare
      v-if="step === 'prepare'"
      v-model:point="point"
      :busy="starting"
      :error="startError"
      :supported="supported"
      @next="begin"
    />
    <StepLevel
      v-else-if="step === 'level'"
      :read="readLevel"
      :starting="signalStarting"
      :disabled="micLost"
      @next="toMembers"
      @cancel="cancel"
    />
    <StepMembers
      v-else-if="step === 'members'"
      :members="ordered"
      :point="point"
      :results="results"
      :stale="stale"
      :departed="departed"
      :active="active"
      :progress="progress"
      :errors="errors"
      :not-measured="notMeasured"
      :complete="complete"
      @record="record"
      @skip="skip"
      @move="move"
      @next="toProposal"
      @back="backToLevel"
      @cancel="cancel"
    />
    <template v-else-if="step === 'proposal'">
      <p v-if="proposing" role="status" class="flex items-center gap-2 text-sm text-text-secondary">
        <SbSpinner size="sm" />{{ t('sync.wizard.computing') }}
      </p>
      <div
        v-else-if="proposalError"
        data-proposal-error
        role="alert"
        class="flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
      >
        <span class="min-w-0 flex-1">{{ proposalError }}</span>
        <SbButton type="button" variant="outline" size="sm" data-cancel @click="cancel">{{ t('sync.wizard.cancel') }}</SbButton>
        <SbButton type="button" variant="outline" size="sm" data-back @click="backToMembers">{{ t('sync.wizard.back') }}</SbButton>
        <SbButton type="button" size="sm" data-retry-proposal @click="compute">{{ t('sync.wizard.retry') }}</SbButton>
      </div>
      <StepProposal
        v-else-if="proposal && proposalSession"
        :session="proposalSession"
        :proposal="proposal"
        :mode="mode"
        :busy="applying"
        :error="applyError"
        @apply="apply"
        @apply-and-save="applyAndSave"
        @cancel="cancel"
      />
    </template>
    <StepDone
      v-else-if="step === 'done'"
      :state="doneState"
      :before="doneBefore"
      :after="doneAfter"
      :predicted="measuredAfter == null"
      :undone="undone"
      :saved="savedName"
      :save-error="saveError"
      :listening="listening"
      :listen-error="listenError"
      @listen="quickListen"
      @again="again"
    />
  </section>
</template>
