<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bluetooth, Pencil, Play, Radio, Trash2 } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'
import type { SourceInputStatus, SourceState } from '@/api/types'

/**
 * One configured source: a Bluetooth bond or a saved local input.  Start/Stop
 * belongs to the whole source mode in Settings; this card only chooses which
 * input feeds it and removes the input.
 */
const props = withDefaults(
  defineProps<{
    input: SourceInputStatus
    /** The bridge's source state, shown only on the active card. */
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

const kindLabel = computed(() =>
  props.input.kind === 'bluetooth' ? t('devices.sources.bluetooth') : t('devices.sources.local'),
)
const stateLabel = computed(() => {
  if (!props.input.active) return t('devices.sources.available')
  const labels = {
    off: t('devices.sources.off'),
    pairing: t('devices.sources.pairing'),
    waiting: t('devices.sources.waiting'),
    streaming: t('devices.sources.streaming'),
    error: t('devices.sources.error'),
  } as const
  return labels[props.state ?? 'off']
})
const stateTone = computed(() => {
  if (!props.input.active) return 'neutral'
  if (props.state === 'streaming') return 'success'
  if (props.state === 'error') return 'error'
  if (props.state === 'pairing' || props.state === 'waiting') return 'warning'
  return 'neutral'
})
const targetLabel = computed(() =>
  props.input.active ? props.targetPlayer || t('devices.sources.targetInMa') : '',
)

function startRename() {
  draft.value = props.input.name
  renaming.value = true
}

function saveRename() {
  const name = draft.value.trim()
  if (name) emit('rename', props.input.id, name)
  renaming.value = false
}
</script>

<template>
  <article
    data-source-card
    :data-source-id="input.id"
    :data-source-active="String(input.active)"
    class="relative flex flex-col gap-3 rounded-(--radius-card) border bg-surface-card p-4"
    :class="input.active ? 'border-primary/50' : 'border-border'"
  >
    <header class="flex items-start gap-3">
      <span class="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary-text">
        <Bluetooth v-if="input.kind === 'bluetooth'" class="size-5" aria-hidden="true" />
        <Radio v-else class="size-5" aria-hidden="true" />
      </span>
      <div class="min-w-0 flex-1">
        <h3 class="break-words text-base font-medium leading-snug text-text-primary">
          {{ input.name || input.description || input.id }}
        </h3>
        <p class="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-text-secondary">
          <span>{{ kindLabel }}</span>
          <span aria-hidden="true">·</span>
          <span class="truncate font-mono">{{ input.kind === 'bluetooth' ? input.mac : input.node }}</span>
        </p>
      </div>
      <SbBadge data-source-state :tone="stateTone" size="sm">{{ stateLabel }}</SbBadge>
    </header>

    <div class="flex flex-wrap items-center gap-1.5">
      <SbBadge v-if="input.active" size="sm" tone="info">{{ t('devices.sources.active') }}</SbBadge>
      <SbBadge v-if="input.kind === 'bluetooth' && input.connected" size="sm">{{ t('devices.sources.connected') }}</SbBadge>
      <SbBadge v-if="input.monitor" size="sm" tone="warning">{{ t('devices.sources.monitor') }}</SbBadge>
      <SbBadge v-if="!input.available" size="sm" tone="error">{{ t('devices.sources.unavailable') }}</SbBadge>
    </div>

    <p v-if="targetLabel" data-source-target class="text-xs text-text-secondary">
      {{ t('devices.sources.playsOn', { target: targetLabel }) }}
    </p>

    <form v-if="renaming" class="flex flex-wrap items-center gap-2" @submit.prevent="saveRename">
      <input
        v-model="draft"
        data-source-rename-input
        class="min-w-0 flex-1 rounded-(--radius-input) border border-border-strong bg-surface-card px-2 py-1 text-sm text-text-primary"
        :aria-label="t('devices.sources.rename')"
      />
      <SbButton size="sm" type="submit" :disabled="!draft.trim()">{{ t('common.save') }}</SbButton>
      <SbButton size="sm" variant="ghost" type="button" @click="renaming = false">{{ t('common.cancel') }}</SbButton>
    </form>

    <div class="mt-auto flex flex-wrap items-center gap-2">
      <SbButton
        v-if="!input.active"
        size="sm"
        variant="outline"
        data-source-use
        :disabled="busy || !input.available"
        @click="emit('use', input.id)"
      >
        <template #icon-left><Play class="h-3.5 w-3.5" aria-hidden="true" /></template>
        {{ t('devices.sources.use') }}
      </SbButton>
      <SbButton v-if="input.kind === 'local'" size="sm" variant="ghost" data-source-rename :disabled="busy" @click="startRename">
        <template #icon-left><Pencil class="h-3.5 w-3.5" aria-hidden="true" /></template>
        {{ t('devices.sources.rename') }}
      </SbButton>
      <SbButton size="sm" variant="ghost" data-source-remove :disabled="busy" @click="emit('remove', input.id)">
        <template #icon-left><Trash2 class="h-3.5 w-3.5" aria-hidden="true" /></template>
        {{ input.kind === 'local' ? t('devices.sources.remove') : t('devices.sources.forget') }}
      </SbButton>
    </div>
  </article>
</template>
