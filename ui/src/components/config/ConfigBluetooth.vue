<script setup lang="ts">
import { computed, ref, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useBridgeStore } from '@/stores/bridge'
import { SbSlider, SbCard, SbBadge, SbButton } from '@/kit'
import { Pencil, Check } from 'lucide-vue-next'

const { t } = useI18n()
const configStore = useConfigStore()
const bridgeStore = useBridgeStore()

const btCheckInterval = computed({
  get: () => configStore.config?.BT_CHECK_INTERVAL ?? 15,
  set: (v: number) => configStore.updateField('BT_CHECK_INTERVAL', v),
})

const btMaxReconnect = computed({
  get: () => configStore.config?.BT_MAX_RECONNECT_FAILS ?? 10,
  set: (v: number) => configStore.updateField('BT_MAX_RECONNECT_FAILS', v),
})

function formatSec(v: number): string {
  return `${v} ${t('config.btCheckIntervalUnit')}`
}

/* Adapter name inline editing */
const editingHci = ref<string | null>(null)
const editName = ref('')
const editInput = ref<HTMLInputElement | null>(null)

function startEditing(adapter: { hci_device: string; name: string }) {
  editingHci.value = adapter.hci_device
  editName.value = adapter.name || ''
  nextTick(() => editInput.value?.focus())
}

function confirmEdit() {
  if (!editingHci.value || !configStore.config) return
  const adapters = configStore.config.adapters ?? []
  const idx = adapters.findIndex((a) => a.hci_device === editingHci.value)
  if (idx >= 0) {
    configStore.updateField(`adapters.${idx}.name`, editName.value.trim())
  } else {
    adapters.push({ hci_device: editingHci.value, name: editName.value.trim() })
    configStore.updateField('adapters', adapters)
  }
  editingHci.value = null
}
</script>

<template>
  <div class="space-y-6 py-6">
    <SbCard>
      <template #header>
        <h3 class="text-lg font-semibold text-text-primary">{{ t('config.bluetooth') }}</h3>
      </template>
      <div class="space-y-6">
        <div>
          <SbSlider
            v-model="btCheckInterval"
            :label="t('config.btCheckInterval')"
            :min="5"
            :max="60"
            :step="5"
            show-value
            :format-value="formatSec"
          />
          <p class="mt-1 text-xs text-text-secondary">
            {{ t('config.btCheckIntervalHint') }}
          </p>
        </div>

        <div>
          <SbSlider
            v-model="btMaxReconnect"
            :label="t('config.btMaxReconnect')"
            :min="1"
            :max="50"
            :step="1"
            show-value
          />
          <p class="mt-1 text-xs text-text-secondary">
            {{ t('config.btMaxReconnectHint') }}
          </p>
        </div>

        <p class="text-sm text-text-secondary italic">
          {{ t('config.managementMode') }}
        </p>
      </div>
    </SbCard>

    <SbCard>
      <template #header>
        <h3 class="text-lg font-semibold text-text-primary">{{ t('config.adapterList') }}</h3>
      </template>
      <div v-if="bridgeStore.adapters.length === 0" class="py-4 text-center text-sm text-text-secondary">
        {{ t('config.noAdapters') }}
      </div>
      <div v-else class="divide-y divide-gray-200 dark:divide-gray-700">
        <div
          v-for="adapter in bridgeStore.adapters"
          :key="adapter.hci_device"
          class="flex items-center justify-between px-1 py-3"
        >
          <div class="min-w-0 flex-1">
            <div v-if="editingHci === adapter.hci_device" class="flex items-center gap-2">
              <input
                ref="editInput"
                v-model="editName"
                type="text"
                class="w-48 rounded border border-gray-300 bg-transparent px-2 py-1 text-sm text-text-primary outline-none focus:ring-2 focus:ring-primary/40 dark:border-gray-600"
                @keydown.enter="confirmEdit"
                @keydown.escape="editingHci = null"
                @blur="confirmEdit"
              />
              <SbButton variant="ghost" size="sm" icon :title="t('common.confirm')" @click.prevent="confirmEdit">
                <Check class="h-4 w-4" aria-hidden="true" />
              </SbButton>
            </div>
            <div v-else class="flex items-center gap-1.5">
              <p class="text-sm font-medium text-text-primary">
                {{ adapter.name || adapter.hci_device }}
              </p>
              <SbButton
                variant="ghost"
                size="sm"
                icon
                :title="t('config.editAdapterName')"
                class="!h-6 !w-6"
                @click="startEditing(adapter)"
              >
                <Pencil class="h-3.5 w-3.5" aria-hidden="true" />
              </SbButton>
            </div>
            <p class="text-xs text-text-secondary">
              {{ adapter.hci_device }} · {{ adapter.mac }}
            </p>
          </div>
          <SbBadge :tone="adapter.powered ? 'success' : 'neutral'">
            {{ adapter.powered ? t('config.adapterPowered') : t('config.adapterOff') }}
          </SbBadge>
        </div>
      </div>
    </SbCard>
  </div>
</template>
