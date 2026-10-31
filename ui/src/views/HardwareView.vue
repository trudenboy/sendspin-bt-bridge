<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RefreshCw } from 'lucide-vue-next'
import { SbBadge, SbButton, SbEmptyState, SbSpinner } from '@/kit'
import ControllerCard from '@/components/hardware/ControllerCard.vue'
import BtDeviceCard from '@/components/hardware/BtDeviceCard.vue'
import CapabilityBanner from '@/components/hardware/CapabilityBanner.vue'
import EntityDrawer from '@/components/hardware/EntityDrawer.vue'
import { useHardwareStore } from '@/stores/hardware'
import { useBridgeStore } from '@/stores/bridge'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useNotificationStore } from '@/stores/notifications'
import { confirmAction } from '@/composables/useActionPolicy'
import { forgetBtDevice, setAdapterPower } from '@/api/bluetooth'
import { ApiError } from '@/api/client'
import { speakerName } from '@/utils/speakerName'
import type { BtDeviceSummary, ControllerSummary } from '@/api/types'

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
const selected = ref<{ kind: 'controller' | 'device'; id: string } | null>(null)
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

/* The drawer belongs to the selected entity: a refresh that drops it closes the drawer. */
watch([selectedController, selectedDevice], ([controller, device]) => {
  if (selected.value && !controller && !device) drawerOpen.value = false
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

      <section data-devices-section>
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
    </template>

    <EntityDrawer
      v-model:open="drawerOpen"
      :kind="selected?.kind ?? 'controller'"
      :controller="selectedController"
      :device="selectedDevice"
      :busy="busy === selectedController?.id"
      @toggle-power="togglePower"
    />
  </div>
</template>
