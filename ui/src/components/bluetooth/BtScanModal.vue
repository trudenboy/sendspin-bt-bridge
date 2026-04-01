<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useBluetoothStore } from '@/stores/bluetooth'
import { SbDialog, SbButton, SbSpinner, SbBadge } from '@/kit'
import { Bluetooth, Wifi } from 'lucide-vue-next'

defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t } = useI18n()
const btStore = useBluetoothStore()

function onClose(...args: unknown[]) {
  const val = Boolean(args[0])
  if (!val) btStore.stopPolling()
  emit('update:open', val)
}

function startScan() {
  btStore.startScan()
}

function pair(mac: string) {
  btStore.pairDevice(mac)
}

function signalTone(rssi?: number): 'success' | 'warning' | 'error' | 'neutral' {
  if (rssi == null) return 'neutral'
  if (rssi > -50) return 'success'
  if (rssi > -70) return 'warning'
  return 'error'
}
</script>

<template>
  <SbDialog
    :model-value="open"
    :title="t('bluetooth.scan.title')"
    size="md"
    @update:model-value="onClose"
  >
    <!-- Scan controls -->
    <div class="space-y-4">
      <div class="flex items-center justify-between">
        <p class="text-sm text-text-secondary">
          {{ t('bluetooth.scan.description') }}
        </p>
        <SbButton
          :loading="btStore.scanning"
          :disabled="btStore.scanning"
          size="sm"
          @click="startScan"
        >
          <template #icon-left>
            <Bluetooth class="h-4 w-4" />
          </template>
          {{ btStore.scanning ? t('bluetooth.scan.scanning') : t('bluetooth.scan.start') }}
        </SbButton>
      </div>

      <!-- Scanning indicator -->
      <div v-if="btStore.scanning" class="flex items-center justify-center py-6">
        <SbSpinner size="md" :label="t('bluetooth.scan.scanning')" />
      </div>

      <!-- Results -->
      <div
        v-if="!btStore.scanning && btStore.scanResults.length > 0"
        class="divide-y divide-surface-secondary"
      >
        <div
          v-for="result in btStore.scanResults"
          :key="result.mac"
          class="flex items-center justify-between py-3"
        >
          <div class="flex items-center gap-3">
            <Wifi class="h-4 w-4 text-text-secondary" />
            <div>
              <p class="text-sm font-medium text-text-primary">
                {{ result.name || t('bluetooth.scan.unknown') }}
              </p>
              <p class="text-xs text-text-secondary">{{ result.mac }}</p>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <SbBadge v-if="result.rssi != null" :tone="signalTone(result.rssi)" size="sm">
              {{ result.rssi }} dBm
            </SbBadge>
            <SbBadge v-if="result.is_audio" tone="info" size="sm">
              {{ t('bluetooth.scan.audio') }}
            </SbBadge>
            <SbButton
              v-if="!result.paired"
              size="sm"
              variant="secondary"
              :loading="btStore.pairing && btStore.pairTarget === result.mac"
              :disabled="btStore.pairing"
              @click="pair(result.mac)"
            >
              {{ t('bluetooth.scan.pair') }}
            </SbButton>
            <SbBadge v-else tone="success" size="sm">
              {{ t('bluetooth.scan.paired') }}
            </SbBadge>
          </div>
        </div>
      </div>

      <!-- Empty results -->
      <div
        v-if="!btStore.scanning && btStore.scanResults.length === 0 && btStore.scanJobId"
        class="py-6 text-center text-sm text-text-secondary"
      >
        {{ t('bluetooth.scan.noDevices') }}
      </div>
    </div>

    <template #footer>
      <SbButton variant="secondary" @click="onClose(false)">
        {{ t('common.close') }}
      </SbButton>
    </template>
  </SbDialog>
</template>
