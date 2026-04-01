<script setup lang="ts">
import { watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBluetoothStore } from '@/stores/bluetooth'
import { SbDialog, SbButton, SbSpinner, SbBadge } from '@/kit'

const props = defineProps<{
  mac: string
  open: boolean
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
}>()

const { t } = useI18n()
const btStore = useBluetoothStore()

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen && props.mac) {
      btStore.fetchBtDeviceInfo(props.mac)
    }
  },
  { immediate: true },
)

function boolTone(val?: string): 'success' | 'error' | 'neutral' {
  if (val === 'yes') return 'success'
  if (val === 'no') return 'error'
  return 'neutral'
}

function onClose() {
  emit('update:open', false)
}
</script>

<template>
  <SbDialog
    :model-value="open"
    :title="t('bluetooth.deviceInfo')"
    size="sm"
    @update:model-value="onClose"
  >
    <div v-if="btStore.loadingInfo" class="flex items-center justify-center py-8">
      <SbSpinner size="md" :label="t('common.loading')" />
    </div>

    <div v-else-if="btStore.btDeviceInfo" class="space-y-3">
      <dl class="divide-y divide-surface-secondary text-sm">
        <div class="flex items-center justify-between py-2">
          <dt class="text-text-secondary">{{ t('drawer.config.name') }}</dt>
          <dd class="font-medium text-text-primary">
            {{ btStore.btDeviceInfo.name ?? '—' }}
          </dd>
        </div>
        <div class="flex items-center justify-between py-2">
          <dt class="text-text-secondary">{{ t('drawer.config.mac') }}</dt>
          <dd class="font-mono text-xs text-text-primary">{{ btStore.btDeviceInfo.mac }}</dd>
        </div>
        <div v-if="btStore.btDeviceInfo.alias" class="flex items-center justify-between py-2">
          <dt class="text-text-secondary">Alias</dt>
          <dd class="text-text-primary">{{ btStore.btDeviceInfo.alias }}</dd>
        </div>
        <div class="flex items-center justify-between py-2">
          <dt class="text-text-secondary">Paired</dt>
          <dd>
            <SbBadge :tone="boolTone(btStore.btDeviceInfo.paired)" size="sm">
              {{ btStore.btDeviceInfo.paired ?? '—' }}
            </SbBadge>
          </dd>
        </div>
        <div class="flex items-center justify-between py-2">
          <dt class="text-text-secondary">Trusted</dt>
          <dd>
            <SbBadge :tone="boolTone(btStore.btDeviceInfo.trusted)" size="sm">
              {{ btStore.btDeviceInfo.trusted ?? '—' }}
            </SbBadge>
          </dd>
        </div>
        <div class="flex items-center justify-between py-2">
          <dt class="text-text-secondary">Connected</dt>
          <dd>
            <SbBadge :tone="boolTone(btStore.btDeviceInfo.connected)" size="sm">
              {{ btStore.btDeviceInfo.connected ?? '—' }}
            </SbBadge>
          </dd>
        </div>
        <div v-if="btStore.btDeviceInfo.class" class="flex items-center justify-between py-2">
          <dt class="text-text-secondary">Class</dt>
          <dd class="font-mono text-xs text-text-primary">{{ btStore.btDeviceInfo.class }}</dd>
        </div>
        <div v-if="btStore.btDeviceInfo.icon" class="flex items-center justify-between py-2">
          <dt class="text-text-secondary">Icon</dt>
          <dd class="text-text-primary">{{ btStore.btDeviceInfo.icon }}</dd>
        </div>
      </dl>

      <div v-if="btStore.btDeviceInfo.error" class="rounded-lg bg-red-50 p-3 text-sm text-error dark:bg-red-900/10">
        {{ btStore.btDeviceInfo.error }}
      </div>
    </div>

    <template #footer>
      <SbButton variant="secondary" @click="onClose">
        {{ t('common.close') }}
      </SbButton>
    </template>
  </SbDialog>
</template>
