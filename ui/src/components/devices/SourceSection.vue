<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus, Radio } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'
import { confirmAction } from '@/composables/useActionPolicy'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import { removeSource, renameSource, selectSource } from '@/api/source'
import type { SourceInputStatus } from '@/api/types'
import SourceCard from './SourceCard.vue'

/** Configured sources on the Devices page. Pairing/adding lives in Add device. */
const props = defineProps<{ query?: string }>()
const emit = defineEmits<{ add: [] }>()

const { t } = useI18n()
const bridge = useBridgeStore()
const notifications = useNotificationStore()
const busy = ref<string | null>(null)

const source = computed(() => bridge.bridge?.sendspin_source ?? null)
const inputs = computed<SourceInputStatus[]>(() => {
  const all = source.value?.inputs ?? []
  return [...all].sort((a, b) => Number(b.active) - Number(a.active) || a.name.localeCompare(b.name))
})
const visibleInputs = computed(() => {
  const q = (props.query ?? '').trim().toLowerCase()
  if (!q) return inputs.value
  return inputs.value.filter((input) =>
    [input.name, input.description, input.mac, input.node, input.kind].some((value) => (value ?? '').toLowerCase().includes(q)),
  )
})
async function refresh() {
  await bridge.refresh()
}

async function onUse(id: string) {
  busy.value = id
  try {
    await selectSource(id)
    await refresh()
  } catch {
    notifications.error(t('devices.sources.useFailed'))
  } finally {
    busy.value = null
  }
}

async function onRename(id: string, name: string) {
  busy.value = id
  try {
    await renameSource(id, name)
    await refresh()
  } catch {
    notifications.error(t('devices.sources.renameFailed'))
  } finally {
    busy.value = null
  }
}

async function onRemove(id: string) {
  const input = inputs.value.find((entry) => entry.id === id)
  const ok = await confirmAction('source.removeInput', {
    title: t('devices.sources.removeTitle', { name: input?.name || id }),
    consequences: [t('devices.sources.removeConsequence')],
    confirmLabel: input?.kind === 'local' ? t('devices.sources.remove') : t('devices.sources.forget'),
  })
  if (!ok) return
  busy.value = id
  try {
    await removeSource(id)
    await refresh()
    notifications.success(t('devices.sources.removed'))
  } catch {
    notifications.error(t('devices.sources.removeFailed'))
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <section data-sources-section class="mb-8">
    <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 class="flex items-center gap-2 text-lg font-semibold text-text-primary">
        <Radio class="size-5 text-text-secondary" aria-hidden="true" />
        {{ t('devices.sources.title') }}
        <SbBadge tone="warning" size="sm">{{ t('common.experimental') }}</SbBadge>
      </h2>
      <SbButton size="sm" variant="outline" data-add-source @click="emit('add')">
        <template #icon-left><Plus class="h-4 w-4" /></template>
        {{ t('devices.sources.add') }}
      </SbButton>
    </div>

    <p
      v-if="source?.target_error === 'press_play'"
      data-sources-press-play
      class="mb-3 rounded-(--radius-card) border border-info/40 bg-info/10 px-3 py-2 text-sm text-text-secondary"
    >
      {{ t('devices.sources.pressPlay') }}
    </p>

    <p v-if="visibleInputs.length === 0" data-sources-empty class="rounded-(--radius-card) border border-dashed border-border px-4 py-5 text-sm text-text-secondary">
      {{ t('devices.sources.empty') }}
    </p>
    <div v-else class="grid grid-cols-[repeat(auto-fill,minmax(min(100%,320px),1fr))] gap-4">
      <SourceCard
        v-for="input in visibleInputs"
        :key="input.id"
        :input="input"
        :state="source?.state"
        :target-player="source?.target_player"
        :busy="busy === input.id"
        @use="onUse"
        @rename="onRename"
        @remove="onRemove"
      />
    </div>
  </section>
</template>
