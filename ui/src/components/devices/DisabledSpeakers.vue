<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { ApiError } from '@/api/client'
import { confirmDialog } from '@/composables/useConfirm'
import { speakerName } from '@/utils/speakerName'
import { SbButton } from '@/kit'
import { Trash2 } from 'lucide-vue-next'

/** Speakers kept in the settings but switched off: they have no card, only these rows. */
const { t } = useI18n()
const bridge = useBridgeStore()
const store = useDeviceStore()
const notifications = useNotificationStore()
const busy = ref<string | null>(null)

const speakers = computed(() =>
  (bridge.bridge?.disabled_devices ?? []).filter((d): d is typeof d & { id: string } => !!d.id),
)

async function enable(id: string) {
  busy.value = id
  try {
    const result = (await store.setEnabled(id, true)) as { restart_required?: boolean } | undefined
    await bridge.refresh()
    if (result?.restart_required) notifications.warning(t('settings.restartNeeded'))
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  } finally {
    busy.value = null
  }
}

async function remove(id: string, name: string) {
  const ok = await confirmDialog({
    title: t('speaker.removeTitle', { name }),
    message: t('speaker.removeMessage'),
    confirmLabel: t('speaker.remove'),
    danger: true,
  })
  if (!ok) return
  busy.value = id
  try {
    await store.remove(id)
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <section v-if="speakers.length" class="mt-8" :aria-labelledby="'disabled-speakers'">
    <h2 id="disabled-speakers" class="text-base font-medium text-text-primary">{{ t('speaker.disabledTitle') }}</h2>
    <p class="mt-0.5 text-sm text-text-secondary">{{ t('speaker.disabledHelp') }}</p>
    <ul class="mt-3 divide-y divide-border rounded-(--radius-card) border border-border bg-surface-card">
      <li v-for="d in speakers" :key="d.id" class="flex items-center gap-3 px-4 py-2.5">
        <span class="min-w-0 flex-1 truncate text-sm text-text-primary" :title="d.player_name ?? undefined">
          {{ speakerName(d.player_name, d.mac ?? d.id) }}
        </span>
        <span class="rounded-full tone-neutral px-2 py-0.5 text-xs font-medium">{{ t('device.status.disabled') }}</span>
        <SbButton variant="outline" size="sm" :loading="busy === d.id" @click="enable(d.id)">{{ t('speaker.enable') }}</SbButton>
        <SbButton
          variant="ghost"
          size="sm"
          icon
          :aria-label="t('speaker.remove')"
          :title="t('speaker.remove')"
          @click="remove(d.id, speakerName(d.player_name, d.mac ?? d.id))"
        >
          <Trash2 class="text-error" />
        </SbButton>
      </li>
    </ul>
  </section>
</template>
