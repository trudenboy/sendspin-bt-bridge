<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import { confirmAction } from '@/composables/useActionPolicy'
import { SbButton, SbBadge, SbSpinner } from '@/kit'
import { speakerName } from '@/utils/speakerName'
import BtDeviceInfoModal from './BtDeviceInfoModal.vue'
import { Bluetooth, Info, Trash2, RefreshCw } from 'lucide-vue-next'

const { t } = useI18n()
const btStore = useBluetoothStore()
const bridge = useBridgeStore()
const notifications = useNotificationStore()

const audioOnly = ref(true)
const infoModalOpen = ref(false)
const infoMac = ref('')

onMounted(() => {
  btStore.fetchPairedDevices()
})

const bridgeMacs = computed(() => {
  const set = new Set<string>()
  for (const d of bridge.devices) {
    if (d.bluetooth?.mac) set.add(d.bluetooth?.mac.toUpperCase())
  }
  for (const d of bridge.bridge?.disabled_devices ?? []) {
    if (typeof d.mac === 'string') set.add(d.mac.toUpperCase())
  }
  return set
})

const filteredDevices = computed(() => {
  if (!audioOnly.value) return btStore.pairedDevices
  // Show all when audio-only filter is on — backend already returns named devices
  // We don't have is_audio from paired list, so show all named devices
  return btStore.pairedDevices
})

const audioCount = computed(() => btStore.pairedDevices.length)
const totalCount = computed(() => btStore.pairedDevices.length)

function isInFleet(mac: string): boolean {
  return bridgeMacs.value.has(mac.toUpperCase())
}

function openInfo(mac: string) {
  infoMac.value = mac
  infoModalOpen.value = true
}

/** Forgetting a bond is irreversible: say what happens to this speaker first. */
async function forget(mac: string, name: string) {
  const bridgeNames = bridge.devices
    .filter((d) => d.bluetooth?.mac?.toUpperCase() === mac.toUpperCase())
    .map((d) => speakerName(d.name))
    .join(', ')
  const ok = await confirmAction('bluetooth.forgetBond', {
    title: t('device.actions.forgetTitle', { name: name || mac }),
    consequences: [
      ...(bridgeNames ? [t('consequence.stopsPlayback', { names: bridgeNames })] : []),
      t('consequence.needsRepair'),
    ],
    confirmLabel: t('device.actions.forget'),
  })
  if (!ok) return
  try {
    await btStore.removePairedDevice(mac)
    notifications.success(t('device.actions.remove'))
  } catch {
    notifications.error(t('common.error'))
  }
}

const adding = ref<string | null>(null)

async function addToBridge(mac: string, name: string, adapters: string[]) {
  adding.value = mac
  try {
    const hci = bridge.adapters.find((a) => adapters.includes(a.mac))?.id ?? ''
    if (await btStore.addToBridge(mac, name, hci)) notifications.success(t('bluetooth.scan.addedToast', { name }))
  } catch {
    notifications.error(t('common.error'))
  } finally {
    adding.value = null
  }
}

function refresh() {
  btStore.fetchPairedDevices()
}
</script>

<template>
  <div class="space-y-4">
    <!-- Header with count + refresh -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="text-sm text-text-secondary">
          {{ t('bluetooth.audioCount', { audio: audioCount, total: totalCount }) }}
        </span>
      </div>
      <SbButton
        size="sm"
        variant="outline"
        :loading="btStore.loadingPaired"
        @click="refresh"
      >
        <template #icon-left>
          <RefreshCw class="h-3.5 w-3.5" />
        </template>
        {{ t('common.retry') }}
      </SbButton>
    </div>

    <!-- Loading -->
    <div v-if="btStore.loadingPaired" class="flex items-center justify-center py-6">
      <SbSpinner size="md" :label="t('common.loading')" />
    </div>

    <!-- Device list -->
    <div
      v-else-if="filteredDevices.length > 0"
      class="divide-y divide-surface-secondary"
    >
      <div
        v-for="device in filteredDevices"
        :key="device.mac"
        class="flex items-center justify-between py-3"
      >
        <div class="flex items-center gap-3">
          <Bluetooth class="h-4 w-4 text-text-secondary" />
          <div>
            <p class="text-sm font-medium text-text-primary">
              {{ device.name }}
            </p>
            <p class="font-mono text-xs text-text-secondary">{{ device.mac }}</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <SbBadge v-if="isInFleet(device.mac)" tone="success" size="sm">
            {{ t('bluetooth.scan.added') }}
          </SbBadge>
          <SbButton
            v-else
            size="sm"
            variant="outline"
            :loading="adding === device.mac"
            @click="addToBridge(device.mac, device.name, device.adapters ?? [])"
          >
            {{ t('bluetooth.addToFleet') }}
          </SbButton>
          <button
            type="button"
            class="cursor-pointer rounded p-1 text-text-secondary transition-colors hover:text-text-primary"
            :aria-label="t('bluetooth.info')"
            @click="openInfo(device.mac)"
          >
            <Info class="h-4 w-4" />
          </button>
          <button
            type="button"
            class="cursor-pointer rounded p-1 text-text-secondary transition-colors hover:text-error"
            :aria-label="t('device.actions.forget')"
            @click="forget(device.mac, device.name)"
          >
            <Trash2 class="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div
      v-else
      class="py-6 text-center text-sm text-text-secondary"
    >
      {{ t('bluetooth.noDevices') }}
    </div>

    <!-- BT Device Info Modal -->
    <BtDeviceInfoModal
      :mac="infoMac"
      :open="infoModalOpen"
      @update:open="infoModalOpen = $event"
    />
  </div>
</template>
