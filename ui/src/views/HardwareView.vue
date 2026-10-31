<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, RefreshCw } from 'lucide-vue-next'
import { SbBadge, SbButton, SbEmptyState, SbSpinner } from '@/kit'
import ControllerCard from '@/components/hardware/ControllerCard.vue'
import BtDeviceCard from '@/components/hardware/BtDeviceCard.vue'
import AudioCardPanel from '@/components/hardware/AudioCardPanel.vue'
import AudioCaptureNotice from '@/components/hardware/AudioCaptureNotice.vue'
import EndpointRow from '@/components/hardware/EndpointRow.vue'
import CapabilityBanner from '@/components/hardware/CapabilityBanner.vue'
import EntityDrawer from '@/components/hardware/EntityDrawer.vue'
import { useHardwareStore } from '@/stores/hardware'
import { useBridgeStore } from '@/stores/bridge'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useNotificationStore } from '@/stores/notifications'
import { confirmAction, undoable } from '@/composables/useActionPolicy'
import { forgetBtDevice, setAdapterPower } from '@/api/bluetooth'
import { ApiError } from '@/api/client'
import { speakerName } from '@/utils/speakerName'
import type {
  AudioCardSummary,
  AudioEndpointRef,
  AudioEndpointSummary,
  BtDeviceSummary,
  ControllerSummary,
} from '@/api/types'

const { t } = useI18n()
const store = useHardwareStore()
const bridge = useBridgeStore()
const bt = useBluetoothStore()
const notifications = useNotificationStore()

onMounted(() => store.connect())
onUnmounted(() => store.disconnect())

const snapshot = computed(() => store.snapshot)
const controllers = computed(() => snapshot.value?.controllers ?? [])
const devices = computed(() => snapshot.value?.bluetooth_devices ?? [])
const capabilities = computed(() => snapshot.value?.capabilities ?? null)

/**
 * Four different answers, never the same screen: a bridge without the route,
 * a host that cannot read Bluetooth, a host whose read failed, and "nothing is
 * paired" (a truth, not a failure). Only the last one is empty.
 */
const state = computed<'loading' | 'unsupported' | 'unavailable' | 'empty' | 'ready'>(() => {
  if (store.unsupported) return 'unsupported'
  if (store.loading && !snapshot.value) return 'loading'
  if (!snapshot.value) return store.error ? 'unavailable' : 'loading'
  if (!snapshot.value.capabilities.bluetooth) return 'unavailable'
  return devices.value.length === 0 ? 'empty' : 'ready'
})

/** The code the banner names; D-Bus first because it is the deeper cause. */
const unavailableReason = computed(() => {
  const caps = capabilities.value
  if (!caps) return 'hardware_unavailable'
  return caps.reasons.dbus ?? caps.reasons.bluetooth ?? 'hardware_unavailable'
})

const unavailableDetail = computed(() => {
  const degraded = snapshot.value?.degraded ?? []
  const section = degraded.find((entry) => entry.code === unavailableReason.value)
  return section?.detail ?? (snapshot.value ? undefined : store.error ?? undefined)
})

/**
 * D-Bus can be down while bluetoothctl still answers: that read is real but
 * partial, so the lists stay and the banner explains the fallback.
 */
const degradedReason = computed(() =>
  capabilities.value && capabilities.value.bluetooth && !capabilities.value.dbus ? 'dbus_unavailable' : null,
)

const degradedDetail = computed(() => {
  const code = degradedReason.value
  if (!code) return undefined
  return snapshot.value?.degraded.find((entry) => entry.code === code)?.detail
})

// ---------------------------------------------------------------------------
// Audio group
// ---------------------------------------------------------------------------

const audio = computed(() => store.audio)
const audioCards = computed(() => audio.value.cards)
const sinks = computed(() => audio.value.sinks)
const sources = computed(() => audio.value.sources)

/** An older bridge omits ``audio`` entirely: that is unsupported, not "no audio". */
const audioReported = computed(() => store.audioReported)

/** Whole-section failure: the audio server did not answer, so the lists mean nothing. */
const audioUnavailable = computed(() => {
  const entry = store.degraded.find((item) => item.section === 'audio' && item.code === 'pulse_unavailable')
  if (entry) return entry
  if (capabilities.value?.audio_server === 'none') {
    return { section: 'audio', code: 'pulse_unavailable', detail: '' }
  }
  return null
})

/** The phone capture is a different gap from a missing local input; it gets the reason. */
const captureUnavailable = computed(
  () => store.degraded.find((item) => item.section === 'audio' && item.code === 'capture_unavailable') ?? null,
)

/** A truthful "nothing is there": the audio server answered, and listed nothing. */
const audioEmpty = computed(
  () =>
    audioReported.value &&
    !audioUnavailable.value &&
    !captureUnavailable.value &&
    audioCards.value.length === 0 &&
    sinks.value.length === 0 &&
    sources.value.length === 0,
)

/** Nothing local, and the only input is a phone the bridge cannot capture. */
const audioCaptureOnly = computed(
  () =>
    audioReported.value &&
    !audioUnavailable.value &&
    !!captureUnavailable.value &&
    audioCards.value.length === 0 &&
    sinks.value.length === 0 &&
    sources.value.length === 0,
)

function endpointLabel(endpoint: { description?: string; name: string }) {
  return endpoint.description || endpoint.name
}

function commandFailure(e: unknown) {
  return e instanceof ApiError ? `${e.code}: ${e.message}` : t('common.error')
}

function commandFailed(e: unknown, name: string) {
  notifications.error(t('hardware.audio.commandFailed', { name, reason: commandFailure(e) }))
}

/** The slider debounces in the row; one undo toast belongs to the level it settles on. */
function onVolume(endpoint: AudioEndpointSummary, value: number) {
  const previous = endpoint.volume ?? 0
  if (value === previous) return
  const name = endpointLabel(endpoint)
  void undoable(
    'audio.volume',
    () => store.setVolume(endpoint.name, value),
    () => store.setVolume(endpoint.name, previous),
    {
      doneKey: 'undo.audioVolume',
      name: `${name} (${value}%)`,
      failure: (e) => t('hardware.audio.commandFailed', { name, reason: commandFailure(e) }),
    },
  )
}

function onMute(endpoint: AudioEndpointSummary, muted: boolean) {
  const name = endpointLabel(endpoint)
  void undoable('audio.mute', () => store.setMute(endpoint.name, muted), () => store.setMute(endpoint.name, !muted), {
    doneKey: muted ? 'undo.audioMuted' : 'undo.audioUnmuted',
    name,
    failure: (e) => t('hardware.audio.commandFailed', { name, reason: commandFailure(e) }),
  })
}

const audioBusy = ref<string | null>(null)

async function onDefault(endpoint: AudioEndpointSummary) {
  const name = endpointLabel(endpoint)
  const ok = await confirmAction('audio.defaultSink', {
    title: t('hardware.audio.defaultConfirmTitle', { name }),
    consequences: [t('hardware.audio.defaultConsequence', { name })],
    confirmLabel: t('hardware.audio.makeDefault'),
  })
  if (!ok) return
  audioBusy.value = endpoint.name
  try {
    await store.setDefault(endpoint.name)
    notifications.success(t('hardware.audio.defaultSet', { name }))
  } catch (e) {
    commandFailed(e, name)
  } finally {
    audioBusy.value = null
  }
}

async function onSuspend(endpoint: AudioEndpointSummary) {
  const name = endpointLabel(endpoint)
  const ok = await confirmAction('audio.suspend', {
    title: t('hardware.audio.suspendConfirmTitle', { name }),
    consequences: [t('hardware.audio.suspendConsequence', { name })],
    confirmLabel: t('hardware.audio.suspend'),
  })
  if (!ok) return
  audioBusy.value = endpoint.name
  try {
    await store.suspend(endpoint.name)
    notifications.success(t('hardware.audio.suspended', { name }))
  } catch (e) {
    commandFailed(e, name)
  } finally {
    audioBusy.value = null
  }
}

/** Undo of a resume is a suspend, and the user just chose to go back; no second dialog. */
function onResume(endpoint: AudioEndpointSummary) {
  const name = endpointLabel(endpoint)
  void undoable('audio.resume', () => store.resume(endpoint.name), () => store.suspend(endpoint.name), {
    doneKey: 'undo.audioResumed',
    name,
    failure: (e) => t('hardware.audio.commandFailed', { name, reason: commandFailure(e) }),
  })
}

const busy = ref<string | null>(null)

/** Every speaker on this adapter loses its link when the adapter is powered off. */
function consequences(controller: ControllerSummary) {
  const names = bridge.devices
    .filter((device) => device.bluetooth?.adapter.mac === controller.mac)
    .map((device) => speakerName(device.name))
    .join(', ')
  return names ? [t('consequence.adapterDisconnects', { names })] : [t('adapter.confirmNone')]
}

async function togglePower(controller: ControllerSummary) {
  const name = controller.alias || controller.name || controller.id
  if (controller.powered) {
    const ok = await confirmAction('adapter.powerOff', {
      title: t('adapter.confirmOffTitle', { adapter: name }),
      consequences: consequences(controller),
      confirmLabel: t('adapter.turnOff'),
    })
    if (!ok) return
  }
  busy.value = controller.id
  try {
    // Commands take the MAC: hciN can be synthetic (design, "Controller").
    await setAdapterPower(controller.mac, !controller.powered)
    await Promise.all([store.refresh(), bridge.refreshAdapters()])
    notifications.success(t('adapter.powerToggled'))
  } catch {
    notifications.error(t('adapter.powerFailed'))
  } finally {
    busy.value = null
  }
}

const drawerOpen = ref(false)

type Selection =
  | { kind: 'controller' | 'device'; id: string }
  | { kind: 'audio-card' | 'audio-sink' | 'audio-source'; id: string; label: string }

const selected = ref<Selection | null>(null)

const selectedController = computed(() =>
  selected.value?.kind === 'controller'
    ? controllers.value.find((controller) => controller.id === selected.value!.id) ?? null
    : null,
)
const selectedDevice = computed(() =>
  selected.value?.kind === 'device'
    ? devices.value.find((device) => device.mac === selected.value!.id) ?? null
    : null,
)
const selectedAudio = computed(() => {
  const entry = selected.value
  if (!entry) return null
  if (entry.kind !== 'audio-card' && entry.kind !== 'audio-sink' && entry.kind !== 'audio-source') return null
  const exists =
    entry.kind === 'audio-card'
      ? audioCards.value.some((card) => card.name === entry.id)
      : entry.kind === 'audio-sink'
        ? sinks.value.some((sink) => sink.name === entry.id)
        : entry.kind === 'audio-source'
          ? sources.value.some((source) => source.name === entry.id)
          : false
  if (!exists) return null
  return { kind: entry.kind, name: entry.id, label: entry.label }
})

const deviceBusy = ref<string | null>(null)

function deviceName(device: BtDeviceSummary) {
  return device.alias || device.name || device.mac
}

/** The config names an adapter by hciN; the snapshot knows the MACs holding the bond. */
function controllerIdFor(device: BtDeviceSummary) {
  for (const mac of device.controller_macs) {
    const controller = controllers.value.find((entry) => entry.mac.toUpperCase() === mac.toUpperCase())
    if (controller) return controller.id
  }
  return ''
}

/** Make an already-paired device a player: the config grows and the bridge starts it. */
async function addSpeaker(device: BtDeviceSummary) {
  const name = deviceName(device)
  deviceBusy.value = `add:${device.mac}`
  try {
    const added = await bt.addToBridge(device.mac, name, controllerIdFor(device))
    if (!added) {
      notifications.error(t('hardware.addSpeakerFailed', { name }))
      return
    }
    await store.refresh()
    notifications.success(t('addSpeaker.addedTitle', { name }))
  } catch {
    notifications.error(t('hardware.addSpeakerFailed', { name }))
  } finally {
    deviceBusy.value = null
  }
}

/** The bond can back a player; its id is a fact from the read, not a client-side guess. */
function forgetConsequences(device: BtDeviceSummary) {
  const lines = [t('device.actions.forgetConfirm')]
  if (device.player_id) lines.push(t('hardware.forgetPlayer', { player: device.player_id }))
  return lines
}

async function forgetBond(device: BtDeviceSummary) {
  const name = deviceName(device)
  const ok = await confirmAction('bluetooth.forgetBond', {
    title: t('device.actions.forgetTitle', { name }),
    consequences: forgetConsequences(device),
    confirmLabel: t('device.actions.forget'),
  })
  if (!ok) return
  deviceBusy.value = `forget:${device.mac}`
  try {
    await forgetBtDevice(device.mac)
    await store.refresh()
    notifications.success(t('hardware.forgotBond', { name }))
  } catch (e) {
    // A 409 carries the server's own code and message (e.g. bond_in_use_by_player).
    const reason = e instanceof ApiError ? `${e.code}: ${e.message}` : e instanceof Error ? e.message : String(e)
    notifications.error(t('hardware.forgetFailed', { reason }))
  } finally {
    deviceBusy.value = null
  }
}

function openController(id: string) {
  selected.value = { kind: 'controller', id }
  drawerOpen.value = true
}

function openDevice(mac: string) {
  selected.value = { kind: 'device', id: mac }
  drawerOpen.value = true
}

function openAudioCard(card: AudioCardSummary) {
  selected.value = { kind: 'audio-card', id: card.name, label: card.name }
  drawerOpen.value = true
}

function openAudioEndpoint(kind: 'audio-sink' | 'audio-source', endpoint: { name: string; description?: string }) {
  selected.value = { kind, id: endpoint.name, label: endpointLabel(endpoint) }
  drawerOpen.value = true
}

function openAudioRef(ref: AudioEndpointRef) {
  const list = ref.kind === 'sink' ? sinks.value : sources.value
  const found = list.find((entry) => entry.name === ref.name)
  openAudioEndpoint(ref.kind === 'sink' ? 'audio-sink' : 'audio-source', found ?? { name: ref.name })
}

/* The drawer belongs to the selected entity: a refresh that drops it closes the drawer. */
watch([selectedController, selectedDevice, selectedAudio], ([controller, device, audioEntry]) => {
  if (selected.value && !controller && !device && !audioEntry) drawerOpen.value = false
})
</script>

<template>
  <div :data-state="state">
    <div class="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div class="min-w-0">
        <h1 class="text-2xl font-semibold tracking-tight text-text-primary">{{ t('nav.hardware') }}</h1>
        <p class="mt-1 text-sm text-text-secondary">{{ t('hardware.subtitle') }}</p>
      </div>
      <div class="flex items-center gap-2">
        <SbBadge v-if="snapshot?.stale" data-hardware-stale tone="warning" size="sm">
          {{ t('hardware.stale') }}
        </SbBadge>
        <SbButton size="sm" variant="outline" :loading="store.refreshing" data-hardware-refresh @click="store.refresh({ manual: true })">
          <template #icon-left>
            <RefreshCw class="h-3.5 w-3.5" aria-hidden="true" />
          </template>
          {{ t('hardware.refresh') }}
        </SbButton>
      </div>
    </div>

    <div
      v-if="store.error && snapshot"
      role="alert"
      class="mb-4 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
    >
      {{ store.error }}
    </div>

    <div v-if="state === 'loading'" class="flex items-center gap-2 text-sm text-text-secondary" aria-busy="true">
      <SbSpinner :label="t('common.loading')" />
      {{ t('common.loading') }}
    </div>

    <section v-else-if="state === 'unsupported'" data-unsupported class="rounded-(--radius-card) border border-border bg-surface-card p-6">
      <h2 class="text-lg font-semibold text-text-primary">{{ t('hardware.unsupported.title') }}</h2>
      <p class="mt-2 max-w-2xl text-sm text-text-secondary">{{ t('hardware.unsupported.body') }}</p>
    </section>

    <CapabilityBanner
      v-else-if="state === 'unavailable'"
      :reason="unavailableReason"
      :detail="unavailableDetail"
    />

    <template v-else-if="snapshot">
      <CapabilityBanner v-if="degradedReason" :reason="degradedReason" :detail="degradedDetail" />

      <section class="mb-8" data-controllers-section>
        <h2 class="mb-3 text-lg font-semibold text-text-primary">{{ t('hardware.controllers') }}</h2>
        <div
          v-if="controllers.length"
          class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))] gap-4"
        >
          <ControllerCard
            v-for="controller in controllers"
            :key="controller.id"
            :controller="controller"
            :busy="busy === controller.id"
            @open="openController"
            @toggle-power="togglePower"
          />
        </div>
        <SbEmptyState v-else :title="t('hardware.noControllers')" />
      </section>

      <section class="mb-8" data-devices-section>
        <h2 class="mb-3 text-lg font-semibold text-text-primary">{{ t('hardware.devices') }}</h2>
        <ul
          v-if="devices.length"
          class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,320px),1fr))] gap-4"
        >
          <li v-for="device in devices" :key="device.mac" class="min-w-0">
            <BtDeviceCard
              :device="device"
              :adding="deviceBusy === `add:${device.mac}`"
              :forgetting="deviceBusy === `forget:${device.mac}`"
              @open="openDevice"
              @add="addSpeaker"
              @forget="forgetBond"
            />
          </li>
        </ul>
        <SbEmptyState v-else :title="t('hardware.noDevices')" />
      </section>

      <section data-audio-section>
        <h2 class="mb-3 text-lg font-semibold text-text-primary">{{ t('hardware.audio.title') }}</h2>

        <!-- An older bridge omits `audio`: unsupported, not a host without audio. -->
        <section
          v-if="!audioReported"
          data-audio-not-reported
          role="status"
          class="rounded-(--radius-card) border border-border bg-surface-secondary px-4 py-3"
        >
          <h3 class="text-sm font-medium text-text-primary">{{ t('hardware.audio.notReportedTitle') }}</h3>
          <p class="mt-1 max-w-2xl text-sm text-text-secondary">{{ t('hardware.audio.notReportedBody') }}</p>
        </section>

        <!-- No audio server at all: the lists would be a lie, so only the reason shows. -->
        <section
          v-else-if="audioUnavailable"
          data-audio-unavailable
          role="status"
          class="flex items-start gap-3 rounded-(--radius-card) border border-warning/40 bg-warning/10 px-4 py-3"
        >
          <AlertTriangle class="mt-0.5 size-5 shrink-0 text-warning-text" aria-hidden="true" />
          <div class="min-w-0">
            <h3 class="text-sm font-medium text-text-primary">{{ t('hardware.audio.unavailableTitle') }}</h3>
            <p class="mt-1 text-sm text-text-secondary">{{ t('hardware.audio.unavailableBody') }}</p>
            <p v-if="audioUnavailable.detail" class="mt-1 text-sm text-text-secondary">{{ audioUnavailable.detail }}</p>
            <p class="mt-1 font-mono text-xs text-text-secondary">
              {{ t('hardware.unavailable.reason', { reason: audioUnavailable.code }) }}
            </p>
          </div>
        </section>

        <!-- The audio server answered and listed nothing: a truth, so the empty state. -->
        <SbEmptyState
          v-else-if="audioEmpty"
          data-audio-empty
          :title="t('hardware.audio.noDevices')"
          :description="t('hardware.audio.noDevicesBody')"
        />

        <!-- Nothing local and no capture: the capture reason is the only real answer. -->
        <div v-else-if="audioCaptureOnly">
          <AudioCaptureNotice :detail="captureUnavailable?.detail" :code="captureUnavailable?.code" />
        </div>

        <template v-else>
          <h3 class="mb-2 text-sm font-semibold uppercase tracking-wider text-text-secondary">
            {{ t('hardware.audio.cards') }}
          </h3>
          <div
            v-if="audioCards.length"
            class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,320px),1fr))] gap-4"
          >
            <AudioCardPanel
              v-for="card in audioCards"
              :key="card.name"
              :card="card"
              @open="openAudioCard"
              @open-endpoint="openAudioRef"
            />
          </div>
          <p v-else class="text-sm text-text-secondary">{{ t('hardware.audio.noCards') }}</p>

          <h3 class="mb-2 mt-5 text-sm font-semibold uppercase tracking-wider text-text-secondary">
            {{ t('hardware.audio.outputs') }}
          </h3>
          <ul
            v-if="sinks.length"
            data-audio-outputs
            class="divide-y divide-border rounded-(--radius-card) border border-border"
          >
            <EndpointRow
              v-for="sink in sinks"
              :key="sink.name"
              :endpoint="sink"
              :default-sink="store.defaultSink === sink.name"
              :allow-default="capabilities?.default_sink ?? false"
              :busy="audioBusy === sink.name"
              @open="openAudioEndpoint('audio-sink', $event)"
              @volume="onVolume(sink, $event)"
              @mute="onMute(sink, $event)"
              @default="onDefault(sink)"
              @suspend="onSuspend(sink)"
              @resume="onResume(sink)"
            />
          </ul>
          <p v-else data-audio-no-outputs class="text-sm text-text-secondary">{{ t('hardware.audio.noOutputs') }}</p>

          <h3 class="mb-2 mt-5 text-sm font-semibold uppercase tracking-wider text-text-secondary">
            {{ t('hardware.audio.inputs') }}
          </h3>
          <ul
            v-if="sources.length"
            data-audio-inputs
            class="divide-y divide-border rounded-(--radius-card) border border-border"
          >
            <EndpointRow
              v-for="source in sources"
              :key="source.name"
              :endpoint="source"
              @open="openAudioEndpoint('audio-source', $event)"
            />
          </ul>
          <p v-else-if="!captureUnavailable" data-audio-no-inputs class="text-sm text-text-secondary">
            {{ t('hardware.audio.noInputs') }}
          </p>
          <div v-if="captureUnavailable" class="mt-3">
            <AudioCaptureNotice :detail="captureUnavailable.detail" :code="captureUnavailable.code" />
          </div>
        </template>
      </section>
    </template>

    <EntityDrawer
      v-model:open="drawerOpen"
      :kind="selected?.kind ?? 'controller'"
      :controller="selectedController"
      :device="selectedDevice"
      :audio-name="selectedAudio?.name ?? null"
      :audio-label="selectedAudio?.label ?? null"
      :busy="busy === selectedController?.id"
      @toggle-power="togglePower"
    />
  </div>
</template>
