<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Copy, Play, Smartphone } from 'lucide-vue-next'
import { SbBadge, SbButton, SbSpinner } from '@/kit'
import { captureLink, startSequence } from '@/api/sync'
import type { SyncMember, SyncSession } from '@/api/types'
import { errorCode, useSyncStore } from '@/stores/sync'
import { speakerName } from '@/sync/names'
import { qrPath } from '@/sync/qr'
import { formatClock } from '@/sync/format'

/**
 * Measuring with a phone (semi-automatic, "a phone" chosen on step 1): a link and QR code to the
 * bridge's capture page; the bridge plays every speaker alone in turn ("the sequence") while the phone
 * records once, and the phone sends the recording. Results arrive with the session (its events).
 */
const props = defineProps<{
  session: SyncSession
  /** Members to measure, in order (adjustable, not departed). */
  members: SyncMember[]
  /** Seconds of the click track left. */
  clickerLeftS?: number | null
}>()
const emit = defineEmits<{ next: []; back: []; cancel: []; restart: [] }>()

const { t, locale } = useI18n()
const sync = useSyncStore()
const nameOf = (m: SyncMember) => speakerName(m.name, m.ma_player_id)

/* -- the link ------------------------------------------------------------------------- */
const link = ref<{ url: string; http_url: string; secure: boolean; expires_in_s: number } | null>(null)
const linkError = ref('')
const linking = ref(false)
async function makeLink() {
  linking.value = true
  linkError.value = ''
  try {
    const out = await captureLink(props.session.id, locale.value === 'ru' ? 'ru' : 'en')
    link.value = { url: out.url, http_url: out.http_url, secure: out.secure, expires_in_s: out.expires_in_s }
  } catch (e) {
    linkError.value = t('sync.phone.linkFailed', { code: errorCode(e) })
  } finally {
    linking.value = false
  }
}
onMounted(makeLink)
const qr = computed(() => (link.value ? qrPath(link.value.url) : null))
const copied = ref<'' | 'copied' | 'failed'>('')
async function copyLink() {
  if (!link.value) return
  try {
    await navigator.clipboard.writeText(link.value.url)
    copied.value = 'copied'
  } catch {
    copied.value = 'failed'
  }
}

/* -- which members, the sequence ------------------------------------------------------------ */
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
const playing = ref(false)
const playError = ref('')
async function play() {
  const members = toPlay.value.map((m) => m.ma_player_id)
  if (!members.length) return
  playing.value = true
  playError.value = ''
  try {
    sync.applyEvent(await startSequence(props.session.id, members))
  } catch (e) {
    const code = errorCode(e)
    playError.value =
      code === 'clicker_stopped' ? t('sync.prep.clicker.stopped') : t('sync.wizard.failed', { code })
  } finally {
    playing.value = false
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

const progress = computed(() => {
  const s = sequence.value
  if (!s) return ''
  if (s.phase === 'starting' || s.phase === 'lead') return t('sync.phone.lead')
  if (s.phase === 'solo' && s.member) {
    const m = props.members.find((x) => x.ma_player_id === s.member)
    return t('sync.phone.now', { name: m ? nameOf(m) : s.member, n: s.index + 1, total: s.members.length })
  }
  if (s.phase === 'gap') return t('sync.phone.pause')
  if (s.phase === 'done') return s.uploaded ? t('sync.phone.measured') : t('sync.phone.waitingUpload')
  if (s.phase === 'failed') return t('sync.phone.failed', { code: s.error ?? '' })
  return ''
})

const left = computed(
  () => props.members.filter((m) => !skipped.value.includes(m.ma_player_id) && status(m.ma_player_id) !== 'ok').length,
)
const complete = computed(() => props.members.length > 0 && left.value === 0)
const tone = (s: string | null) => (s === 'ok' ? 'success' : s === 'skipped' || !s ? 'neutral' : 'warning')
const label = (s: string | null) =>
  s ? t(`sync.wizard.${s === 'ok' ? 'heard' : s === 'not_heard' ? 'notHeard' : s}`) : t('sync.phone.notYet')
</script>

<template>
  <div data-phone class="flex flex-col gap-4">
    <p class="text-sm text-text-primary">{{ t('sync.phone.help') }}</p>

    <div class="flex flex-col items-center gap-3 rounded-(--radius-card) border border-border bg-surface-card px-4 py-4 sm:flex-row sm:items-start">
      <div v-if="linking" class="flex size-44 items-center justify-center"><SbSpinner /></div>
      <svg
        v-else-if="qr"
        data-qr
        role="img"
        :aria-label="t('sync.phone.qr')"
        :viewBox="`-2 -2 ${qr.size + 4} ${qr.size + 4}`"
        class="size-44 shrink-0 rounded bg-white p-1"
        shape-rendering="crispEdges"
      >
        <rect :x="-2" :y="-2" :width="qr.size + 4" :height="qr.size + 4" fill="#fff" />
        <path :d="qr.d" fill="#000" />
      </svg>
      <div class="flex min-w-0 flex-1 flex-col gap-2 text-sm">
        <p class="flex items-center gap-2 font-medium text-text-primary">
          <Smartphone class="size-4" aria-hidden="true" />{{ t('sync.phone.scan') }}
        </p>
        <p v-if="link" data-link class="break-all font-mono text-xs text-text-secondary">{{ link.url }}</p>
        <p v-if="link?.secure" data-cert-note class="text-xs text-text-secondary">{{ t('sync.phone.certificate') }}</p>
        <p v-if="link?.secure" class="text-xs text-text-secondary">
          {{ t('sync.phone.httpFallback') }}
          <span data-http-link class="break-all font-mono">{{ link.http_url }}</span>
        </p>
        <div class="flex flex-wrap items-center gap-2">
          <SbButton v-if="link" type="button" size="sm" variant="outline" data-copy-link @click="copyLink">
            <template #icon-left><Copy aria-hidden="true" /></template>
            {{ t('sync.phone.copy') }}
          </SbButton>
          <SbButton type="button" size="sm" variant="ghost" data-new-link :loading="linking" @click="makeLink">
            {{ t('sync.phone.newLink') }}
          </SbButton>
          <span v-if="copied" role="status" class="text-xs text-text-secondary">
            {{ copied === 'copied' ? t('sync.proposal.copied') : t('sync.proposal.copyFailed') }}
          </span>
        </div>
        <p class="text-xs text-text-secondary">{{ t('sync.phone.security', { min: Math.round((link?.expires_in_s ?? 1800) / 60) }) }}</p>
        <p class="text-xs text-text-secondary">{{ t('sync.phone.sameNetwork') }}</p>
        <p v-if="linkError" role="alert" class="text-error">{{ linkError }}</p>
      </div>
    </div>

    <ol class="list-decimal pl-5 text-sm text-text-primary">
      <li>{{ t('sync.phone.step1') }}</li>
      <li>{{ t('sync.phone.step2') }}</li>
      <li>{{ t('sync.phone.step3') }}</li>
    </ol>

    <ul class="flex flex-col divide-y divide-border rounded-(--radius-card) border border-border">
      <li v-for="m in members" :key="m.ma_player_id" :data-phone-member="m.ma_player_id" class="flex flex-wrap items-center gap-2 px-4 py-2">
        <span :title="m.name" class="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">{{ nameOf(m) }}</span>
        <SbBadge :tone="tone(skipped.includes(m.ma_player_id) ? 'skipped' : status(m.ma_player_id))" size="sm">
          {{ label(skipped.includes(m.ma_player_id) ? 'skipped' : status(m.ma_player_id)) }}
        </SbBadge>
        <SbButton
          type="button"
          size="sm"
          variant="ghost"
          :data-phone-skip="m.ma_player_id"
          :aria-pressed="skipped.includes(m.ma_player_id)"
          :disabled="running"
          @click="toggleSkip(m.ma_player_id)"
        >
          {{ skipped.includes(m.ma_player_id) ? t('sync.phone.measureIt') : t('sync.wizard.skip') }}
        </SbButton>
      </li>
    </ul>

    <div class="flex flex-col gap-2">
      <div class="flex flex-wrap items-center gap-3">
        <SbButton
          type="button"
          data-play-sequence
          :loading="playing"
          :disabled="running || !session.clicker_playing || toPlay.length === 0"
          :aria-describedby="!session.clicker_playing ? 'sync-phone-why' : undefined"
          @click="play()"
        >
          <template #icon-left><Play aria-hidden="true" /></template>
          {{ t('sync.phone.play', { n: toPlay.length }) }}
        </SbButton>
        <span v-if="clickerLeftS != null && session.clicker_playing" class="text-xs text-text-secondary">
          {{ t('sync.prep.clicker.left', { time: formatClock(clickerLeftS) }) }}
        </span>
      </div>
      <p v-if="!session.clicker_playing" id="sync-phone-why" class="flex flex-wrap items-center gap-2 text-sm text-text-secondary">
        {{ t('sync.prep.clicker.stopped') }}
        <SbButton type="button" size="sm" variant="outline" data-restart-clicker @click="emit('restart')">
          {{ t('sync.prep.clicker.again') }}
        </SbButton>
      </p>
      <p v-if="progress" data-sequence-status role="status" aria-live="polite" class="text-sm text-text-primary">{{ progress }}</p>
      <p v-if="playError" role="alert" class="text-sm text-error">{{ playError }}</p>
    </div>

    <div class="flex flex-wrap justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel :disabled="running" @click="emit('cancel')">{{ t('sync.wizard.cancel') }}</SbButton>
      <SbButton type="button" variant="outline" data-back :disabled="running" @click="emit('back')">{{ t('sync.wizard.back') }}</SbButton>
      <SbButton type="button" data-to-tune :disabled="!complete || running" @click="emit('next')">{{ t('sync.wizard.toTune') }}</SbButton>
    </div>
    <p v-if="!complete" class="text-right text-xs text-text-secondary">{{ t('sync.wizard.members.left', { n: left }, left) }}</p>
  </div>
</template>
