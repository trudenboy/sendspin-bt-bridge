<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import SettingsRow from './SettingsRow.vue'

/**
 * The source's state, pairing window and relays live on the Hardware page now;
 * Music Assistant's settings keep the switch and the port and point there.
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
