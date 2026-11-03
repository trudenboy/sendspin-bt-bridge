<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useHardwareStore } from '@/stores/hardware'
import { SbBadge, SbToggle } from '@/kit'
import SettingsRow from './SettingsRow.vue'

/**
 * The source mode is opt-in: the switch starts and stops the Sendspin source
 * right here, without a bridge restart.  Music Assistant pairing lives below
 * it and is only rendered while the mode is on; the Bluetooth side belongs to
 * the Hardware page and only appears after MA has paired the bridge.
 */
const { t } = useI18n()
const bridge = useBridgeStore()
const hardware = useHardwareStore()

const source = computed(() => bridge.bridge?.sendspin_source ?? null)
const enabled = computed(() => !!source.value?.enabled)
const busy = computed(() => hardware.sourceBusy)
const error = ref<string | null>(null)

const stateKey = computed(() => {
  const state = source.value?.state ?? 'off'
  return `hardware.source.state${state.charAt(0).toUpperCase()}${state.slice(1)}`
})

async function setEnabled(next: boolean) {
  error.value = null
  try {
    if (next) await hardware.startSource()
    else await hardware.stopSource()
    await bridge.refresh()
  } catch {
    // The store already kept the failure; a start that could not run leaves
    // the source off instead of persisting an "enabled" flag that does nothing.
    error.value = t('settings.parts.sourceMode.failed')
  }
}
</script>

<template>
  <div data-source-mode>
    <SettingsRow :label="t('settings.parts.sourceMode.label')" :help="t('settings.parts.sourceMode.help')">
      <div class="flex flex-wrap items-center gap-2">
        <SbToggle
          :model-value="enabled"
          :disabled="busy"
          :label="t('settings.parts.sourceMode.label')"
          data-source-mode-toggle
          @update:model-value="setEnabled"
        />
        <SbBadge v-if="source" data-source-mode-state :tone="enabled ? 'success' : 'neutral'" size="sm">
          {{ t(stateKey) }}
        </SbBadge>
      </div>
    </SettingsRow>

    <p v-if="error" data-source-mode-error role="alert" class="mb-4 rounded-(--radius-card) border border-error/50 bg-error/10 px-3 py-2 text-sm text-text-primary">
      {{ error }}
    </p>

    <p v-if="!enabled" class="pb-4 text-sm text-text-secondary">{{ t('settings.parts.sourceMode.off') }}</p>
  </div>
</template>
