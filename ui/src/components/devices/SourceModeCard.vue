<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Volume2, VolumeX } from 'lucide-vue-next'
import { SbBadge, SbButton, SbSlider } from '@/kit'
import StatusState from '@/components/StatusState.vue'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { useSourceStore } from '@/stores/source'
import { headsetMicId, sourceToggle } from '@/utils/sourceMode'
import DeviceCardShell from './card/DeviceCardShell.vue'
import { sourceModeCardView, sourceToggleAction } from './card/viewModels'
import SourceCommandError from './SourceCommandError.vue'
import SpeakerActionsMenu from './SpeakerActionsMenu.vue'
import type { Device } from '@/api/types'

/**
 * The same player card, moved to the Sources section: the link now runs the
 * other way, so its identity stays and its playback controls give way to the
 * input's gain, mute and live level. "Back to playback" is the same shell
 * control that brought it here.
 */
const props = defineProps<{ device: Device }>()

const { t } = useI18n()
const bridge = useBridgeStore()
const deviceStore = useDeviceStore()
const notifications = useNotificationStore()
const source = useSourceStore()

const inputId = computed(() => {
  const mac = props.device.bluetooth?.mac
  return mac ? headsetMicId(mac) : null
})
const featureEnabled = computed(() => !!bridge.bridge?.sendspin_source?.enabled)
const input = computed(() => source.status?.inputs?.find((row) => row.id === inputId.value) ?? null)
/** Capture is open exactly while the bridge says the source is streaming. */
const captureOpen = computed(() => source.status?.state === 'streaming' && input.value?.active === true)
/** The multi-second A2DP restore this card is waiting on, if any. */
const transition = computed(() => source.transitionFor(props.device.bluetooth?.mac))
/** The refusal of this card's last source command, shown on the card itself. */
const failure = computed(() => source.failureFor(props.device.bluetooth?.mac))
const view = computed(() => {
  const base = sourceModeCardView(props.device, source.status?.state ?? undefined, transition.value, captureOpen.value)
  const primary = sourceToggleAction(
    'back-to-playback',
    sourceToggle(props.device, {
      featureEnabled: featureEnabled.value,
      mode: 'source',
      switching: transition.value !== null,
    }),
  )
  return primary ? { ...base, primary } : base
})

const muted = computed(() => input.value?.muted === true)
const gain = ref(input.value?.volume ?? 100)
const level = computed(() => {
  const id = inputId.value
  return id ? (source.levelFor(id)?.level ?? 0) : 0
})
const levelPercent = computed(() => Math.round(Math.min(1, Math.max(0, level.value)) * 100))

/** The slider is optimistic in the store; the card only debounces a drag. */
let gainTimer: ReturnType<typeof setTimeout> | null = null
function onGain(value: number) {
  gain.value = value
  if (gainTimer) clearTimeout(gainTimer)
  gainTimer = setTimeout(() => {
    gainTimer = null
    void run(() => source.setGain(value))
  }, 250)
}

function onMute() {
  void run(() => source.setMuted(!muted.value))
}

async function run(action: () => Promise<unknown>) {
  try {
    await action()
  } catch {
    notifications.error(t('devices.sources.commandFailed'))
  }
}

function onAction(id: string) {
  if (id === 'back-to-playback') {
    void run(() => source.release(props.device.bluetooth?.mac))
  }
}

/** Five seconds of a real zero while the capture is open is "check the mute". */
const noSignal = ref(false)
let zeroTimer: ReturnType<typeof setTimeout> | null = null
function clearZeroTimer() {
  if (zeroTimer) clearTimeout(zeroTimer)
  zeroTimer = null
}
function watchSilence() {
  if (!captureOpen.value || level.value > 0.001) {
    clearZeroTimer()
    noSignal.value = false
    return
  }
  if (zeroTimer === null && !noSignal.value) {
    zeroTimer = setTimeout(() => {
      zeroTimer = null
      noSignal.value = true
    }, 5000)
  }
}
watch([captureOpen, level], watchSilence, { immediate: true })
watch(
  () => input.value?.volume,
  (volume) => {
    if (typeof volume === 'number') gain.value = volume
  },
)

const restoreFailed = computed(
  () =>
    source.status?.error === 'headset_a2dp_restore_failed' ||
    props.device.health?.code === 'a2dp_unavailable',
)
const restoreFromSource = computed(() => source.status?.error === 'headset_a2dp_restore_failed')

onMounted(() => {
  if (inputId.value) void source.ensureLevels()
})
onBeforeUnmount(clearZeroTimer)
</script>

<template>
  <DeviceCardShell :view="view" :data-source-mode-card="''" @action="onAction">
    <template #menu><SpeakerActionsMenu :device="device" /></template>
    <template #badges>
      <SbBadge size="sm" tone="info">{{ t('devices.sources.sourceMode') }}</SbBadge>
      <StatusState kind="bluetooth" :status="device.links.bluetooth" only-if-not-ok />
    </template>

    <template #context>
      <p v-if="!captureOpen" data-source-waiting class="mb-2 text-sm text-text-secondary">
        {{ t('devices.sources.waitingForStream') }}
      </p>
      <div class="flex items-center gap-2">
          <button
            type="button"
            class="-ml-2 inline-flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
            :class="{ 'text-warning-text': muted }"
            :aria-label="muted ? t('devices.sources.unmute') : t('devices.sources.mute')"
            data-source-mute
            @click="onMute"
          >
            <VolumeX v-if="muted" class="size-4" />
            <Volume2 v-else class="size-4" />
          </button>
          <SbSlider
            :model-value="gain"
            :min="0"
            :max="100"
            :show-value="false"
            :aria-label="t('devices.sources.gain')"
            data-source-gain
            class="min-w-0 flex-1"
            @update:model-value="onGain"
          />
          <span class="w-9 shrink-0 text-right text-xs tabular-nums text-text-secondary">{{ gain }}%</span>
        </div>
        <div
          role="meter"
          :aria-label="t('devices.sources.level')"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="levelPercent"
          data-source-level
          class="mt-2 h-2 overflow-hidden rounded-full bg-surface-secondary"
        >
          <div class="h-full rounded-full bg-success transition-[width] duration-150" :style="{ width: `${levelPercent}%` }" />
        </div>
        <p v-if="noSignal" data-source-no-signal class="mt-2 text-xs text-warning-text">
          {{ t('devices.sources.noSignal') }}
        </p>
    </template>

    <template #extra>
      <SourceCommandError v-if="failure" :failure="failure" />
      <div
        v-if="restoreFailed"
        data-source-a2dp-failure
        class="relative z-10 rounded-lg border border-warning-border bg-warning-soft p-3 text-sm"
      >
        <p class="font-medium text-text-primary">
          {{ t(restoreFromSource ? 'hardware.source.errorHeadsetA2dp' : 'device.a2dpUnavailable') }}
        </p>
        <p class="mt-1 text-text-secondary">
          {{ t(restoreFromSource ? 'hardware.source.errorHeadsetA2dpNext' : 'device.a2dpUnavailableHint') }}
        </p>
        <SbButton
          data-source-a2dp-retry
          class="mt-2"
          size="sm"
          variant="outline"
          @click="run(() => deviceStore.retryA2dpProfile(device.id))"
        >
          {{ t('device.retryA2dp') }}
        </SbButton>
      </div>
    </template>
  </DeviceCardShell>
</template>
