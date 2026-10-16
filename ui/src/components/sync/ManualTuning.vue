<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RotateCcw } from 'lucide-vue-next'
import { ENDED, errorCode, errorMember, useSyncStore } from '@/stores/sync'
import { SbBadge, SbButton, SbCard } from '@/kit'
import DelayRow from './DelayRow.vue'
import ListeningBar from './ListeningBar.vue'
import SoundPicker from './SoundPicker.vue'
import type { SyncSound } from '@/api/types'
import { speakerName } from '@/sync/names'
import { useCountdown } from '@/sync/countdown'
import { formatClock } from '@/sync/format'
import StepStopGroup from './StepStopGroup.vue'
import StepClicker from './StepClicker.vue'
import StepVolume from './StepVolume.vue'

const props = defineProps<{
  /** The group is ready already (the measurement prepared it): straight to listening and editing. */
  skipPrep?: boolean
  /** Member id -> the delay the measurement found (semi-automatic): shown next to the field, with a reset. */
  measured?: Record<string, number>
  /** Member id -> the delay before this session changed anything: "Reset" goes back to it. */
  before?: Record<string, number>
}>()

const emit = defineEmits<{
  /** Whether any delay differs from where this session started (the view offers a profile on leaving). */
  changed: [boolean]
}>()

const { t } = useI18n()
const sync = useSyncStore()

/** A/B: each member plays for two clicks (P = 2 s) before the other takes over. */
const AB_MS = 4000

const session = computed(() => sync.session)
const departed = computed(() => new Set(session.value?.departed ?? []))
const members = computed(() => session.value?.members ?? [])
const present = computed(() => members.value.filter((m) => !departed.value.has(m.ma_player_id)))
const nameOf = (id: string) => speakerName(members.value.find((m) => m.ma_player_id === id)?.name, id)

/* -- delays ------------------------------------------------------------------------ */

/** What is on screen (and written), and where the session started; seeded once per member. */
const values = reactive<Record<string, number>>({})
const initial: Record<string, number> = {}

watch(
  members,
  (list) => {
    for (const m of list) {
      if (!m.adjustable) continue
      const id = m.ma_player_id
      if (!(id in initial)) {
        initial[id] = props.before?.[id] ?? m.delay_ms ?? 0
        values[id] = m.delay_ms ?? 0
      } else if (m.delay_ms != null && !sync.hasPendingWrite(id)) {
        // Another tab, or the bridge: follow it unless this screen has an edit on the way.
        values[id] = m.delay_ms
      }
    }
  },
  { immediate: true },
)

/** A live write Music Assistant refused: say so, and show what it really holds. */
const writeError = ref('')
watch(
  () => sync.writeFailure,
  (failure) => {
    if (!failure || !(failure.member in initial)) return
    const name = nameOf(failure.member)
    writeError.value =
      failure.code === 'ma_forbidden'
        ? t('sync.manual.writeForbidden', { name })
        : t('sync.manual.writeFailed', { name, code: failure.code })
    if (sync.hasPendingWrite(failure.member)) return
    const held = sync.group?.members.find((m) => m.ma_player_id === failure.member)?.delay_ms
    values[failure.member] = held ?? initial[failure.member]!
  },
)

const changedIds = computed(() => Object.keys(initial).filter((id) => values[id] !== initial[id]))
watch(
  () => changedIds.value.length > 0,
  (changed) => emit('changed', changed),
  { immediate: true },
)

const note = ref('')

function write(member: string, ms: number) {
  note.value = ''
  writeError.value = ''
  values[member] = ms
  sync.setDelay(member, ms)
}

/** Back to the session start: only the members that moved are written. */
function reset() {
  for (const id of changedIds.value) {
    if (departed.value.has(id)) continue
    write(id, initial[id]!)
  }
  note.value = t('sync.manual.resetDone')
}

/* -- getting the group ready: stop it, start the clicker, set the volumes ----------------- */

type Prep = 'stop' | 'clicker' | 'volume' | 'ready'
/** Where the preparation stands; a page opened on a session whose clicker plays goes straight to tuning. */
const PREP_STEPS = ['stop', 'clicker', 'volume'] as const
/** Where the preparation stands; a session whose clicks play already goes straight to tuning. */
const prep = ref<Prep>(
  props.skipPrep || session.value?.clicker_playing ? 'ready' : session.value && session.value.group_state !== 'playing' ? 'clicker' : 'stop',
)
/** The stop step was skipped because nothing played: say so on the clicks step. */
const autoNote = ref(prep.value === 'clicker' ? t('sync.prep.stop.notPlaying') : '')
const prepBusy = ref(false)
const prepError = ref('')

function failText(e: unknown) {
  const code = errorCode(e)
  if (code === 'group_playing') return t('sync.prep.clicker.groupPlaying')
  if (code === 'clicker_stopped') return t('sync.prep.clicker.stopped')
  if (code === 'sync_mute_unsupported') return t('sync.mute.unsupportedManual', { name: nameOf(errorMember(e) ?? '') })
  return t('sync.listen.failed', { code })
}

async function prepCall(call: () => Promise<unknown>) {
  if (prepBusy.value) return
  prepBusy.value = true
  prepError.value = ''
  try {
    await call()
  } catch (e) {
    prepError.value = failText(e)
  } finally {
    prepBusy.value = false
  }
}

function toPrep(next: Prep) {
  prepError.value = ''
  autoNote.value = ''
  prep.value = next
}

async function setVolume(member: string, level: number) {
  prepError.value = ''
  try {
    await sync.volume(member, level)
  } catch (e) {
    prepError.value = failText(e)
  }
}

/* -- listening --------------------------------------------------------------------- */

const failure = ref('')
const abPair = ref<readonly [string, string] | null>(null)
let abTimer: ReturnType<typeof setInterval> | null = null

function stopAb() {
  if (abTimer) clearInterval(abTimer)
  abTimer = null
  abPair.value = null
}

async function run(call: Promise<unknown>) {
  try {
    await call
    failure.value = ''
    return true
  } catch (e) {
    failure.value = failText(e)
    return false
  }
}

/**
 * The clicker's state as this screen asked for it, until the session says otherwise:
 * solo and A/B are offered only while the clicker plays, and must be there right after Play.
 */
const signalWanted = ref<boolean | null>(null)
const signalRunning = computed(() => signalWanted.value ?? !!session.value?.clicker_playing)
watch(
  () => session.value?.clicker_playing,
  () => {
    signalWanted.value = null
  },
)

function onSignal(action: 'start' | 'stop') {
  if (action === 'stop') stopAb()
  signalWanted.value = action === 'start'
  // "Stop clicks" stops our clicker only: music someone started meanwhile plays on
  void run(sync.signal(action === 'start' ? 'start' : 'stop_clicker')).then((ok) => {
    if (!ok) signalWanted.value = null
  })
}

/**
 * Manual tuning's listening sound (semi-automatic tuning plays the measurement track: no choice there).
 * The pick shows at once; a playing clicker starts over with it, keeping the mutes and the solo.
 */
const soundWanted = ref<SyncSound | null>(null)
const sound = computed<SyncSound>(() => soundWanted.value ?? session.value?.sound ?? 'clicks')
watch(
  () => session.value?.sound,
  () => {
    soundWanted.value = null
  },
)

function onSound(next: SyncSound) {
  soundWanted.value = next
  void run(sync.sound(next)).then((ok) => {
    if (!ok) soundWanted.value = null
  })
}

function onSolo(member: string | null) {
  stopAb()
  void run(sync.solo(member))
}

function onAb(a: string, b: string) {
  stopAb()
  const pair = [a, b] as const
  abPair.value = pair
  let current: string = a
  abTimer = setInterval(() => {
    current = current === a ? b : a
    void run(sync.solo(current)).then((ok) => {
      if (!ok && abPair.value === pair) stopAb()
    })
  }, AB_MS)
  void run(sync.ab(a, b)).then((ok) => {
    if (!ok && abPair.value === pair) stopAb()
  })
}

// Nothing to alternate once the session is over, or a member of the pair left.
watch(
  () => [session.value?.state, [...departed.value]] as const,
  ([state, gone]) => {
    const pair = abPair.value
    if (!pair) return
    if (!state || ENDED.has(state) || gone.includes(pair[0]) || gone.includes(pair[1])) stopAb()
  },
)

onBeforeUnmount(stopAb)

/** The click track's time left, counted down between the bridge's readings. */
const leftS = useCountdown(() => session.value?.clicker_left_s ?? null)

/** The bridge sees the clicker playing: solo and A/B only then. */
const soloReady = computed(() => !!session.value?.clicker_playing)

const listenStatus = computed(() => {
  const s = session.value
  if (abPair.value) return t('sync.listen.alternating', { a: nameOf(abPair.value[0]), b: nameOf(abPair.value[1]) })
  if (!signalRunning.value) return t('sync.listen.idle')
  return s?.soloed ? t('sync.listen.soloed', { name: nameOf(s.soloed) }) : t('sync.listen.playing')
})
</script>

<template>
  <div class="mt-4 flex flex-col gap-4">
    <SbCard v-if="prep !== 'ready'" data-manual-prep>
      <h2 class="mb-3 text-sm font-medium text-text-secondary">
        {{ t('sync.manual.prepOf', { n: (PREP_STEPS as readonly string[]).indexOf(prep) + 1, total: PREP_STEPS.length, name: t(`sync.wizard.steps.${prep}`) }) }}
      </h2>
      <StepStopGroup
        v-if="prep === 'stop'"
        :group-state="session?.group_state ?? null"
        :clicker-playing="!!session?.clicker_playing"
        :busy="prepBusy"
        :error="prepError"
        @stop="prepCall(() => sync.signal('stop'))"
        @next="toPrep('clicker')"
        cancel-label="skip"
        @cancel="toPrep('ready')"
      />
      <StepClicker
        v-else-if="prep === 'clicker'"
        :clicker-playing="!!session?.clicker_playing"
        :started="(session?.clicker_starts ?? 0) > 0"
        :left-s="leftS"
        :note="autoNote"
        :busy="prepBusy"
        :error="prepError"
        @start="prepCall(() => sync.signal('start'))"
        @next="toPrep('volume')"
        @back="toPrep('stop')"
        cancel-label="skip"
        @cancel="toPrep('ready')"
      />
      <StepVolume
        v-else
        :members="present"
        :volumes="session?.volumes ?? {}"
        :error="prepError"
        @volume="setVolume"
        @next="toPrep('ready')"
        @back="toPrep('clicker')"
        cancel-label="skip"
        @cancel="toPrep('ready')"
      />
    </SbCard>

    <SbCard v-else>
      <h2 class="mb-3 text-sm font-medium text-text-secondary">{{ t('sync.listen.title') }}</h2>
      <SoundPicker v-if="session?.mode === 'manual'" class="mb-3" :model-value="sound" @update:model-value="onSound" />
      <ListeningBar
        :members="present"
        :signal-running="signalRunning"
        :solo-ready="soloReady"
        :soloed="session?.soloed ?? null"
        :alternating="abPair"
        @signal="onSignal"
        @solo="onSolo"
        @ab="onAb"
      />
      <p data-listen-status role="status" aria-live="polite" class="mt-3 text-sm text-text-secondary">
        {{ listenStatus }}<template v-if="signalRunning && leftS != null"> {{ t('sync.prep.clicker.left', { time: formatClock(leftS) }) }}</template><template v-if="note"> {{ note }}</template>
      </p>
      <p v-if="failure" data-listen-error role="alert" class="mt-2 text-sm text-error">{{ failure }}</p>
    </SbCard>

    <SbCard>
      <div class="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-sm font-medium text-text-secondary">{{ t('sync.manual.title') }}</h2>
        <SbButton type="button" size="sm" variant="outline" data-reset :disabled="changedIds.length === 0" @click="reset">
          <template #icon-left><RotateCcw aria-hidden="true" /></template>
          {{ t('sync.manual.reset') }}
        </SbButton>
      </div>
      <p class="mb-2 text-xs text-text-secondary">{{ t('sync.manual.help') }}</p>
      <p v-if="writeError" data-write-error role="alert" class="mb-2 text-sm text-error">{{ writeError }}</p>
      <ul class="divide-y divide-border">
        <li v-for="m in members" :key="m.ma_player_id">
          <div
            v-if="departed.has(m.ma_player_id)"
            :data-departed="m.ma_player_id"
            class="flex items-center justify-between gap-3 py-3 text-text-secondary"
          >
            <span :title="m.name" class="min-w-0 flex-1 truncate line-through">{{ nameOf(m.ma_player_id) }}</span>
            <SbBadge tone="warning" size="sm">{{ t('sync.manual.departed') }}</SbBadge>
          </div>
          <DelayRow
            v-else-if="m.adjustable"
            :member="m"
            :model-value="values[m.ma_player_id] ?? 0"
            :measured="measured?.[m.ma_player_id]"
            :before="initial[m.ma_player_id]"
            @update:model-value="write(m.ma_player_id, $event)"
          />
          <div v-else data-fixed-row class="flex items-center justify-between gap-3 py-3">
            <span :title="m.name" class="min-w-0 flex-1 truncate text-text-primary">{{ nameOf(m.ma_player_id) }}</span>
            <SbBadge tone="warning" size="sm">{{ t('sync.origin.other') }}</SbBadge>
          </div>
        </li>
      </ul>
    </SbCard>
  </div>
</template>
