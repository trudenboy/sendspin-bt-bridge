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
  finishSession,
  getSession,
  sessionSignal,
  sessionSolo,
  uploadRecording,
} from '@/api/sync'
import { ApiError } from '@/api/client'
import type { SessionPoint, SyncMeasurement, SyncProposal, SyncSession } from '@/api/types'
import { ENDED, errorCode, errorMember, useSyncStore } from '@/stores/sync'
import { SOLO_SETTLE_MS, SyncCaptureError, SyncRecorder, type Capture } from '@/sync/capture'
import { SbButton, SbSpinner } from '@/kit'
import { confirmDialog } from '@/composables/useConfirm'
import { formatMs } from '@/sync/format'
import { speakerName } from '@/sync/names'
import { useCountdown } from '@/sync/countdown'
import StepPrepare from './StepPrepare.vue'
import StepStopGroup from './StepStopGroup.vue'
import StepClicker from './StepClicker.vue'
import StepVolume from './StepVolume.vue'
import StepMembers, { type MemberPhase } from './StepMembers.vue'
import StepTune from './StepTune.vue'
import PhoneCapture from './PhoneCapture.vue'
import StepDone from './StepDone.vue'

/**
 * The measurement wizard, in four stages. Prepare: where to measure and the microphone, stop the
 * group's music, start the clicks, set the volumes (with the microphone's level). Measure: speaker by
 * speaker. Tune: the measured delays are applied and the session becomes the manual editor (listen,
 * adjust, finish). Done: the group restored.
 *
 * The wizard owns the microphone: it is opened from the first "Next" (a user gesture) and
 * closed on cancel, on unmount and when the session ends. Captures share one clock only
 * within one recorder epoch; when the epoch breaks (the audio context stopped, or the
 * microphone was reopened) every member recorded before must be recorded again, so one
 * round never mixes clocks.
 */
const props = defineProps<{
  groupId: string
  /** Speakers the operator chose to skip before starting (e.g. unavailable ones): skipped once it runs. */
  preSkip?: string[]
  /** For tests: the recorder to use instead of the browser's microphone. */
  recorderFactory?: () => SyncRecorder
}>()
const emit = defineEmits<{
  /** The operator cancelled (the session is ended), or left the done step. */
  done: []
  /** The session this wizard holds (null when it holds none): the page must not unmount it meanwhile. */
  session: [id: string | null]
  /** The stage the wizard is in while it holds a session (the page's status line); null otherwise. */
  stage: [stage: Stage | null]
  /** The operator would rather tune by ear (no microphone, or a speaker that cannot be measured). */
  manual: []
  /** The operator toggled skipping a speaker on step 1. */
  skip: [member: string, skip: boolean]
}>()

type Step = 'prepare' | 'stop' | 'clicker' | 'volume' | 'members' | 'tune' | 'done'
export type Stage = 'prepare' | 'measure' | 'tune' | 'done'
const STAGE_OF: Record<Step, Stage> = {
  prepare: 'prepare',
  stop: 'prepare',
  clicker: 'prepare',
  volume: 'prepare',
  members: 'measure',
  tune: 'tune',
  done: 'done',
}
const STAGES: Stage[] = ['prepare', 'measure', 'tune', 'done']
const QUICK_LISTEN_MS = 10_000

const { t } = useI18n()
const sync = useSyncStore()

const step = ref<Step>('prepare')
const point = ref<SessionPoint>('device')
/** Record here (this device's microphone) or with a phone through the capture page. */
const capture = ref<'device' | 'phone'>('device')
const starting = ref(false)
const startError = ref('')
/** The session this wizard started; null before the start and after it ended. */
const sessionId = ref<string | null>(null)
const ended = ref(false)
watch(sessionId, (id) => emit('session', id))

let recorder: SyncRecorder | null = null
let unsubscribers: (() => void)[] = []
let disposed = false
let settleTimer: ReturnType<typeof setTimeout> | null = null
/** Ends the settle wait early (on close), so the waiting step settles and exits. */
let settleDone: (() => void) | null = null
let progressTimer: ReturnType<typeof setInterval> | null = null
const PROGRESS_TICK_MS = 100

const supported = props.recorderFactory ? true : SyncRecorder.supported()
// No microphone here (plain HTTP, an old browser): a phone is the way to measure.
if (!supported) capture.value = 'phone'

/* -- members ------------------------------------------------------------------------ */

const session = computed(() => (sync.session && sync.session.id === sessionId.value ? sync.session : null))
const allMembers = computed(() => session.value?.members ?? sync.group?.members ?? [])
const adjustable = computed(() => allMembers.value.filter((m) => m.adjustable))
const notMeasured = computed(() => allMembers.value.filter((m) => !m.adjustable).map((m) => m.name))
/*
 * The clicker (the click track queued on the group): recordings only while it plays; when it stopped
 * (the track ended, or someone stopped the group) it is offered again.
 */
const clickerPlaying = computed(() => !!session.value?.clicker_playing)
const clickerStarted = computed(() => (session.value?.clicker_starts ?? 0) > 0)
/** The click track's time left, counted down here between the bridge's readings. */
const leftS = useCountdown(() => session.value?.clicker_left_s ?? null)

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
const nameOf = (id: string) => speakerName(allMembers.value.find((m) => m.ma_player_id === id)?.name, id)

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
      if (capture.value === 'phone') {
        const s = session.value?.measurements?.[id]?.status
        return s === 'ok' || s === 'skipped'
      }
      const r = results[id]
      return !!r && (r.status === 'ok' || r.status === 'skipped') && !stale.value.includes(id)
    }),
)
const phoneMembers = computed(() => ordered.value.filter((m) => !departed.value.includes(m.ma_player_id)))

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
const stage = computed(() => STAGE_OF[step.value])
const stepLine = computed(() =>
  t('sync.wizard.stageOf', {
    stage: t(`sync.wizard.stages.${stage.value}`),
    n: STAGES.indexOf(stage.value) + 1,
    total: STAGES.length,
    name: t(`sync.wizard.steps.${step.value}`),
  }),
)
watch(
  () => (sessionId.value ? stage.value : null),
  (now) => emit('stage', now),
  { immediate: true },
)
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
  // With a phone, nothing is recorded here: no microphone to open.
  const rec = capture.value === 'device' ? (props.recorderFactory?.() ?? new SyncRecorder()) : null
  recorder = rec
  try {
    if (rec) {
      try {
        await rec.open()
      } catch (e) {
        startError.value = `${t('sync.wizard.micDenied')} ${captureText(e)}`
        closeRecorder()
        return
      }
      if (disposed || rec !== recorder) return
    }
    let id: string
    try {
      id = (await sync.start(props.groupId, 'semi', point.value)).id
    } catch (e) {
      startError.value = t('sync.wizard.startFailed', { code: errorCode(e) })
      if (rec && rec === recorder) closeRecorder()
      return
    }
    if (disposed) {
      void sync.end().catch(() => {})
      return
    }
    sessionId.value = id
    trackedId.value = id
    notice.value = ''
    setActiveFlag()
    if (rec) attach(rec)
    resetMeasurements()
    // The speakers the operator chose to skip on step 1 (unavailable, silent, muted).
    for (const member of props.preSkip ?? []) {
      if (!adjustable.value.some((m) => m.ma_player_id === member)) continue
      try {
        await sync.skip(member)
        setResult(member, measurement(member, 'skipped'), undefined)
      } catch {
        /* still offered on the members step */
      }
    }
    await goTo('stop')
    if (session.value && session.value.group_state !== 'playing') {
      // Nothing to stop: one tap fewer.
      await goTo('clicker')
      message.value = t('sync.prep.stop.notPlaying')
      autoNote.value = message.value
    }
    // iOS may leave a fresh context suspended until a tap: offer it right away.
    if (rec?.needsResume) interrupt()
  } finally {
    starting.value = false
  }
}

/* -- getting the group ready: stop it, start the clicker, set the volumes ---------------------- */

const prepBusy = ref(false)
/** Said on the clicks step when the stop step was skipped because nothing played. */
const autoNote = ref('')
const prepError = ref('')

function prepText(e: unknown) {
  const code = errorCode(e)
  return code === 'group_playing' ? t('sync.prep.clicker.groupPlaying') : t('sync.prep.failed', { code })
}

async function prepCall(call: () => Promise<unknown>) {
  if (prepBusy.value) return
  prepBusy.value = true
  prepError.value = ''
  try {
    await call()
  } catch (e) {
    if (!disposed) prepError.value = prepText(e)
  } finally {
    prepBusy.value = false
  }
}

const stopGroup = () => prepCall(() => sync.signal('stop'))
const startClicker = () => prepCall(() => sync.signal('start'))

async function setVolume(member: string, level: number) {
  prepError.value = ''
  try {
    await sync.volume(member, level)
  } catch (e) {
    if (!disposed) prepError.value = prepText(e)
  }
}

async function toStep(next: Step) {
  prepError.value = ''
  autoNote.value = ''
  await goTo(next)
}

async function toMembers() {
  await toStep('members')
}

async function backToVolume() {
  await toStep('volume')
  // Every speaker plays again while the volumes are set.
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
  if (active.value || !rec || !sid || !clickerPlaying.value) return
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
      errors[member] = failureText(e, member)
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
      if (e.code === 'clicker_stopped') {
        errors[member] = failureText(e, member)
        return
      }
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

/** A failed solo or upload, said for the operator: a member that cannot be muted is named. */
function failureText(e: unknown, member: string) {
  const code = errorCode(e)
  if (code === 'sync_mute_unsupported') return t('sync.mute.unsupported', { name: nameOf(errorMember(e) ?? member) })
  if (code === 'clicker_stopped') return t('sync.prep.clicker.stopped')
  return t('sync.wizard.failed', { code })
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

/** The clicker stopped mid-round: start it again (the bridge starts the round again with it). */
const restarting = ref(false)
async function restartClicker() {
  const sid = sessionId.value
  if (!sid || active.value || restarting.value) return
  restarting.value = true
  try {
    const answer = await sessionSignal(sid, 'start')
    if (disposed || sid !== sessionId.value) return
    sync.applyEvent(answer)
  } catch (e) {
    if (!disposed) message.value = prepText(e)
  } finally {
    restarting.value = false
  }
}

/* A new clicker start is a new playback: phases from the old one do not compare, so every member is redone. */
watch(
  () => session.value?.clicker_starts,
  (clip, prev) => {
    if (clip == null || prev == null || clip <= prev || Object.keys(results).length === 0) return
    markAllStale()
    message.value = t('sync.signal.restarted')
  },
)

/* -- tune: the measured delays applied, then the manual editor; finish; done ----------------- */

const proposal = ref<SyncProposal | null>(null)
const proposing = ref(false)
const proposalError = ref('')
const applying = ref(false)
const applyError = ref('')
/** Music Assistant refused the write: the measured values are offered to copy instead. */
const forbidden = ref(false)
/** The measured delays were written: the editor is live. */
const applied = ref(false)
const finishing = ref(false)
const finishError = ref('')
/** The measuring session as last seen (it outlives ``sessionId`` in the done step). */
const lastSession = ref<SyncSession | null>(null)
/** The spread of the measurement, before anything was applied. */
const firstSpread = ref<number | null>(null)
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
  return (h[h.length - 1] ?? null) as { spread_before_ms?: number } | null
}

/** Members that left the group are left out of the write (Music Assistant would refuse them). */
function applyRows(p: SyncProposal): Record<string, number> | undefined {
  const live = p.rows.filter((r) => !departed.value.includes(r.member))
  return live.length === p.rows.length ? undefined : Object.fromEntries(live.map((r) => [r.member, r.after_ms]))
}

const copyText = computed(() =>
  (proposal.value?.rows ?? [])
    .filter((r) => !departed.value.includes(r.member))
    .map((r) => `${nameOf(r.member)}: ${r.after_ms} ms`)
    .join('\n'),
)

async function toTune() {
  if (!complete.value) return
  // Nothing more is recorded: the microphone goes.
  closeRecorder()
  await goTo('tune')
  await computeAndApply()
}

/** POST the proposal, then write it: the session becomes the manual editor with the clicks still playing. */
async function computeAndApply() {
  const sid = sessionId.value
  if (!sid || proposing.value || applying.value) return
  proposalError.value = ''
  applyError.value = ''
  forbidden.value = false
  let p: SyncProposal
  if (!proposal.value || session.value?.state !== 'proposed') {
    proposing.value = true
    message.value = t('sync.wizard.computing')
    try {
      p = await computeProposal(sid)
    } catch (e) {
      if (stale$(sid)) return
      proposalError.value = t('sync.wizard.proposalFailed', { code: errorCode(e) })
      message.value = proposalError.value
      return
    } finally {
      proposing.value = false
    }
    if (stale$(sid)) return
    const s = await refresh(sid)
    if (stale$(sid)) return
    proposal.value = p
    firstSpread.value = lastHistory(s)?.spread_before_ms ?? firstSpread.value
  } else {
    p = proposal.value
  }
  const gen = flow
  applying.value = true
  message.value = t('sync.wizard.applying')
  try {
    const s = await applyProposal(sid, applyRows(p))
    if (disposed || gen !== flow) return
    applied.value = true
    sync.applyEvent(s)
    if (s.id === sid) lastSession.value = s
    message.value = t('sync.tune.ready')
  } catch (e) {
    if (disposed || gen !== flow) return
    const code = errorCode(e)
    if (code === 'ma_forbidden') forbidden.value = true
    else {
      if (code === 'member_gone') await refresh(sid)
      applyError.value = t('sync.wizard.applyFailed', { code })
    }
    message.value = forbidden.value ? t('sync.proposal.forbidden') : applyError.value
  } finally {
    applying.value = false
  }
}

const copyState = ref<'' | 'copied' | 'failed'>('')
async function copyValues() {
  try {
    await navigator.clipboard.writeText(copyText.value)
    copyState.value = 'copied'
  } catch {
    copyState.value = 'failed'
  }
}

/** Finish from the editor: the last edits are written, a profile saved if asked, the session ended "done". */
async function finish(profileName: string | null) {
  const sid = sessionId.value
  if (!sid || finishing.value) return
  const gen = flow
  finishing.value = true
  finishError.value = ''
  saveError.value = ''
  try {
    await sync.flushDelays()
    if (profileName) {
      const before = firstSpread.value
      const after = proposal.value?.predicted_spread_ms
      const body: { name: string; kind: SessionPoint; notes?: string } = { name: profileName, kind: point.value }
      if (before != null && after != null) body.notes = `spread ${formatMs(before)} → ${formatMs(after)} ms`
      try {
        const created = await captureProfile(props.groupId, body)
        savedName.value = created?.name ?? profileName
        if (sync.group?.id === props.groupId && created) sync.profiles = [...sync.profiles, created]
      } catch (e) {
        saveError.value = t('sync.done.saveFailed', { code: errorCode(e) })
      }
    }
    if (disposed || gen !== flow) return
    try {
      const s = await finishSession(sid)
      if (disposed || gen !== flow) return
      sync.applyEvent(s)
      if (s.id === sid) lastSession.value = s
    } catch (e) {
      finishError.value = t('sync.wizard.failed', { code: errorCode(e) })
      return
    }
    sessionId.value = null
    clearActiveFlag()
    await toDone()
  } finally {
    finishing.value = false
  }
}

/** Cancel from the editor: put back the delays from before the measurement, then end. */
async function putBackDelays() {
  const s = session.value
  const p = proposal.value
  if (!s || !p || !applied.value) return
  for (const r of p.rows) {
    if (departed.value.includes(r.member)) continue
    const now = s.members.find((m) => m.ma_player_id === r.member)?.delay_ms
    if (now !== r.before_ms) sync.setDelay(r.member, r.before_ms)
  }
  await sync.flushDelays()
}

/** Stage 4. The microphone goes now: nothing is recorded any more. */
async function toDone() {
  if (step.value === 'done') return
  active.value = null
  closeRecorder()
  await goTo('done')
}

const doneState = computed(() => (session.value ?? lastSession.value)?.state ?? null)
const doneBefore = computed(() => firstSpread.value ?? lastHistory(lastSession.value)?.spread_before_ms ?? null)
const doneAfter = computed(() => proposal.value?.predicted_spread_ms ?? null)
const doneLine = computed(() => (doneState.value && ENDED.has(doneState.value) ? t('sync.done.restored') : t('sync.done.restoring')))

/* -- quick listen: clicks on the whole group for 10 s, in a session of its own ---------- */

const listening = ref(false)
const listenError = ref('')
let listenId: string | null = null
let listenTimer: ReturnType<typeof setTimeout> | null = null

async function stopListen(id: string) {
  if (listenTimer) clearTimeout(listenTimer)
  listenTimer = null
  // No explicit stop: ending the session stops the group only while it plays our clicker, so
  // music someone started meanwhile (or that refused the quick listen) plays on.
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
  applied.value = false
  finishError.value = ''
  lastSession.value = null
  firstSpread.value = null
  savedName.value = null
  saveError.value = ''
  prepError.value = ''
  gone.value = []
  copyState.value = ''
}

function clearAll() {
  resetMeasurements()
  resetFlow()
  active.value = null
  reopenError.value = ''
}

/* -- telling the page a measurement runs, so a reload can say how it ended ------------- */

const ACTIVE_KEY = 'sync:active'
function setActiveFlag() {
  try {
    sessionStorage.setItem(ACTIVE_KEY, JSON.stringify({ group: props.groupId, kind: 'measure' }))
  } catch {
    /* private mode: no notice after a reload */
  }
}
function clearActiveFlag() {
  try {
    sessionStorage.removeItem(ACTIVE_KEY)
  } catch {
    /* nothing stored */
  }
}

/** Leave the done step: back to the start, ready for another measurement. */
async function again() {
  sessionId.value = null
  clearAll()
  listenError.value = ''
  notice.value = ''
  await goTo('prepare')
  emit('done')
}

async function backToMembers() {
  proposalError.value = ''
  await goTo('members')
  if (capture.value === 'device') await reopenRecorder()
}

/** What the end of a measurement did, said on step 1 (Cancel, or the session ended under it). */
const notice = ref('')

/**
 * Cancel: asks first once something would be lost (recordings, or the applied delays, which go back),
 * then ends the session (the bridge unmutes, puts the volumes back and stops the clicks).
 */
async function cancel() {
  const id = sessionId.value
  const recorded =
    Object.values(results).some((r) => r.status !== 'skipped') ||
    Object.values(session.value?.measurements ?? {}).some((r) => r.status !== 'skipped')
  if (applied.value || recorded) {
    const ok = await confirmDialog({
      title: t(applied.value ? 'sync.cancel.tuneTitle' : 'sync.cancel.title'),
      message: t(applied.value ? 'sync.cancel.tuneMessage' : 'sync.cancel.message'),
      confirmLabel: t('sync.cancel.confirm'),
      cancelLabel: t('sync.cancel.keep'),
      danger: true,
    })
    if (!ok || id !== sessionId.value) return
  }
  if (applied.value) {
    finishing.value = true
    try {
      await putBackDelays()
    } finally {
      finishing.value = false
    }
  }
  sessionId.value = null
  closeRecorder()
  clearAll()
  clearActiveFlag()
  await goTo('prepare')
  if (id && sync.session?.id === id && !ENDED.has(sync.session.state)) {
    try {
      await sync.end()
    } catch {
      /* kept in the store's error; the bridge times the session out */
    }
  }
  notice.value = t('sync.cancel.done')
  message.value = notice.value
  emit('done')
}

/* The session ended under the wizard (timeout, another tab, a failure): the microphone goes too. */
watch(
  () => sync.session,
  (s) => {
    if (s && trackedId.value && s.id === trackedId.value) lastSession.value = s
    const id = sessionId.value
    if (!id) return
    const ours = s && s.id === id ? s : null
    if (ours && !ENDED.has(ours.state)) {
      // Finished from elsewhere after the apply: the end of the measurement, not an interruption.
      if (ours.state === 'restoring' && applied.value) {
        sessionId.value = null
        clearActiveFlag()
        void toDone()
      }
      return
    }
    if (step.value === 'done' || ours?.state === 'done') {
      // Finished: the session keeps its outcome (a finished one is never shown as ended or cancelled).
      sessionId.value = null
      clearActiveFlag()
      void toDone()
      return
    }
    sessionId.value = null
    closeRecorder()
    clearAll()
    clearActiveFlag()
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
  if (listenTimer) clearTimeout(listenTimer)
  listenTimer = null
  if (listenId) void endSession(listenId).catch(() => {})
  listenId = null
  if (id && sync.session?.id === id && !ENDED.has(sync.session.state)) {
    clearActiveFlag()
    void sync.end().catch(() => {})
  }
})

const readLevel = () => recorder?.level() ?? 0
const volumes = computed(() => session.value?.volumes ?? {})
</script>

<template>
  <section ref="root" class="mt-4 flex flex-col gap-4">
    <h2 ref="heading" tabindex="-1" class="text-base font-semibold text-text-primary outline-none">{{ stepLine }}</h2>
    <p data-step-status role="status" aria-live="polite" class="sr-only">
      {{ stepLine }}<template v-if="step === 'done'">. {{ doneLine }}</template><template v-if="message">. {{ message }}</template>
    </p>

    <div
      v-if="ended && step === 'prepare'"
      data-ended
      class="rounded-(--radius-card) border border-border bg-surface-secondary px-4 py-3 text-sm text-text-primary"
    >
      {{ t('sync.wizard.ended') }}
    </div>
    <div
      v-else-if="notice && step === 'prepare'"
      data-notice
      role="status"
      class="rounded-(--radius-card) border border-border bg-surface-secondary px-4 py-3 text-sm text-text-primary"
    >
      {{ notice }}
    </div>

    <div
      v-if="micLost && step !== 'prepare' && step !== 'tune' && step !== 'done'"
      class="flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
    >
      <span class="min-w-0 flex-1">{{ t('sync.capture.errors.mic_lost') }}</span>
      <SbButton type="button" variant="outline" size="sm" data-reopen @click="reopenTap">
        {{ t('sync.wizard.reopen') }}
      </SbButton>
      <p v-if="reopenError" role="alert" class="w-full text-error">{{ reopenError }}</p>
    </div>
    <div
      v-else-if="interrupted && step !== 'prepare' && step !== 'tune' && step !== 'done'"
      class="flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      <span class="min-w-0 flex-1">{{ t('sync.wizard.restartRound') }}</span>
      <SbButton type="button" size="sm" data-resume @click="resumeTap">{{ t('sync.wizard.tapToContinue') }}</SbButton>
      <p v-if="resumeError" role="alert" class="w-full text-error">{{ resumeError }}</p>
    </div>

    <StepPrepare
      v-if="step === 'prepare'"
      v-model:point="point"
      v-model:capture="capture"
      :busy="starting"
      :error="startError"
      :supported="supported"
      :count="adjustable.length - (preSkip?.length ?? 0)"
      :members="allMembers"
      :skipped="preSkip"
      @skip="(id, on) => emit('skip', id, on)"
      @next="begin"
      @manual="emit('manual')"
    />
    <StepStopGroup
      v-else-if="step === 'stop'"
      :group-state="session?.group_state ?? null"
      :clicker-playing="clickerPlaying"
      :busy="prepBusy"
      :error="prepError"
      @stop="stopGroup"
      @next="toStep('clicker')"
      @cancel="cancel"
    />
    <StepClicker
      v-else-if="step === 'clicker'"
      :clicker-playing="clickerPlaying"
      :started="clickerStarted"
      :left-s="leftS"
      :note="autoNote"
      :busy="prepBusy"
      :error="prepError"
      @start="startClicker"
      @next="toStep('volume')"
      @back="toStep('stop')"
      @cancel="cancel"
    />
    <StepVolume
      v-else-if="step === 'volume'"
      :members="allMembers"
      :volumes="volumes"
      :read="capture === 'device' ? readLevel : undefined"
      :disabled="micLost"
      :error="prepError"
      @volume="setVolume"
      @next="toMembers"
      @back="toStep('clicker')"
      @cancel="cancel"
    />
    <PhoneCapture
      v-else-if="step === 'members' && capture === 'phone' && session"
      :session="session"
      :members="phoneMembers"
      :clicker-left-s="leftS"
      @restart="restartClicker"
      @next="toTune"
      @back="backToVolume"
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
      :clicker-playing="clickerPlaying"
      :clicker-left-s="leftS"
      :restarting="restarting"
      @restart="restartClicker"
      @record="record"
      @skip="skip"
      @move="move"
      @next="toTune"
      @back="backToVolume"
      @cancel="cancel"
    />
    <template v-else-if="step === 'tune'">
      <p v-if="proposing || applying" role="status" class="flex items-center gap-2 text-sm text-text-secondary">
        <SbSpinner size="sm" />{{ proposing ? t('sync.wizard.computing') : t('sync.wizard.applying') }}
      </p>
      <div
        v-else-if="proposalError || applyError || forbidden"
        data-proposal-error
        role="alert"
        class="flex flex-col gap-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
      >
        <p>{{ forbidden ? t('sync.proposal.forbidden') : proposalError || applyError }}</p>
        <template v-if="forbidden">
          <pre data-copy-text class="whitespace-pre-wrap rounded bg-surface-card px-3 py-2 font-mono text-sm select-all">{{ copyText }}</pre>
          <div class="flex flex-wrap items-center gap-3">
            <SbButton type="button" size="sm" variant="outline" data-copy-values @click="copyValues">
              {{ t('sync.proposal.copy') }}
            </SbButton>
            <span v-if="copyState" role="status" class="text-text-secondary">
              {{ copyState === 'copied' ? t('sync.proposal.copied') : t('sync.proposal.copyFailed') }}
            </span>
          </div>
          <p class="text-text-secondary">{{ t('sync.proposal.forbiddenHint') }}</p>
        </template>
        <div class="flex flex-wrap justify-end gap-3">
          <SbButton type="button" variant="outline" size="sm" data-cancel @click="cancel">{{ t('sync.wizard.cancel') }}</SbButton>
          <SbButton type="button" variant="outline" size="sm" data-back @click="backToMembers">{{ t('sync.wizard.back') }}</SbButton>
          <SbButton type="button" size="sm" data-retry-proposal @click="computeAndApply">{{ t('sync.wizard.retry') }}</SbButton>
        </div>
      </div>
      <StepTune
        v-else-if="applied && proposal && session"
        :session="session"
        :proposal="proposal"
        :busy="finishing"
        :error="finishError || saveError"
        @finish="finish"
        @cancel="cancel"
      />
    </template>
    <StepDone
      v-else-if="step === 'done'"
      :state="doneState"
      :before="doneBefore"
      :after="doneAfter"
      :saved="savedName"
      :save-error="saveError"
      :listening="listening"
      :listen-error="listenError"
      @listen="quickListen"
      @again="again"
    />
  </section>
</template>
