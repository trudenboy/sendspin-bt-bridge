<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Mic } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'
import { captureInput, getSession, type ListeningDevice } from '@/api/sync'
import type { SyncMeasurement, SyncMember, SyncSession } from '@/api/types'
import { errorCode, useSyncStore } from '@/stores/sync'
import { formatClock } from '@/sync/format'
import { inputKindKey } from '@/sync/listening'
import { speakerName } from '@/sync/names'

/**
 * Measuring with a bridge input ("a listening device"): the bridge records the same sequence the
 * phone path plays — every member alone in turn — from one of its own inputs, then measures every
 * member from that one recording. The input is borrowed for the measurement: the source's active
 * input is not changed and whatever the operator was playing keeps playing.
 *
 * No microphone is opened here and no capture link is made. The input's latency is not calibrated;
 * that is a constant and the proposal is a difference, so it does not shift the result (the result
 * screen says so).
 */
const props = defineProps<{
  session: SyncSession
  /** Members to measure, in order (adjustable, not departed). */
  members: SyncMember[]
  /** The bridge input chosen on step 1. */
  input: ListeningDevice
  /** Seconds of the click track left. */
  clickerLeftS?: number | null
}>()
const emit = defineEmits<{ next: []; back: []; cancel: []; restart: []; phone: [] }>()

const { t } = useI18n()
const sync = useSyncStore()
const nameOf = (m: SyncMember) => speakerName(m.name, m.ma_player_id)

/* -- the members, and the sequence they play -------------------------------------------- */

const skipped = ref<string[]>([])
watch(
  () => props.session.measurements,
  (m) => {
    // a skip made on step 1 (or anywhere) shows here
    skipped.value = [...new Set([...skipped.value, ...Object.keys(m ?? {}).filter((id) => m?.[id]?.status === 'skipped')])]
  },
  { immediate: true },
)
const status = (id: string) => props.session.measurements?.[id]?.status ?? null
/** Every speaker not skipped plays in each sequence: phases from two recordings never mix (one clock). */
const toPlay = computed(() => props.members.filter((m) => !skipped.value.includes(m.ma_player_id)))
const sequence = computed(() => props.session.sequence ?? null)
const running = computed(() => !!sequence.value && ['starting', 'lead', 'solo', 'gap'].includes(sequence.value.phase))

/** The bridge is recording (and measuring) right now. */
const busy = ref(false)
/** The chosen input cannot be used for the whole measurement; the phone is the way out. */
const failure = ref<'' | 'input_unavailable' | 'input_busy'>('')
const playError = ref('')
let disposed = false
onBeforeUnmount(() => {
  disposed = true
})

const progress = computed(() => {
  const s = sequence.value
  if (!s) return busy.value ? t('sync.input.recording') : ''
  if (s.phase === 'starting' || s.phase === 'lead') return t('sync.phone.lead')
  if (s.phase === 'solo' && s.member) {
    const m = props.members.find((x) => x.ma_player_id === s.member)
    return t('sync.phone.now', { name: m ? nameOf(m) : s.member, n: s.index + 1, total: s.members.length })
  }
  if (s.phase === 'gap') return t('sync.phone.pause')
  if (s.phase === 'done') return s.uploaded ? t('sync.input.measured') : busy.value ? t('sync.input.measuring') : ''
  if (s.phase === 'failed') return t('sync.phone.failed', { code: s.error ?? '' })
  return ''
})

/**
 * Hand the chosen input and the members to the bridge: it opens the recorder, plays the sequence,
 * stops the recorder once the sequence has finished and measures every member from that one
 * recording. The sequence's progress arrives as session events; the session then holds the results
 * exactly as after a phone upload, so the wizard goes on to the same result screen.
 */
async function recordSequence() {
  const sid = props.session.id
  if (busy.value || running.value) return
  const members = toPlay.value.map((m) => m.ma_player_id)
  if (!members.length) return
  const stillHere = () => !disposed && props.session.id === sid
  busy.value = true
  failure.value = ''
  playError.value = ''
  try {
    let answer: { results: SyncMeasurement[] }
    try {
      answer = await captureInput(sid, props.input.id, members)
    } catch (e) {
      if (!stillHere()) return
      const code = errorCode(e)
      // The input disappeared, or the source feed took it: the bridge names the device it was,
      // and the phone measures the same sequence instead of leaving an empty result.
      if (code === 'input_unavailable' || code === 'input_busy') failure.value = code
      else playError.value = t('sync.wizard.failed', { code })
      return
    }
    if (!stillHere()) return
    // The server measured from the one recording: read the session it updated (an event may also
    // have arrived already) and go on where the phone path goes on.
    let fresh: SyncSession | null = null
    try {
      fresh = await getSession(sid)
    } catch {
      fresh = null
    }
    if (!stillHere()) return
    if (fresh) sync.applyEvent(fresh)
    const measured = Object.values(fresh?.measurements ?? {})
    const heard = measured.some((r) => r.status === 'ok') || (answer?.results ?? []).some((r) => r.status === 'ok')
    if (heard) emit('next')
  } finally {
    if (!disposed) busy.value = false
  }
}

async function toggleSkip(id: string) {
  if (skipped.value.includes(id)) {
    skipped.value = skipped.value.filter((x) => x !== id)
    return
  }
  try {
    await sync.skip(id)
    skipped.value = [...skipped.value, id]
  } catch (e) {
    playError.value = t('sync.wizard.failed', { code: errorCode(e) })
  }
}

const left = computed(
  () => props.members.filter((m) => !skipped.value.includes(m.ma_player_id) && status(m.ma_player_id) !== 'ok').length,
)
/** Speakers the sequence played but did not measure: they keep their current delay. */
const keeps = computed(() =>
  props.members.filter((m) => {
    const st = status(m.ma_player_id)
    return !skipped.value.includes(m.ma_player_id) && st !== null && st !== 'ok'
  }),
)
/** One speaker heard is enough to go on: the others keep their delay (see ``keeps``). */
const complete = computed(
  () => props.members.some((m) => status(m.ma_player_id) === 'ok') && (left.value === 0 || sequence.value?.phase === 'done'),
)
const tone = (s: string | null) => (s === 'ok' ? 'success' : s === 'skipped' || !s ? 'neutral' : 'warning')
const label = (s: string | null) =>
  s ? t(`sync.wizard.${s === 'ok' ? 'heard' : s === 'not_heard' ? 'notHeard' : s}`) : t('sync.phone.notYet')
</script>

<template>
  <div data-listening-capture class="flex flex-col gap-4">
    <p class="text-sm text-text-primary">{{ t('sync.input.help', { name: input.name }) }}</p>
    <p class="flex items-center gap-2 text-sm text-text-secondary">
      <Mic class="size-4" aria-hidden="true" />{{ input.name }} · {{ t(inputKindKey(input)) }}
    </p>

    <ul class="flex flex-col divide-y divide-border rounded-(--radius-card) border border-border">
      <li v-for="m in members" :key="m.ma_player_id" :data-input-member="m.ma_player_id" class="flex flex-wrap items-center gap-2 px-4 py-2">
        <span :title="m.name" class="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">{{ nameOf(m) }}</span>
        <SbBadge :tone="tone(skipped.includes(m.ma_player_id) ? 'skipped' : status(m.ma_player_id))" size="sm">
          {{ label(skipped.includes(m.ma_player_id) ? 'skipped' : status(m.ma_player_id)) }}
        </SbBadge>
        <SbButton
          type="button"
          size="sm"
          variant="ghost"
          :data-input-skip="m.ma_player_id"
          :aria-pressed="skipped.includes(m.ma_player_id)"
          :disabled="running || busy"
          @click="toggleSkip(m.ma_player_id)"
        >
          {{ skipped.includes(m.ma_player_id) ? t('sync.phone.measureIt') : t('sync.wizard.skip') }}
        </SbButton>
      </li>
    </ul>

    <div
      v-if="failure"
      data-input-failure
      role="alert"
      class="flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
    >
      <span class="min-w-0 flex-1">
        {{ t(failure === 'input_unavailable' ? 'sync.input.unavailable' : 'sync.input.busy', { name: input.name }) }}
      </span>
      <SbButton type="button" size="sm" variant="outline" data-use-phone @click="emit('phone')">
        {{ t('sync.wizard.capture.usePhone') }}
      </SbButton>
    </div>
    <p v-if="playError" data-input-error role="alert" class="text-sm text-error">{{ playError }}</p>

    <div class="flex flex-col gap-2">
      <div class="flex flex-wrap items-center gap-3">
        <SbButton
          type="button"
          data-record-sequence
          :loading="busy"
          :disabled="running || !session.clicker_playing || toPlay.length === 0"
          :aria-describedby="!session.clicker_playing ? 'sync-input-why' : undefined"
          @click="recordSequence"
        >
          <template #icon-left><Mic aria-hidden="true" /></template>
          {{ t('sync.input.play', { n: toPlay.length }) }}
        </SbButton>
        <span v-if="clickerLeftS != null && session.clicker_playing" class="text-xs text-text-secondary">
          {{ t('sync.prep.clicker.left', { time: formatClock(clickerLeftS) }) }}
        </span>
      </div>
      <p v-if="!session.clicker_playing" id="sync-input-why" class="flex flex-wrap items-center gap-2 text-sm text-text-secondary">
        {{ t('sync.prep.clicker.stopped') }}
        <SbButton type="button" size="sm" variant="outline" data-restart-clicker @click="emit('restart')">
          {{ t('sync.prep.clicker.again') }}
        </SbButton>
      </p>
      <p v-if="progress" data-sequence-status role="status" aria-live="polite" class="text-sm text-text-primary">{{ progress }}</p>
    </div>

    <div class="flex flex-wrap justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel @click="emit('cancel')">{{ t('sync.wizard.cancel') }}</SbButton>
      <SbButton type="button" variant="outline" data-back :disabled="running || busy" @click="emit('back')">{{ t('sync.wizard.back') }}</SbButton>
      <SbButton type="button" data-to-tune :disabled="!complete || running || busy" @click="emit('next')">{{ t('sync.wizard.toTune') }}</SbButton>
    </div>
    <p v-if="complete && keeps.length" data-keeps-delay class="text-right text-xs text-text-secondary">
      {{ t('sync.phone.keepsDelay', { names: keeps.map(nameOf).join(', ') }) }}
    </p>
    <p v-if="!complete" class="text-right text-xs text-text-secondary">{{ t('sync.wizard.members.left', { n: left }, left) }}</p>
  </div>
</template>
