<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import SettingsRow from './SettingsRow.vue'

/**
 * The source's state and both pairing windows (the phone's and Music Assistant's)
 * live on the Hardware page; these settings only point there.
 */
const { t } = useI18n()
</script>

<template>
  <SettingsRow :label="t('settings.parts.sourceLink.label')" :help="t('settings.parts.sourceLink.help')">
    <RouterLink
      to="/hardware"
      data-source-hardware-link
      class="text-sm text-primary-text underline-offset-2 hover:underline"
    >
      {{ t('settings.parts.sourceLink.action') }}
    </RouterLink>
  </SettingsRow>
</template>
