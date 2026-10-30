<script setup lang="ts">
import { confirmAction } from '@/composables/useActionPolicy'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useNow } from '@/composables/useNow'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import { forgetAirCalibration, setLatency } from '@/api/devices'
import { getLatencyHistory, type TimingSample } from '@/api/calibration'
import { ApiError } from '@/api/client'
import { SbBadge, SbButton } from '@/kit'
import SettingField from '@/components/settings/SettingField.vue'
import SettingsRow from '@/components/settings/SettingsRow.vue'
import { DEVICE_GROUPS } from '@/settings/device'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import { signedMs } from '@/utils/deviceChips'
import type { Device } from '@/api/types'

const props = defineProps<{ device: Device }>()

const { t, te, locale } = useI18n()
const bridge = useBridgeStore()
const notifications = useNotificationStore()

const fields = DEVICE_GROUPS.find((g) => g.id === 'sync')!.fields
const showAdvanced = technicalDetails

/* Live values: timing edits are applied at once, no save step. */
function read(key: string) {
  if (key === 'static_delay_ms') return props.device.audio.static_delay_ms
  if (key === 'min_buffer_ms') return props.device.timing.min_buffer_ms
  if (key === 'required_lead_time_ms') return props.device.timing.required_lead_time_ms
  return undefined
}

async function apply(field: 'static_delay_ms' | 'min_buffer_ms' | 'required_lead_time_ms', value: number, source = 'manual', revision?: string) {
  try {
    await setLatency(props.device.id, value, { field, source, revision })
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  }
}

function write(key: string, value: unknown) {
  if (typeof value === 'number') void apply(key as 'static_delay_ms', value)
}

/* The bridge's own suggestion (BlueZ delay report, codec defaults). */
const suggestion = computed(() => props.device.timing.latency_suggestion)
const canSuggest = computed(
  () => suggestion.value?.suggested_static_delay_ms != null && suggestion.value.suggested_static_delay_ms !== Math.round(props.device.audio.static_delay_ms),
)

/* Recent timing samples, drawn as two small lines. */
const samples = ref<TimingSample[]>([])
async function loadHistory() {
  try {
    samples.value = (await getLatencyHistory(props.device.id)).samples.slice(-120)
  } catch {
    samples.value = []
  }
}
onMounted(loadHistory)

function path(key: keyof TimingSample) {
  const values = samples.value.map((s) => Number(s[key] ?? NaN)).filter((v) => Number.isFinite(v))
  if (values.length < 2) return ''
  const max = Math.max(...values.map(Math.abs), 1)
  return values
    .map((v, i) => `${((i / (values.length - 1)) * 300).toFixed(1)},${(30 - (v / max) * 26).toFixed(1)}`)
    .join(' ')
}
/* The engine's rows; off mode ({mode: 'off'}) has none to show. */
const playout = computed(() => {
  const p = props.device.timing.playout
  return p && p.mode !== 'off' ? p : null
})
const fmt = (v: number | null | undefined, unit: string, digits = 1) => (v == null ? '—' : `${v.toFixed(digits)} ${unit}`)
/* Plain timing rows, shown in every mode when the daemon reports them. */
const latencyRows = computed(() => {
  const timing = props.device.timing
  const p = timing.playout
  const pa = timing.pa_latency_ms ?? p?.pa_latency_ms
  const slew = timing.clock_slew_ppm ?? p?.clock_slew_ppm
  const rows: [string, string][] = []
  if (pa != null) rows.push(['paLatency', fmt(pa, 'ms', 0)])
  if (slew != null) rows.push(['clockSlew', fmt(slew, 'ppm', 1)])
  return rows
})
const playoutRows = computed(() => {
  const p = playout.value
  if (!p) return []
  return [
    ['error', fmt(p.error_ms, 'ms')],
    ['p95', fmt(p.error_p95_ms, 'ms')],
    ['slope', fmt(p.error_slope_ms_per_s, 'ms/s', 2)],
    ['corrections', fmt(p.corrections_per_s, '/s', 2)],
    ['jumps', String(p.jumps ?? 0)],
    ['underruns', String(p.underruns ?? 0)],
    ['hostLatency', fmt(p.host_latency_ms, 'ms')],
    ['sinkRate', fmt(p.sink_rate_ppm, 'ppm', 0)],
    ['warm', p.warm ? t('timing.playout.yes') : p.warm_s != null && p.warmup_s != null ? t('timing.playout.warming', { s: Math.max(0, Math.ceil(p.warmup_s - p.warm_s)) }) : t('timing.playout.no')],
  ] as [string, string][]
})
/* Air sensor: null (switch off) shows nothing. */
const air = computed(() => props.device.timing.air_sensor ?? null)
const airReason = computed(() => {
  const reason = air.value?.reason
  if (!reason) return null
  if (reason.startsWith('codec:')) return t('timing.air.reasons.codec', { codec: reason.slice(6).toUpperCase() })
  /* input:<rate>/<bits> from the daemon's tap: the format it cannot read. */
  if (reason.startsWith('input:')) return t('timing.air.reasons.unsupported_input')
  const key = `timing.air.reasons.${reason}`
  return te(key) ? t(key) : t('timing.air.reasons.unknown', { reason })
})
const airHasValues = computed(() => air.value != null && air.value.host_placement_ms != null)
const now = useNow()
const airRows = computed(() => {
  const a = air.value
  if (!a || !airHasValues.value) return []
  const jitter = a.jitter_ms == null ? '' : ` (±${a.jitter_ms.toFixed(1)})`
  const rows: [string, string][] = [['host', `${signedMs(a.host_placement_ms!)} ms${jitter}`]]
  rows.push(['air', a.air_error_ms != null && a.calibrated ? `${signedMs(a.air_error_ms)} ms` : t('timing.air.needsCalibration')])
  if (a.confidence != null) rows.push(['confidence', `${Math.round(a.confidence * 100)} %`])
  if (a.psr != null) rows.push(['psr', a.psr.toFixed(2)])
  if (a.last_confirmed_at) {
    const ageS = Math.max(0, Math.round((now.value - Date.parse(a.last_confirmed_at)) / 1000))
    if (Number.isFinite(ageS)) rows.push(['lock', t('timing.air.agoSeconds', { s: ageS })])
  }
  return rows
})
/* Stored air-to-sound calibrations (one per codec, SBC rate and adapter), also with the sensor off. */
type AirCalibration = Device['audio']['air_to_sound_latency'][number]
const calibrations = computed(() => props.device.audio.air_to_sound_latency ?? [])
const calibrationKey = (c: Pick<AirCalibration, 'codec' | 'rate' | 'adapter'>) => `${c.codec}/${c.rate}/${c.adapter}`
const AGE_STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['day', 86_400],
  ['hour', 3_600],
  ['minute', 60],
]
function calibrationAge(iso: string): string {
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return '—'
  const seconds = Math.max(0, Math.round((Date.now() - at) / 1000))
  const fmtAge = new Intl.RelativeTimeFormat(locale.value, { numeric: 'auto' })
  for (const [unit, size] of AGE_STEPS) {
    if (seconds >= size) return fmtAge.format(-Math.floor(seconds / size), unit)
  }
  return fmtAge.format(-seconds, 'second')
}
function calibrationParams(c: Pick<AirCalibration, 'codec' | 'rate' | 'adapter'>) {
  const own = props.device.bluetooth?.adapter
  const adapter = own?.hci && own.mac?.toUpperCase() === c.adapter.toUpperCase() ? own.hci : c.adapter
  return { codec: c.codec.toUpperCase(), rate: String(Number((c.rate / 1000).toFixed(1))), adapter }
}
const calibrationLine = (c: AirCalibration) =>
  t('timing.air.calibration.line', { ...calibrationParams(c), latency: c.latency_ms.toFixed(1), age: calibrationAge(c.measured_at) })
const forgetting = ref('')
async function forgetCalibration(c: AirCalibration) {
  const ok = await confirmAction('timing.forgetCalibration', {
    title: t('timing.air.calibration.forgetTitle'),
    message: t('timing.air.calibration.forgetConfirm', { ...calibrationParams(c), name: props.device.name }),
    confirmLabel: t('timing.air.calibration.forget'),
  })
  if (!ok) return
  forgetting.value = calibrationKey(c)
  try {
    await forgetAirCalibration(props.device.id, c.codec, c.rate, c.adapter)
    await bridge.refresh()
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  } finally {
    forgetting.value = ''
  }
}
/* Learned latencies (from the speaker's starts), forgotten the same way as a calibration. */
type AirLearned = Device['audio']['air_learned_latency'][number]
const learnedEntries = computed(() => props.device.audio.air_learned_latency ?? [])
const learnedLine = (c: AirLearned) =>
  t('timing.air.learned.line', {
    latency: c.latency_ms.toFixed(1),
    delay: c.d_ref_ms.toFixed(0),
    codec: c.codec.toUpperCase(),
    adapter: calibrationParams(c).adapter,
    starts: c.starts,
  })
async function forgetLearned(c: AirLearned) {
  const ok = await confirmAction('timing.forgetLearned', {
    title: t('timing.air.learned.forgetTitle'),
    message: t('timing.air.learned.forgetConfirm', { ...calibrationParams(c), name: props.device.name }),
    confirmLabel: t('timing.air.learned.forget'),
  })
  if (!ok) return
  forgetting.value = `learned:${calibrationKey(c)}`
  try {
    await forgetAirCalibration(props.device.id, c.codec, c.rate, c.adapter)
    await bridge.refresh()
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  } finally {
    forgetting.value = ''
  }
}
/* The air correction of a loop-mode speaker: target, bias, corrections and the delay trim. */
const LEARN_NEED = 2
const correctionLines = computed(() => {
  const p = playout.value
  if (!p || p.mode !== 'loop' || p.air_bias_state == null) return []
  const lines: string[] = []
  const parts: string[] = []
  if (p.air_target === 'calibrated' && p.air_target_ms != null) {
    parts.push(t('timing.air.correction.calibrated', { ms: p.air_target_ms.toFixed(1) }))
  } else if (p.air_target === 'learned') {
    parts.push(t('timing.air.correction.learned', { n: p.air_learned_starts ?? 0 }))
  } else {
    const a = air.value
    const own = a?.codec != null && a.rate != null && a.adapter != null ? learnedEntries.value.find((e) => e.codec === a.codec && e.rate === a.rate && e.adapter === a.adapter) : undefined
    parts.push(t('timing.air.correction.noData', { n: own?.samples_ms.length ?? 0, need: LEARN_NEED }))
  }
  const locked = air.value?.state === 'locked' || air.value?.state === 'holding'
  if (p.air_bias_ms == null && air.value != null && !locked) parts.push(t('timing.air.correction.inactive'))
  lines.push(t('timing.air.correction.target', { target: parts.join(' · ') }))
  if (p.air_bias_ms != null) lines.push(t('timing.air.correction.bias', { value: signedMs(p.air_bias_ms) }))
  if (p.air_corrections > 0) lines.push(t('timing.air.correction.corrections', { n: p.air_corrections }))
  const trim = p.air_trim_ms
  if (trim != null && Math.abs(trim) >= 0.05) {
    const key = trim > 0 ? 'trimEarly' : 'trimLate'
    lines.push(t(`timing.air.correction.${key}`, { value: signedMs(-trim), ms: Math.abs(trim).toFixed(1) }))
  }
  return lines
})
const syncError = computed(() => props.device.timing.sync_error_ms)
</script>

<template>
  <div class="py-2">
    <div data-timing-main class="divide-y divide-border">
      <template v-for="f in fields" :key="f.key">
        <SettingField v-if="!f.advanced" :field="f" i18n-base="settings.device" :read="read" :write="write" />
      </template>
      <SettingsRow
        v-if="canSuggest"
        :label="t('timing.suggestion', { value: suggestion!.suggested_static_delay_ms })"
        :help="suggestion!.explanation"
      >
        <SbButton
          variant="outline"
          size="sm"
          @click="apply('static_delay_ms', suggestion!.suggested_static_delay_ms!, suggestion!.source, suggestion!.revision ?? undefined)"
        >{{ t('timing.applySuggestion') }}</SbButton>
      </SettingsRow>
      <SettingsRow v-if="device.music_assistant.syncgroup_id" :label="t('sync.tuneGroup')" :help="t('sync.tuneGroupHelp')">
        <RouterLink
          :to="{ name: 'sync', params: { groupId: device.music_assistant.syncgroup_id } }"
          class="inline-flex h-8 items-center rounded-(--radius-button) border border-border-strong px-3 text-sm font-medium text-text-primary transition-colors hover:bg-surface-secondary"
        >
          {{ t('sync.tuneLink') }}
        </RouterLink>
      </SettingsRow>
      <SettingsRow v-else :label="t('sync.tuneGroup')" :help="t('sync.noGroupHelp', { name: device.name })" data-no-group>
        <RouterLink
          :to="{ name: 'groups' }"
          class="inline-flex h-8 items-center rounded-(--radius-button) border border-border-strong px-3 text-sm font-medium text-text-primary transition-colors hover:bg-surface-secondary"
        >
          {{ t('sync.noGroupLink') }}
        </RouterLink>
      </SettingsRow>
    </div>

    <details v-if="technicalDetails" data-timing-details class="mt-4 rounded-(--radius-card) border border-border">
      <summary class="cursor-pointer px-3 py-2 text-sm font-medium text-text-primary">{{ t('timing.details') }}</summary>
      <div class="px-3 pb-3">
        <div class="divide-y divide-border">
          <template v-for="f in fields" :key="f.key">
            <SettingField v-if="f.advanced && showAdvanced" :field="f" i18n-base="settings.device" :read="read" :write="write" />
          </template>
        </div>

    <dl v-if="latencyRows.length" class="mt-4 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm" data-testid="timing-latency">
      <template v-for="[key, value] in latencyRows" :key="key">
        <dt class="text-text-secondary">{{ t(`timing.playout.rows.${key}`) }}</dt>
        <dd class="tabular-nums text-text-primary">{{ value }}</dd>
      </template>
    </dl>

    <section v-if="playout" class="mt-4" data-testid="playout-block">
      <h3 class="text-xs font-semibold uppercase tracking-wide text-text-secondary">{{ t('timing.playout.title') }}</h3>
      <p v-if="playout.state === 'fallback'" class="mt-1 text-sm text-warning-text" data-testid="playout-fallback">
        {{ t('timing.playout.fallback', { reason: playout.fallback_reason ?? t('timing.playout.unknownReason') }) }}
      </p>
      <dl class="mt-2 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
        <template v-for="[key, value] in playoutRows" :key="key">
          <dt class="text-text-secondary">{{ t(`timing.playout.rows.${key}`) }}</dt>
          <dd class="tabular-nums text-text-primary">{{ value }}</dd>
        </template>
      </dl>
    </section>

    <section v-if="air" class="mt-4" data-testid="air-sensor-block">
      <h3 class="text-xs font-semibold uppercase tracking-wide text-text-secondary">{{ t('timing.air.title') }}</h3>
      <dl class="mt-2 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
        <dt class="text-text-secondary">{{ t('timing.air.rows.state') }}</dt>
        <dd class="text-text-primary">{{ te(`timing.air.states.${air.state}`) ? t(`timing.air.states.${air.state}`) : air.state }}</dd>
        <template v-for="[key, value] in airRows" :key="key">
          <dt class="text-text-secondary">{{ t(`timing.air.rows.${key}`) }}</dt>
          <dd class="tabular-nums text-text-primary">{{ value }}</dd>
        </template>
      </dl>
      <p v-if="airReason" class="mt-1 text-sm text-text-secondary" data-testid="air-sensor-reason">{{ airReason }}</p>
    </section>

    <ul v-if="correctionLines.length" class="mt-4 space-y-1 text-sm tabular-nums text-text-primary" data-testid="air-correction">
      <li v-for="line in correctionLines" :key="line">{{ line }}</li>
    </ul>

    <section v-if="learnedEntries.length" class="mt-4" data-testid="air-learned">
      <h3 class="text-xs font-semibold uppercase tracking-wide text-text-secondary">{{ t('timing.air.learned.title') }}</h3>
      <ul class="mt-2 space-y-1 text-sm">
        <li v-for="c in learnedEntries" :key="calibrationKey(c)" class="flex items-center justify-between gap-2">
          <span class="tabular-nums text-text-primary">{{ learnedLine(c) }}</span>
          <SbButton
            variant="outline"
            size="sm"
            data-testid="air-learned-forget"
            :loading="forgetting === `learned:${calibrationKey(c)}`"
            @click="forgetLearned(c)"
          >{{ t('timing.air.learned.forget') }}</SbButton>
        </li>
      </ul>
    </section>

    <section v-if="calibrations.length" class="mt-4" data-testid="air-calibrations">
      <h3 class="text-xs font-semibold uppercase tracking-wide text-text-secondary">{{ t('timing.air.calibration.title') }}</h3>
      <ul class="mt-2 space-y-1 text-sm">
        <li v-for="c in calibrations" :key="calibrationKey(c)" class="flex items-center justify-between gap-2">
          <span class="tabular-nums text-text-primary">{{ calibrationLine(c) }}</span>
          <SbButton
            variant="outline"
            size="sm"
            data-testid="air-calibration-forget"
            :loading="forgetting === calibrationKey(c)"
            @click="forgetCalibration(c)"
          >{{ t('timing.air.calibration.forget') }}</SbButton>
        </li>
      </ul>
    </section>

    <section class="mt-4">
      <div class="flex items-center justify-between">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-text-secondary">{{ t('timing.history.title') }}</h3>
        <SbBadge v-if="syncError != null" :tone="Math.abs(syncError) < 20 ? 'success' : 'warning'" size="sm">
          {{ t('timing.history.syncError', { ms: syncError.toFixed(1) }) }}
        </SbBadge>
      </div>
      <svg v-if="path('buffered_audio_ms')" viewBox="0 0 300 32" class="mt-2 h-16 w-full" preserveAspectRatio="none" role="img" :aria-label="t('timing.history.title')">
        <polyline :points="path('buffered_audio_ms')" fill="none" class="stroke-primary" stroke-width="1.5" vector-effect="non-scaling-stroke" />
        <polyline :points="path('playback_sync_error_ms')" fill="none" class="stroke-warning" stroke-width="1.5" vector-effect="non-scaling-stroke" />
      </svg>
      <p v-else class="mt-2 text-sm text-text-secondary">{{ t('timing.history.empty') }}</p>
      <p v-if="path('buffered_audio_ms')" class="mt-1 flex gap-4 text-xs text-text-secondary">
        <span><span class="mr-1 inline-block size-2 rounded-full bg-primary" />{{ t('timing.history.buffer') }}</span>
        <span><span class="mr-1 inline-block size-2 rounded-full bg-warning" />{{ t('timing.history.error') }}</span>
      </p>
    </section>

      </div>
    </details>
  </div>
</template>
