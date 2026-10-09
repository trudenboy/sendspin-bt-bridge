<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import { SbDialog, SbButton, SbSpinner, SbBadge, SbTabs } from '@/kit'
import PairedDevicesList from './PairedDevicesList.vue'
import { Bluetooth, Wifi } from 'lucide-vue-next'
import type { ScanDevice } from '@/api/types'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t } = useI18n()
const btStore = useBluetoothStore()
const bridge = useBridgeStore()
const notifications = useNotificationStore()

const activeTab = ref('scan')
const adapter = ref('')
const audioOnly = ref(true)
const showAdvanced = ref(false)
const options = ref({ quiesceAdapter: false, noInputNoOutputAgent: false, allowHfpProfile: false })
const scanned = ref(false)

const tabs = [
  { id: 'scan', label: t('bluetooth.scan.title') },
  { id: 'paired', label: t('bluetooth.paired') },
]

const adapters = computed(() => bridge.adapters.filter((a) => a.powered !== false))

watch(
  () => [props.open, adapters.value.length] as const,
  ([open]) => {
    if (open && !adapter.value && adapters.value.length) adapter.value = adapters.value[0]!.id
  },
  { immediate: true },
)

const bridgeMacs = computed(() => {
  const set = new Set<string>()
  for (const d of bridge.devices) if (d.bluetooth.mac) set.add(d.bluetooth.mac.toUpperCase())
  return set
})

function onClose(...args: unknown[]) {
  emit('update:open', Boolean(args[0]))
}

async function startScan() {
  if (!adapter.value) return
  scanned.value = true
  await btStore.startScan(adapter.value, audioOnly.value)
  if (btStore.scanError) notifications.error(btStore.scanError)
}

/** The hciN of the adapter that saw the device (scan results carry its MAC). */
function hciOf(result: ScanDevice) {
  return bridge.adapters.find((a) => a.mac === result.adapter)?.id ?? adapter.value
}

async function pairAndAdd(result: ScanDevice) {
  const hci = hciOf(result)
  const paired = await btStore.pairDevice(result.mac, { adapter: hci, ...options.value })
  if (!paired) {
    notifications.error(t('bluetooth.scan.pairFailed', { reason: btStore.pairError ?? '' }))
    return
  }
  try {
    const name = result.name && result.name !== result.mac ? result.name : result.mac
    await btStore.addToBridge(result.mac, name, hci)
    notifications.success(t('bluetooth.scan.addedToast', { name }))
  } catch {
    notifications.error(t('common.error'))
  }
}

function signalTone(rssi?: number | null): 'success' | 'warning' | 'error' | 'neutral' {
  if (rssi == null) return 'neutral'
  if (rssi > -50) return 'success'
  if (rssi > -70) return 'warning'
  return 'error'
}
</script>

<template>
  <SbDialog :model-value="open" :title="t('bluetooth.scan.title')" size="md" @update:model-value="onClose">
    <SbTabs v-model="activeTab" :tabs="tabs">
      <!-- Scan tab -->
      <template #scan>
        <div class="space-y-4 pt-4">
          <p class="text-sm text-text-secondary">
            {{ t('bluetooth.scan.description') }} {{ t('bluetooth.scan.hint') }}
          </p>

          <div class="flex flex-wrap items-center gap-3">
            <label class="flex items-center gap-2 text-sm text-text-secondary">
              {{ t('bluetooth.scan.adapter') }}
              <select
                v-model="adapter"
                class="rounded-lg border border-border bg-surface-primary px-2 py-1 text-sm text-text-primary"
                :disabled="btStore.scanning"
              >
                <option v-for="a in adapters" :key="a.id" :value="a.id">{{ a.id }} — {{ a.name }}</option>
              </select>
            </label>
            <label class="flex items-center gap-1.5 text-sm text-text-secondary">
              <input v-model="audioOnly" type="checkbox" class="accent-primary" :disabled="btStore.scanning" />
              {{ t('bluetooth.audioOnly') }}
            </label>
            <SbButton
              class="ml-auto"
              :loading="btStore.scanning"
              :disabled="btStore.scanning || !adapter"
              size="sm"
              @click="startScan"
            >
              <template #icon-left>
                <Bluetooth class="h-4 w-4" />
              </template>
              {{ btStore.scanning ? t('bluetooth.scan.scanning') : t('bluetooth.scan.start') }}
            </SbButton>
          </div>

          <details :open="showAdvanced" class="text-sm" @toggle="showAdvanced = ($event.target as HTMLDetailsElement).open">
            <summary class="cursor-pointer text-text-secondary">{{ t('bluetooth.scan.advanced') }}</summary>
            <div class="mt-2 space-y-1.5 pl-1">
              <label class="flex items-start gap-2">
                <input v-model="options.quiesceAdapter" type="checkbox" class="mt-0.5 accent-primary" />
                {{ t('bluetooth.scan.quiesce') }}
              </label>
              <label class="flex items-start gap-2">
                <input v-model="options.noInputNoOutputAgent" type="checkbox" class="mt-0.5 accent-primary" />
                {{ t('bluetooth.scan.noIo') }}
              </label>
              <label class="flex items-start gap-2">
                <input v-model="options.allowHfpProfile" type="checkbox" class="mt-0.5 accent-primary" />
                {{ t('bluetooth.scan.hfp') }}
              </label>
            </div>
          </details>

          <div v-if="btStore.scanning" class="flex items-center justify-center py-6">
            <SbSpinner size="md" :label="t('bluetooth.scan.scanning')" />
          </div>

          <div v-if="!btStore.scanning && btStore.scanResults.length > 0" class="divide-y divide-surface-secondary">
            <div v-for="result in btStore.scanResults" :key="result.mac" class="flex items-center justify-between py-3">
              <div class="flex min-w-0 items-center gap-3">
                <Wifi class="h-4 w-4 shrink-0 text-text-secondary" />
                <div class="min-w-0">
                  <p class="truncate text-sm font-medium text-text-primary">
                    {{ result.name && result.name !== result.mac ? result.name : t('bluetooth.scan.unknown') }}
                  </p>
                  <p class="font-mono text-xs text-text-secondary">{{ result.mac }}</p>
                </div>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <SbBadge v-if="result.rssi_dbm != null" :tone="signalTone(result.rssi_dbm)" size="sm">
                  {{ result.rssi_dbm }} dBm
                </SbBadge>
                <SbBadge v-if="result.audio_capable" tone="info" size="sm">
                  {{ t('bluetooth.scan.audio') }}
                </SbBadge>
                <SbBadge v-if="bridgeMacs.has(result.mac.toUpperCase())" tone="success" size="sm">
                  {{ t('bluetooth.scan.added') }}
                </SbBadge>
                <SbButton
                  v-else
                  size="sm"
                  variant="outline"
                  :loading="btStore.pairing && btStore.pairTarget === result.mac"
                  :disabled="btStore.pairing"
                  @click="pairAndAdd(result)"
                >
                  {{ t('bluetooth.scan.pairAndAdd') }}
                </SbButton>
              </div>
            </div>
          </div>

          <div
            v-if="!btStore.scanning && btStore.scanResults.length === 0 && scanned && !btStore.scanError"
            class="py-6 text-center text-sm text-text-secondary"
          >
            {{ t('bluetooth.scan.noDevices') }}
          </div>
          <div v-if="btStore.scanError && !btStore.scanning" class="rounded-lg bg-error/10 p-3 text-sm text-error">
            {{ btStore.scanError }}
          </div>
        </div>
      </template>

      <!-- Paired devices tab -->
      <template #paired>
        <div class="pt-4">
          <PairedDevicesList />
        </div>
      </template>
    </SbTabs>

    <template #footer>
      <SbButton variant="outline" @click="onClose(false)">
        {{ t('common.close') }}
      </SbButton>
    </template>
  </SbDialog>
</template>
