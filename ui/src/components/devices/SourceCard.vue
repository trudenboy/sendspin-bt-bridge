<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { MoreVertical } from 'lucide-vue-next'
import { SbBadge, SbButton, SbDropdown, SbDropdownItem } from '@/kit'
import DeviceCardShell from './card/DeviceCardShell.vue'
import { sourceCardView } from './card/viewModels'
import type { SourceInputStatus, SourceState } from '@/api/types'

const props = withDefaults(
  defineProps<{
    input: SourceInputStatus
    state?: SourceState
    targetPlayer?: string | null
    busy?: boolean
  }>(),
  { state: undefined, targetPlayer: null, busy: false },
)

const emit = defineEmits<{
  use: [id: string]
  rename: [id: string, name: string]
  remove: [id: string]
}>()

const { t } = useI18n()
const renaming = ref(false)
const draft = ref('')
const view = computed(() => sourceCardView(props.input, props.state))

function startRename() {
  draft.value = props.input.name
  renaming.value = true
}
function saveRename() {
  const name = draft.value.trim()
  if (name) emit('rename', props.input.id, name)
  renaming.value = false
}
function action(id: string) {
  if (id === 'use') emit('use', props.input.id)
  if (id === 'rename') startRename()
  if (id === 'remove') emit('remove', props.input.id)
}
</script>

<template>
  <DeviceCardShell
    :view="view"
    :data-source-card="''"
    :data-source-id="input.id"
    :data-source-active="String(input.active)"
    @action="action"
  >
    <template #menu>
      <SbDropdown align="right">
        <template #trigger>
          <button
            type="button"
            class="inline-flex size-10 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
            :aria-label="t('device.actions.more', { name: input.name || input.id })"
            aria-haspopup="true"
          >
            <MoreVertical class="size-4" />
          </button>
        </template>
        <SbDropdownItem v-if="input.kind === 'local'" data-source-rename @click="startRename">
          {{ t('devices.sources.rename') }}
        </SbDropdownItem>
        <SbDropdownItem v-if="input.kind !== 'headset_microphone'" data-source-remove @click="emit('remove', input.id)">
          {{ input.kind === 'local' ? t('devices.sources.remove') : t('devices.sources.forget') }}
        </SbDropdownItem>
      </SbDropdown>
    </template>

    <template #badges>
      <SbBadge v-if="input.monitor" size="sm" tone="warning">{{ t('devices.sources.monitor') }}</SbBadge>
      <SbBadge v-if="!input.available" size="sm" tone="error">{{ t('devices.sources.unavailable') }}</SbBadge>
    </template>

    <template #context>
      <span v-if="input.active && targetPlayer" data-source-target>{{ t('devices.sources.playsOn', { target: targetPlayer }) }}</span>
      <span v-else>{{ t(input.kind === 'headset_microphone' ? 'devices.sources.headsetMicrophone' : input.kind === 'bluetooth' ? 'devices.sources.bluetooth' : 'devices.sources.local') }}</span>
    </template>

    <template #footer>
      <SbButton
        v-if="!input.active"
        size="sm"
        variant="outline"
        data-source-use
        :disabled="busy || !input.available"
        @click="emit('use', input.id)"
      >
        {{ t('devices.sources.use') }}
      </SbButton>
    </template>

    <template #extra>
      <form v-if="renaming" class="relative z-10 flex flex-wrap items-center gap-2" @submit.prevent="saveRename">
        <input
          v-model="draft"
          data-source-rename-input
          class="min-w-0 flex-1 rounded-(--radius-input) border border-border-strong bg-surface-card px-2 py-1 text-sm text-text-primary"
          :aria-label="t('devices.sources.rename')"
        />
        <SbButton size="sm" type="submit" :disabled="!draft.trim()">{{ t('common.save') }}</SbButton>
        <SbButton size="sm" variant="ghost" type="button" @click="renaming = false">{{ t('common.cancel') }}</SbButton>
      </form>
    </template>
  </DeviceCardShell>
</template>
