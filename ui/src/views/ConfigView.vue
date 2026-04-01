<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import { SbTabs, SbButton, SbBadge, SbSpinner } from '@/kit'
import ConfigGeneral from '@/components/config/ConfigGeneral.vue'
import ConfigAudio from '@/components/config/ConfigAudio.vue'
import ConfigBluetooth from '@/components/config/ConfigBluetooth.vue'
import ConfigMA from '@/components/config/ConfigMA.vue'
import ConfigSecurity from '@/components/config/ConfigSecurity.vue'
import ConfigAdvanced from '@/components/config/ConfigAdvanced.vue'

const { t } = useI18n()
const configStore = useConfigStore()
const bridgeStore = useBridgeStore()
const notifications = useNotificationStore()

const activeTab = ref('general')

const tabs = [
  { id: 'general', label: t('config.general') },
  { id: 'audio', label: t('config.audio') },
  { id: 'bluetooth', label: t('config.bluetooth') },
  { id: 'ma', label: t('config.ma') },
  { id: 'security', label: t('config.security') },
  { id: 'advanced', label: t('config.advanced') },
]

async function handleSave() {
  try {
    await configStore.saveConfig()
    notifications.success(t('config.saved'))
  } catch {
    notifications.error(t('common.error'))
  }
}

async function handleSaveAndRestart() {
  try {
    await configStore.saveConfig()
    notifications.success(t('config.saved'))
    await bridgeStore.restart()
  } catch {
    notifications.error(t('common.error'))
  }
}

async function handleValidate() {
  try {
    await configStore.validateConfig()
    if (configStore.isValid) {
      notifications.success(t('config.validationOk'))
    }
  } catch {
    notifications.error(t('common.error'))
  }
}

onMounted(() => {
  configStore.fetchConfig()
})
</script>

<template>
  <div>
    <div class="mb-6 flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <h1 class="text-2xl font-bold text-text-primary">
          {{ t('app.config') }}
        </h1>
        <SbBadge v-if="configStore.isDirty" tone="warning">
          {{ t('config.unsaved') }}
        </SbBadge>
      </div>
      <div class="flex items-center gap-2">
        <SbButton
          variant="ghost"
          :disabled="!configStore.isDirty"
          @click="configStore.resetChanges()"
        >
          {{ t('config.reset') }}
        </SbButton>
        <SbButton variant="secondary" @click="handleValidate">
          {{ t('config.validate') }}
        </SbButton>
        <SbButton
          variant="primary"
          :disabled="!configStore.isDirty"
          :loading="configStore.saving"
          @click="handleSave"
        >
          {{ configStore.saving ? t('config.saving') : t('config.save') }}
        </SbButton>
        <SbButton
          variant="warning"
          :disabled="!configStore.isDirty"
          :loading="configStore.saving"
          @click="handleSaveAndRestart"
        >
          {{ t('config.saveAndRestart') }}
        </SbButton>
      </div>
    </div>

    <div v-if="configStore.loading" class="flex items-center justify-center py-20">
      <SbSpinner size="lg" :label="t('common.loading')" />
    </div>

    <SbTabs v-else v-model="activeTab" :tabs="tabs">
      <template #general>
        <ConfigGeneral />
      </template>
      <template #audio>
        <ConfigAudio />
      </template>
      <template #bluetooth>
        <ConfigBluetooth />
      </template>
      <template #ma>
        <ConfigMA />
      </template>
      <template #security>
        <ConfigSecurity />
      </template>
      <template #advanced>
        <ConfigAdvanced />
      </template>
    </SbTabs>
  </div>
</template>
