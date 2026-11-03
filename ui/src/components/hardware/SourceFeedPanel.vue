<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bluetooth, Play, Trash2 } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import type { SourceFailure } from '@/stores/hardware'
import type { SourceCaptureKind, SourceFeed } from '@/api/types'
import { describeSourceCode } from '@/utils/sourceErrors'

/**
 * The Bluetooth side of the source. Music Assistant pairing is a Settings
 * concern, and this panel is only rendered after that pairing succeeded; here
 * the operator pairs a device, sees which paired devices can feed the source
 * and chooses the active one.
 */
const props = withDefaults(
  defineProps<{
    source: SourceFeed
    /** Seconds left in the device pairing window; 0 while it is closed. */
    pairingSecondsLeft?: number
    /** The last command's refusal, with the server's stable code. */
    failure?: SourceFailure | null
    busy?: boolean
  }>(),
  { pairingSecondsLeft: 0, failure: null, busy: false },
)

const emit = defineEmits<{
  pair: []
  stopPairing: []
  use: [mac: string]
  forget: [mac: string]
}>()

const { t } = useI18n()

const state = computed(() => props.source.state)
const stateLabel = computed(() => {
  const keys = {
    off: 'hardware.source.stateOff',
    pairing: 'hardware.source.statePairing',
    waiting: 'hardware.source.stateWaiting',
    streaming: 'hardware.source.stateStreaming',
    error: 'hardware.source.stateError',
  } as const
  return t(keys[state.value])
})
const tone = computed(() => {
  if (state.value === 'streaming') return 'success'
  if (state.value === 'pairing' || state.value === 'waiting') return 'warning'
  if (state.value === 'error') return 'error'
  return 'neutral'
})

const devices = computed(() => props.source.devices ?? [])
const captureKind = computed<SourceCaptureKind>(() => props.source.capture.kind ?? 'unavailable')
const captureKindLabel = computed(() => {
  const keys = {
    pipewire_node: 'hardware.source.capturePipewire',
    pulse_source: 'hardware.source.capturePulse',
    unavailable: 'hardware.source.captureUnavailable',
  } as const
  return t(keys[captureKind.value])
})

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
const errorInfo = computed(() => describeSourceCode(props.source.error, t))
const failureNextStep = computed(() => t('hardware.source.failureNextStep'))
</script>

<template>
  <section
    data-source-panel
    :data-source-state="state"
    aria-labelledby="hardware-source-title"
    class="mb-8 rounded-(--radius-card) border border-border bg-surface-card p-4"
  >
    <header class="flex flex-wrap items-start gap-x-3 gap-y-1">
      <div class="min-w-0 flex-1">
        <h2 id="hardware-source-title" class="text-lg font-semibold text-text-primary">
          {{ t('hardware.source.title') }}
        </h2>
        <p class="mt-1 text-sm text-text-secondary">{{ t('hardware.source.subtitle') }}</p>
      </div>
      <SbBadge data-source-state-badge :tone="tone" size="sm" dot>{{ stateLabel }}</SbBadge>
    </header>

    <h3 class="mt-4 text-sm font-semibold text-text-primary">{{ t('hardware.source.devicesTitle') }}</h3>
    <p v-if="devices.length === 0" data-source-no-devices class="mt-1 text-sm text-text-secondary">
      {{ t('hardware.source.noDevices') }}
    </p>
    <ul v-else data-source-devices class="mt-2 divide-y divide-border rounded-(--radius-card) border border-border">
      <li
        v-for="device in devices"
        :key="device.mac"
        :data-source-device="device.mac"
        :data-source-device-active="String(device.active)"
        class="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2"
      >
        <Bluetooth class="size-4 shrink-0 text-text-secondary" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-medium text-text-primary">{{ device.name || device.mac }}</p>
          <p class="font-mono text-xs text-text-secondary">{{ device.mac }}</p>
        </div>
        <SbBadge v-if="device.active" data-source-device-active-badge size="sm" tone="success">
          {{ t('hardware.source.deviceActive') }}
        </SbBadge>
        <SbBadge v-else-if="device.connected" size="sm" tone="neutral">
          {{ t('hardware.source.deviceConnected') }}
        </SbBadge>
        <SbButton
          v-if="!device.active"
          variant="ghost"
          size="sm"
          :disabled="busy"
          :data-source-use="device.mac"
          @click="emit('use', device.mac)"
        >
          <template #icon-left><Play class="h-3.5 w-3.5" aria-hidden="true" /></template>
          {{ t('hardware.source.deviceUse') }}
        </SbButton>
        <SbButton
          variant="ghost"
          size="sm"
          :disabled="busy"
          :data-source-forget="device.mac"
          @click="emit('forget', device.mac)"
        >
          <template #icon-left><Trash2 class="h-3.5 w-3.5" aria-hidden="true" /></template>
          {{ t('hardware.source.deviceForget') }}
        </SbButton>
      </li>
    </ul>

    <div class="mt-4">
      <div v-if="pairingSecondsLeft > 0" data-source-pairing class="rounded-(--radius-card) border border-info/40 bg-info/10 px-3 py-2">
        <div class="flex flex-wrap items-center gap-2">
          <p class="text-sm text-text-primary">{{ t('hardware.source.pairing', { time: clock(pairingSecondsLeft) }) }}</p>
          <SbButton variant="outline" size="sm" data-source-pairing-stop :disabled="busy" @click="emit('stopPairing')">
            {{ t('hardware.source.stopPairing') }}
          </SbButton>
        </div>
        <p class="mt-1 text-xs text-text-secondary">{{ t('hardware.source.pairingHint') }}</p>
      </div>
      <div v-else class="flex flex-wrap items-center gap-2">
        <SbButton variant="outline" size="sm" data-source-pair :disabled="busy" @click="emit('pair')">
          <template #icon-left><Bluetooth class="h-3.5 w-3.5" aria-hidden="true" /></template>
          {{ t('hardware.source.pair') }}
        </SbButton>
        <span class="text-xs text-text-secondary">{{ t('hardware.source.phoneHint') }}</span>
      </div>
    </div>

    <div
      v-if="errorInfo"
      data-source-error
      role="alert"
      class="mt-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-3 py-2"
    >
      <p class="text-sm font-medium text-text-primary">{{ t('hardware.source.errorTitle') }}</p>
      <p class="mt-1 text-sm text-text-secondary">{{ errorInfo.text }}</p>
      <p class="mt-1 text-sm text-text-secondary">{{ errorInfo.next }}</p>
      <p v-if="source.error" class="mt-1 font-mono text-xs text-text-secondary">{{ source.error }}</p>
    </div>

    <div
      v-if="failure"
      data-source-failure
      role="alert"
      class="mt-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-3 py-2"
    >
      <p class="text-sm font-medium text-text-primary">{{ t('hardware.source.failureTitle') }}</p>
      <p class="mt-1 font-mono text-xs text-text-secondary">
        {{ failure.code }}<template v-if="failure.detail">: {{ failure.detail }}</template>
      </p>
      <p class="mt-1 text-sm text-text-secondary">{{ failureNextStep }}</p>
    </div>

    <dl
      v-if="technicalDetails"
      data-source-technical
      class="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-t border-border pt-3 text-sm"
    >
      <dt class="text-text-secondary">{{ t('hardware.source.capture') }}</dt>
      <dd class="text-text-primary">{{ captureKindLabel }}</dd>
      <dt class="text-text-secondary">{{ t('hardware.source.node') }}</dt>
      <dd class="break-all font-mono text-xs text-text-primary">
        {{ source.capture.node || t('hardware.source.noNode') }}
      </dd>
    </dl>
  </section>
</template>
