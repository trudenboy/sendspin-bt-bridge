<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbDropdown, SbDropdownItem } from '@/kit'
import { MoreVertical } from 'lucide-vue-next'
import { useDisabledPlayerActions } from '@/composables/useDisabledPlayerActions'
import type { DisabledPlayer } from '@/stores/devices'

/** What applies to a disabled player: enable it (at once), its settings, removal. */
const props = defineProps<{ player: DisabledPlayer }>()
const emit = defineEmits<{ openDetail: [id: string] }>()

const { t } = useI18n()
const actions = useDisabledPlayerActions()
</script>

<template>
  <SbDropdown align="right">
    <template #trigger>
      <button
        type="button"
        aria-haspopup="true"
        class="inline-flex size-10 cursor-pointer items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary disabled:cursor-wait disabled:opacity-50"
        :disabled="actions.busy.value"
        :aria-label="t('device.actions.more', { name: actions.nameOf(props.player) })"
      >
        <MoreVertical class="size-4" />
      </button>
    </template>
    <SbDropdownItem data-action="enable" @click="actions.enable([player])">{{ t('speaker.enable') }}</SbDropdownItem>
    <SbDropdownItem data-action="settings" @click="emit('openDetail', player.id)">{{ t('speaker.settings') }}</SbDropdownItem>
    <div class="my-1 border-t border-border" />
    <SbDropdownItem data-action="remove" :destructive="true" @click="actions.remove([player])">{{ t('speaker.remove') }}…</SbDropdownItem>
  </SbDropdown>
</template>
