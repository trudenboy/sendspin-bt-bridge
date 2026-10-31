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
import SourceFeedPanel from '@/components/hardware/SourceFeedPanel.vue'
import CapabilityBanner from '@/components/hardware/CapabilityBanner.vue'
import EntityDrawer from '@/components/hardware/EntityDrawer.vue'
import AddSpeakerDialog from '@/components/bluetooth/AddSpeakerDialog.vue'
import { useHardwareStore } from '@/stores/hardware'
import { useBridgeStore } from '@/stores/bridge'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useNotificationStore } from '@/stores/notifications'
import { confirmAction, undoable } from '@/composables/useActionPolicy'
import { connectBtDevice, disconnectBtDevice, forgetBtDevice, setAdapterPower } from '@/api/bluetooth'
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

// ---------------------------------------------------------------------------
// Source strip
// ---------------------------------------------------------------------------

/** Every player, so a relay reads as a speaker and the sink refusal names one. */
const sourcePlayers = computed(() =>
  bridge.devices.map((device) => ({ id: device.id, name: speakerName(device.name) || device.id })),
)

/** The name the paired device carries, when the Bluetooth list has the streaming phone. */
const sourcePhoneName = computed(() => {
  const mac = store.source?.phone_mac
  if (!mac) return null
  const device = devices.value.find((entry) => entry.mac.toUpperCase() === mac.toUpperCase())
  return device ? speakerName(device.alias || device.name) || mac : null
})

const sourceCommandFailure = (e: unknown) => t('hardware.source.commandFailed', { reason: commandFailure(e) })

function onSourceStart() {
  void undoable('source.start', () => store.startSource(), () => store.stopSource(), {
    doneKey: 'undo.sourceStarted',
    name: '',
    failure: sourceCommandFailure,
  })
}

/** Stopping interrupts whatever the relays are playing, so it asks and names them. */
async function onSourceStop() {
  const names = (store.source?.relays ?? []).map(
    (id) => sourcePlayers.value.find((player) => player.id === id)?.name ?? id,
  )
  const ok = await confirmAction('source.stop', {
    title: t('hardware.source.stopConfirmTitle'),
    consequences: [
      names.length ? t('hardware.source.stopConsequence', { players: names.join(', ') }) : t('hardware.source.stopNoRelays'),
    ],
    confirmLabel: t('hardware.source.stop'),
  })
  if (!ok) return
  try {
    await store.stopSource()
    notifications.success(t('hardware.source.stopped'))
  } catch (e) {
    notifications.error(sourceCommandFailure(e))
  }
}

function onSourcePair() {
  void undoable('source.pairing', () => store.startPairingWindow(), () => store.stopPairingWindow(), {
    doneKey: 'undo.sourcePairingOpen',
    name: '',
    failure: sourceCommandFailure,
    // A refused window answers 200 with its reason in the body; the strip shows
    // it inline, so there is no success toast and no Undo for a window that never opened.
    offerUndo: (result) => !(result as { error?: string | null } | null)?.error,
  })
}

function onSourceStopPairing() {
  // Undo restores the time that was left, not a brand-new 120 s window.
  const remaining = store.pairingSecondsLeft
  void undoable(
    'source.pairingStop',
    () => store.stopPairingWindow(),
    () => store.startPairingWindow(Math.max(1, Math.ceil(remaining))),
    {
      doneKey: 'undo.sourcePairingClosed',
      name: '',
      failure: sourceCommandFailure,
    },
  )
}

function onSourceRelay(playerId: string, enabled: boolean) {
  const name = sourcePlayers.value.find((player) => player.id === playerId)?.name ?? playerId
  void undoable(
    'source.relay',
    () => store.setSourcePlayer(playerId, enabled),
    () => store.setSourcePlayer(playerId, !enabled),
    {
      doneKey: enabled ? 'undo.sourceAttached' : 'undo.sourceDetached',
      name,
      failure: sourceCommandFailure,
    },
  )
}

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

/** Link work is per MAC: one device connecting must not steal another's spinner or block it. */
const deviceBusy = ref<Record<string, string>>({})

function busyFor(mac: string) {
  return deviceBusy.value[mac] ?? null
}

function setDeviceBusy(mac: string, action: string) {
  deviceBusy.value = { ...deviceBusy.value, [mac]: action }
}

function clearDeviceBusy(mac: string) {
  const rest = { ...deviceBusy.value }
  delete rest[mac]
  deviceBusy.value = rest
}

/** A refused forget, kept per MAC: the server's sentence and the player it names. */
interface BondRefusal {
  playerId: string | null
  remediation: string | null
  detail: string | null
}
const bondRefusals = ref<Record<string, BondRefusal>>({})

function refusalFor(mac: string) {
  return bondRefusals.value[mac] ?? null
}

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
  if (busyFor(device.mac)) return
  const name = deviceName(device)
  setDeviceBusy(device.mac, 'add')
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
    clearDeviceBusy(device.mac)
  }
}

/** The server's stable code, with the two refusals BlueZ words for people. */
function btReason(e: unknown) {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : t('common.error')
  if (e.code === 'connect_refused' || e.code === 'disconnect_refused') {
    return t('hardware.btRefused', { code: e.code })
  }
  if (e.code === 'bluetooth_unavailable' || e.code === 'disconnect_failed') {
    return t('hardware.btUnavailable', { code: e.code })
  }
  return `${e.code}: ${e.message}`
}

/**
 * A link command, then a fresh read; the undo is the opposite command. Neither
 * side is destructive, so the policy runs them at once.
 */
async function runLinkCommand(
  id: 'bluetooth.connect' | 'bluetooth.disconnect',
  device: BtDeviceSummary,
  forward: () => Promise<unknown>,
  backward: () => Promise<unknown>,
  doneKey: string,
  failureKey: string,
) {
  const name = deviceName(device)
  if (busyFor(device.mac)) return
  setDeviceBusy(device.mac, id === 'bluetooth.connect' ? 'connect' : 'disconnect')
  try {
    await undoable(id, forward, backward, {
      doneKey,
      name,
      failure: (e) => t(failureKey, { name, reason: btReason(e) }),
    })
  } finally {
    clearDeviceBusy(device.mac)
  }
}

function connectDevice(device: BtDeviceSummary) {
  void runLinkCommand(
    'bluetooth.connect',
    device,
    async () => {
      // The server picks the controller holding the bond; no client-side guess.
      await connectBtDevice(device.mac)
      await store.refresh()
    },
    async () => {
      await disconnectBtDevice(device.mac)
      await store.refresh()
    },
    'undo.btConnected',
    'hardware.connectFailed',
  )
}

function disconnectDevice(device: BtDeviceSummary) {
  void runLinkCommand(
    'bluetooth.disconnect',
    device,
    async () => {
      await disconnectBtDevice(device.mac)
      await store.refresh()
    },
    async () => {
      await connectBtDevice(device.mac)
      await store.refresh()
    },
    'undo.btDisconnected',
    'hardware.disconnectFailed',
  )
}

/** The voice of the pairing flow is the dialog's; the page only refreshes its inventory. */
const pairOpen = ref(false)

function onPaired() {
  void store.refresh()
}

/** The bond can back a player; its id is a fact from the read, not a client-side guess. */
function forgetConsequences(device: BtDeviceSummary) {
  const lines = [t('device.actions.forgetConfirm')]
  if (device.player_id) lines.push(t('hardware.forgetPlayer', { player: device.player_id }))
  return lines
}

async function forgetBond(device: BtDeviceSummary) {
  if (busyFor(device.mac)) return
  const name = deviceName(device)
  const ok = await confirmAction('bluetooth.forgetBond', {
    title: t('device.actions.forgetTitle', { name }),
    consequences: forgetConsequences(device),
    confirmLabel: t('device.actions.forget'),
  })
  if (!ok) return
  setDeviceBusy(device.mac, 'forget')
  // A retry starts clean: the previous refusal is about the state before this attempt.
  const rest = { ...bondRefusals.value }
  delete rest[device.mac]
  bondRefusals.value = rest
  try {
    await forgetBtDevice(device.mac)
    await store.refresh()
    notifications.success(t('hardware.forgotBond', { name }))
  } catch (e) {
    if (e instanceof ApiError && e.code === 'bond_in_use_by_player') {
      // The server names the player and the next step; the page repeats both
      // instead of guessing which player owns the bond.
      bondRefusals.value = {
        ...bondRefusals.value,
        [device.mac]: {
          playerId: typeof e.problem.player_id === 'string' ? e.problem.player_id : null,
          remediation: typeof e.problem.remediation === 'string' ? e.problem.remediation : null,
          detail: e.problem.detail ?? null,
        },
      }
      return
    }
    const reason = e instanceof ApiError ? `${e.code}: ${e.message}` : e instanceof Error ? e.message : String(e)
    notifications.error(t('hardware.forgetFailed', { reason }))
  } finally {
    clearDeviceBusy(device.mac)
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

      <SourceFeedPanel
        :source="store.source"
        :pairing-seconds-left="store.pairingSecondsLeft"
        :players="sourcePlayers"
        :phone-name="sourcePhoneName"
        :failure="store.sourceError"
        :busy="store.sourceBusy"
        @start="onSourceStart"
        @stop="onSourceStop"
        @pair="onSourcePair"
        @stop-pairing="onSourceStopPairing"
        @relay="onSourceRelay"
      />

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
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-lg font-semibold text-text-primary">{{ t('hardware.devices') }}</h2>
          <SbButton size="sm" variant="outline" data-pair-device @click="pairOpen = true">
            {{ t('hardware.pairDevice') }}
          </SbButton>
        </div>
        <ul
          v-if="devices.length"
          class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,320px),1fr))] gap-4"
        >
          <li v-for="device in devices" :key="device.mac" class="min-w-0">
            <BtDeviceCard
              :device="device"
              :adding="busyFor(device.mac) === 'add'"
              :forgetting="busyFor(device.mac) === 'forget'"
              :connecting="busyFor(device.mac) === 'connect'"
              :disconnecting="busyFor(device.mac) === 'disconnect'"
              :refusal="refusalFor(device.mac)"
              @open="openDevice"
              @add="addSpeaker"
              @forget="forgetBond"
              @connect="connectDevice"
              @disconnect="disconnectDevice"
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
      :connecting="selectedDevice !== null && busyFor(selectedDevice.mac) === 'connect'"
      :disconnecting="selectedDevice !== null && busyFor(selectedDevice.mac) === 'disconnect'"
      @toggle-power="togglePower"
      @connect="connectDevice"
      @disconnect="disconnectDevice"
    />

    <!-- The same dialog Home opens, so scanning and pairing exist in one place only. -->
    <AddSpeakerDialog :open="pairOpen" @update:open="pairOpen = $event" @paired="onPaired" />
  </div>
</template>
