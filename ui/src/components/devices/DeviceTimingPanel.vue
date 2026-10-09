<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import { setLatency } from '@/api/devices'
import { createSession, endSession, getLatencyHistory, setMetronome, uploadRecording, type TimingSample } from '@/api/calibration'
import { ApiError } from '@/api/client'
import { SbBadge, SbButton } from '@/kit'
import SettingField from '@/components/settings/SettingField.vue'
import SettingsRow from '@/components/settings/SettingsRow.vue'
import { DEVICE_GROUPS } from '@/settings/device'
import { microphoneAvailable, openMicrophone, recordClick } from '@/calibration/capture'
import { planCorrection } from '@/calibration/plan'
import type { Device } from '@/api/types'

const props = defineProps<{ device: Device }>()

const { t } = useI18n()
const bridge = useBridgeStore()
const notifications = useNotificationStore()

const fields = DEVICE_GROUPS.find((g) => g.id === 'sync')!.fields
const showAdvanced = ref(false)

/* Live values: timing edits are applied at once, no save step. */
function read(key: string) {
  if (key === 'static_delay_ms') return props.device.audio.static_delay_ms
  if (key === 'min_buffer_ms') return props.device.timing.min_buffer_ms
  if (key === 'required_lead_time_ms') return props.device.timing.required_lead_time_ms
  return undefined
}

async function apply(field: 'static_delay_ms' | 'min_buffer_ms' | 'required_lead_time_ms', value: number, source = 'manual', revision?: string, deviceId = props.device.id) {
  try {
    await setLatency(deviceId, value, { field, source, revision })
    notifications.success(t('timing.applied', { value }))
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

/* Click track: a continuous, phase-aligned click to compare speakers by ear. */
const metronomeBusy = ref(false)
async function toggleMetronome() {
  metronomeBusy.value = true
  try {
    await setMetronome(props.device.id, props.device.timing.calibration_metronome_active ? 'stop' : 'start')
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  } finally {
    metronomeBusy.value = false
  }
}

/* Microphone comparison against another connected speaker. */
const peers = computed(() =>
  bridge.devices.filter((d) => d.id !== props.device.id && d.enabled && d.bluetooth.connected),
)
const referenceId = ref('')
const micStep = ref('')
const canMeasure = computed(() => microphoneAvailable() && peers.value.length > 0)

async function measure() {
  const reference = peers.value.find((p) => p.id === referenceId.value) ?? peers.value[0]
  if (!reference) return
  let stream: MediaStream | null = null
  const estimates: number[] = []
  try {
    stream = await openMicrophone()
    for (let trial = 1; trial <= 3; trial++) {
      const session = await createSession()
      micStep.value = t('timing.mic.step', { n: trial, name: reference.name })
      await uploadRecording(session.session_id, 'reference', await recordClick(stream, reference.id))
      micStep.value = t('timing.mic.step', { n: trial, name: props.device.name })
      const result = await uploadRecording(session.session_id, 'target', await recordClick(stream, props.device.id))
      void endSession(session.session_id).catch(() => undefined)
      if (result.estimate?.valid && result.estimate.delay_ms != null) estimates.push(result.estimate.delay_ms)
    }
    const plan = planCorrection(
      estimates,
      { id: props.device.id, delay: props.device.audio.static_delay_ms },
      { id: reference.id, delay: reference.audio.static_delay_ms },
    )
    if (!plan.ok) {
      notifications.error(t(`timing.mic.${plan.reason}`))
      return
    }
    const late = plan.deviceId === props.device.id ? props.device : reference
    const ok = window.confirm(
      t('timing.mic.confirm', { offset: plan.median.toFixed(0), spread: plan.spread.toFixed(0), value: plan.value, name: late.name }),
    )
    if (ok) await apply('static_delay_ms', plan.value, 'microphone_calibration', undefined, plan.deviceId)
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e))
  } finally {
    stream?.getTracks().forEach((track) => track.stop())
    micStep.value = ''
  }
}

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
const syncError = computed(() => props.device.timing.sync_error_ms)
</script>

<template>
  <div class="py-2">
    <div class="divide-y divide-border">
      <template v-for="f in fields" :key="f.key">
        <SettingField v-if="showAdvanced || !f.advanced" :field="f" i18n-base="settings.device" :read="read" :write="write" />
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
      <SettingsRow :label="t('timing.clicks.label')" :help="t('timing.clicks.help')">
        <SbButton variant="outline" size="sm" :loading="metronomeBusy" :disabled="!device.audio.has_sink" @click="toggleMetronome">
          {{ device.timing.calibration_metronome_active ? t('timing.clicks.stop') : t('timing.clicks.start') }}
        </SbButton>
      </SettingsRow>
      <SettingsRow
        :label="t('timing.mic.label')"
        :help="micStep || (canMeasure ? t('timing.mic.help') : microphoneAvailable() ? t('timing.mic.needPeer') : t('timing.mic.needHttps'))"
      >
        <div class="flex items-center gap-2">
          <select
            v-if="peers.length > 1"
            v-model="referenceId"
            :aria-label="t('timing.mic.reference')"
            class="h-8 rounded-(--radius-input) border border-border-strong bg-surface-card px-2 text-sm text-text-primary"
          >
            <option v-for="p in peers" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
          <SbButton variant="outline" size="sm" :disabled="!canMeasure" :loading="!!micStep" @click="measure">
            {{ t('timing.mic.start') }}
          </SbButton>
        </div>
      </SettingsRow>
    </div>

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

    <label class="mt-4 flex items-center gap-2 text-sm text-text-secondary">
      <input v-model="showAdvanced" type="checkbox" class="accent-primary" />
      {{ t('settings.showAdvanced') }}
    </label>
  </div>
</template>
