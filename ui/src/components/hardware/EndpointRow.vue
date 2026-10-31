<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Pause, Play } from 'lucide-vue-next'
import { SbBadge, SbButton, SbSlider, SbToggle } from '@/kit'
import { activePortAvailability, availabilityTone, endpointStateTone, inputKind, type InputKind } from '@/utils/audioEndpoint'
import type { AudioEndpointSummary } from '@/api/types'

/**
 * One local output or input. The row is the action surface: a sink gets a
 * volume slider, a mute toggle, a default-sink action and suspend/resume; a
 * source gets its kind and, when the audio server cannot hear it, the reason —
 * a row is never left empty.
 */
const props = withDefaults(
  defineProps<{
    endpoint: AudioEndpointSummary
    /** Offer the set-default action (the host has a working audio server). */
    allowDefault?: boolean
    busy?: boolean
  }>(),
  { allowDefault: false, busy: false },
)

const emit = defineEmits<{
  open: [endpoint: AudioEndpointSummary]
  volume: [value: number]
  mute: [muted: boolean]
  default: []
  suspend: []
  resume: []
}>()

const { t } = useI18n()

const isSink = computed(() => props.endpoint.kind === 'sink')
/** The audio server's own answer, refreshed after every write: no session-local copy. */
const isDefault = computed(() => props.endpoint.is_default === true)
const displayName = computed(() => props.endpoint.description || props.endpoint.name)
const state = computed(() => props.endpoint.state || 'unknown')
const stateLabel = computed(() => {
  const keys: Record<string, string> = {
    running: 'hardware.audio.stateRunning',
    idle: 'hardware.audio.stateIdle',
    suspended: 'hardware.audio.stateSuspended',
    unavailable: 'hardware.audio.stateUnavailable',
  }
  return t(keys[state.value] ?? 'hardware.audio.stateUnknown')
})

const availability = computed(() => activePortAvailability(props.endpoint.active_port_available))
const availabilityLabel = computed(() => {
  const keys: Record<string, string> = {
    available: 'hardware.audio.portAvailable',
    unavailable: 'hardware.audio.portUnavailable',
    unknown: 'hardware.audio.portUnknown',
  }
  return availability.value ? t(keys[availability.value]) : ''
})

const localVolume = ref(props.endpoint.volume ?? 0)
watch(
  () => props.endpoint.volume,
  (volume) => {
    if (volume != null) localVolume.value = volume
  },
)

/* A drag is many input events; one command belongs to the level it settles on. */
const VOLUME_DEBOUNCE_MS = 250
let volumeTimer: ReturnType<typeof setTimeout> | null = null

function onVolume(value: number) {
  localVolume.value = value
  if (volumeTimer) clearTimeout(volumeTimer)
  volumeTimer = setTimeout(() => emit('volume', localVolume.value), VOLUME_DEBOUNCE_MS)
}

onBeforeUnmount(() => {
  if (volumeTimer) clearTimeout(volumeTimer)
})

const kind = computed<InputKind>(() => inputKind(props.endpoint))
const kindLabel = computed(() => {
  const keys: Record<InputKind, string> = {
    phone: 'hardware.audio.kindPhone',
    monitor: 'hardware.audio.kindMonitor',
    lineIn: 'hardware.audio.kindLineIn',
    microphone: 'hardware.audio.kindMicrophone',
  }
  return t(keys[kind.value])
})

/** An input that hears nothing says why: an unavailable state, or an unplugged active port. */
const inputReason = computed(() => {
  if (state.value === 'unavailable') return t('hardware.audio.inputUnavailable')
  if (availability.value === 'unavailable') return t('hardware.audio.portUnavailableReason')
  return null
})
</script>

<template>
  <li data-endpoint-row :data-endpoint-kind="endpoint.kind" :data-endpoint-name="endpoint.name" class="px-3 py-3">
    <div class="flex flex-wrap items-start gap-x-2 gap-y-1">
      <div class="min-w-0 flex-1">
        <p class="text-sm font-medium leading-snug text-text-primary">
          <button
            type="button"
            data-endpoint-details
            class="max-w-full break-words text-left underline-offset-2 hover:underline"
            :aria-label="t('hardware.openDetails', { name: displayName })"
            @click="emit('open', endpoint)"
          >
            {{ displayName }}
          </button>
        </p>
        <p class="truncate font-mono text-xs text-text-secondary">{{ endpoint.name }}</p>
      </div>

      <SbBadge data-endpoint-state :tone="endpointStateTone(state)" size="sm" dot>
        {{ stateLabel }}
      </SbBadge>
      <SbBadge v-if="!isSink" data-input-kind :data-kind="kind" tone="info" size="sm">
        {{ kindLabel }}
      </SbBadge>
      <SbBadge v-if="isSink && isDefault" data-default-marker tone="success" size="sm">
        {{ t('hardware.audio.default') }}
      </SbBadge>
    </div>

    <div v-if="endpoint.active_port" class="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-text-secondary">
      <span>{{ t('hardware.audio.activePort') }}:</span>
      <span class="font-mono">{{ endpoint.active_port }}</span>
      <SbBadge
        v-if="availability"
        :data-port-state="availability"
        :tone="availabilityTone(availability)"
        size="sm"
        dot
      >
        {{ availabilityLabel }}
      </SbBadge>
    </div>

    <p v-if="endpoint.in_use_by" class="mt-1.5 text-sm">
      <span class="text-text-secondary">{{ t('hardware.audio.inUse') }}:</span>
      <RouterLink
        data-endpoint-player
        to="/"
        class="ml-1 text-primary-text underline-offset-2 hover:underline"
      >
        {{ endpoint.in_use_by }}
      </RouterLink>
    </p>

    <p v-if="inputReason" data-input-reason class="mt-1.5 text-sm text-warning-text">
      {{ inputReason }}
    </p>

    <div v-if="isSink" class="mt-2.5 flex flex-wrap items-center gap-3">
      <SbSlider
        :model-value="localVolume"
        :min="0"
        :max="100"
        :show-value="false"
        :disabled="busy || endpoint.muted === true"
        :aria-label="t('volume.label', { name: displayName })"
        data-endpoint-volume
        class="min-w-[8rem] flex-1"
        @update:model-value="onVolume"
      />
      <span class="w-9 shrink-0 text-right text-xs tabular-nums text-text-secondary">{{ localVolume }}%</span>

      <SbToggle
        :model-value="endpoint.muted === true"
        :disabled="busy"
        :label="t('hardware.audio.muted')"
        data-mute-toggle
        @update:model-value="emit('mute', $event)"
      />

      <SbButton
        v-if="allowDefault && !isDefault"
        size="sm"
        variant="ghost"
        data-set-default
        :disabled="busy"
        @click="emit('default')"
      >
        {{ t('hardware.audio.setDefault') }}
      </SbButton>

      <SbButton
        v-if="state === 'suspended'"
        size="sm"
        variant="outline"
        data-resume
        :disabled="busy"
        @click="emit('resume')"
      >
        <template #icon-left><Play class="h-3.5 w-3.5" aria-hidden="true" /></template>
        {{ t('hardware.audio.resume') }}
      </SbButton>
      <SbButton
        v-else
        size="sm"
        variant="outline"
        data-suspend
        :disabled="busy"
        @click="emit('suspend')"
      >
        <template #icon-left><Pause class="h-3.5 w-3.5" aria-hidden="true" /></template>
        {{ t('hardware.audio.suspend') }}
      </SbButton>
    </div>
  </li>
</template>
