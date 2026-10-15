<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { endSessionOnPageHide, sessionSolo, uploadRecording } from '@/api/sync'
import { ApiError } from '@/api/client'
import type { SessionPoint, SyncMeasurement } from '@/api/types'
import { ENDED, errorCode, useSyncStore } from '@/stores/sync'
import { SOLO_SETTLE_MS, SyncCaptureError, SyncRecorder, type Capture } from '@/sync/capture'
import { SbButton } from '@/kit'
import StepPrepare from './StepPrepare.vue'
import StepLevel from './StepLevel.vue'
import StepMembers, { type MemberPhase } from './StepMembers.vue'

/**
 * The measurement wizard, steps 1–3: preparation, level check, then speaker by speaker.
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
  /** The operator cancelled; the session is ended. */
  done: []
}>()

type Step = 'prepare' | 'level' | 'members' | 'proposal'
const STEPS: Step[] = ['prepare', 'level', 'members']

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
let unsubscribe: (() => void) | null = null
let disposed = false
let settleTimer: ReturnType<typeof setTimeout> | null = null

const supported = props.recorderFactory ? true : SyncRecorder.supported()

/* -- members ------------------------------------------------------------------------ */

const session = computed(() => (sync.session && sync.session.id === sessionId.value ? sync.session : null))
const allMembers = computed(() => session.value?.members ?? sync.group?.members ?? [])
const adjustable = computed(() => allMembers.value.filter((m) => m.adjustable))
const notMeasured = computed(() => allMembers.value.filter((m) => !m.adjustable).map((m) => m.name))
const departed = computed(() => session.value?.departed ?? [])

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
  if (step.value === 'proposal') return t('sync.wizard.steps.proposal')
  return t('sync.wizard.stepOf', {
    n: STEPS.indexOf(step.value) + 1,
    total: STEPS.length,
    name: t(`sync.wizard.steps.${step.value}`),
  })
})
const heading = ref<HTMLElement | null>(null)

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

function closeRecorder() {
  unsubscribe?.()
  unsubscribe = null
  if (settleTimer) clearTimeout(settleTimer)
  settleTimer = null
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
  try {
    await rec.resume()
  } catch (e) {
    if (e instanceof SyncCaptureError && e.code === 'not_open') return lostMic()
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
    unsubscribe = rec.onEpochInvalidated?.(() => interrupt()) ?? null
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
  const others = Object.keys(results).filter(
    (id) => id !== member && results[id]!.status !== 'skipped' && epochOf[id] !== epoch,
  )
  // Never one round from two clocks: whatever was captured in another epoch is redone.
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
      sync.applyEvent(await sessionSolo(sid, member))
    } catch (e) {
      errors[member] = t('sync.wizard.failed', { code: errorCode(e) })
      return
    }
    await new Promise<void>((resolve) => {
      settleTimer = setTimeout(() => {
        settleTimer = null
        resolve()
      }, SOLO_SETTLE_MS)
    })
    if (disposed || rec !== recorder || sid !== sessionId.value) return
    active.value = { member, phase: 'recording' }
    let cap: Capture
    try {
      cap = await rec.record(point.value === 'device' ? 6 : 12)
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
      r = measurement(member, 'not_heard', e.code)
    }
    if (disposed || sid !== sessionId.value) return
    setResult(member, r, cap.epoch)
  } finally {
    active.value = null
  }
}

async function skip(member: string) {
  if (active.value || !sessionId.value) return
  errors[member] = ''
  try {
    await sync.skip(member)
  } catch (e) {
    errors[member] = t('sync.wizard.failed', { code: errorCode(e) })
    return
  }
  setResult(member, measurement(member, 'skipped'), undefined)
}

async function toProposal() {
  if (!complete.value) return
  await goTo('proposal')
  message.value = t('sync.wizard.measured')
  emit('proposal')
}

function clearAll() {
  resetMeasurements()
  active.value = null
  reopenError.value = ''
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
    const id = sessionId.value
    if (!id) return
    if (s && s.id === id && !ENDED.has(s.state)) return
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
}
onMounted(() => window.addEventListener('pagehide', onPageHide))

onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('pagehide', onPageHide)
  const id = sessionId.value
  sessionId.value = null
  closeRecorder()
  if (id && sync.session?.id === id && !ENDED.has(sync.session.state)) void sync.end().catch(() => {})
})

const readLevel = () => recorder?.level() ?? 0
const signalStarting = computed(() => !session.value?.signal_running)
</script>

<template>
  <section class="mt-4 flex flex-col gap-4">
    <h2 ref="heading" tabindex="-1" class="text-base font-semibold text-text-primary outline-none">{{ stepLine }}</h2>
    <p data-step-status role="status" aria-live="polite" class="sr-only">
      {{ stepLine }}<template v-if="message">. {{ message }}</template>
    </p>

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
    <p v-else class="text-sm text-text-secondary" data-measured>{{ t('sync.wizard.measured') }}</p>
  </section>
</template>
