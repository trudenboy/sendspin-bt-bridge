<script setup lang="ts">
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUpdateStore } from '@/stores/update'
import { useBridgeStore } from '@/stores/bridge'
import { SbBadge, SbButton } from '@/kit'
import SettingsRow from './SettingsRow.vue'

const { t } = useI18n()
const update = useUpdateStore()
const bridge = useBridgeStore()

onMounted(() => {
  if (!update.info) update.fetchInfo()
})
</script>

<template>
  <SettingsRow
    :label="t('settings.parts.updates.label', { version: bridge.version || '—' })"
    :help="
      update.error ||
      (update.info?.update_available
        ? t('settings.parts.updates.available', { version: update.info.version })
        : t('settings.parts.updates.upToDate'))
    "
  >
    <div class="flex items-center gap-2">
      <SbBadge v-if="update.info?.update_available" tone="info" dot>{{ update.info.version }}</SbBadge>
      <SbButton variant="outline" size="sm" :loading="update.checking" @click="update.checkForUpdates()">
        {{ t('settings.parts.updates.check') }}
      </SbButton>
      <SbButton v-if="update.info?.update_available" size="sm" @click="update.openDialog()">
        {{ t('settings.parts.updates.install') }}
      </SbButton>
    </div>
  </SettingsRow>
</template>
