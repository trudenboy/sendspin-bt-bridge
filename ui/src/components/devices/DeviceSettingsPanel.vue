<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { getConfig, saveConfig } from '@/api/config'
import { ApiError } from '@/api/client'
import { SbButton, SbSpinner, SbToggle } from '@/kit'
import SettingField from '@/components/settings/SettingField.vue'
import SettingsRow from '@/components/settings/SettingsRow.vue'
import { DEVICE_GROUPS } from '@/settings/device'
import { confirmDialog } from '@/composables/useConfirm'
import { speakerName } from '@/utils/speakerName'
import type { Device } from '@/api/types'

const props = defineProps<{ device: Device }>()

const { t } = useI18n()
const bridge = useBridgeStore()
const deviceStore = useDeviceStore()
const notifications = useNotificationStore()

type Entry = Record<string, unknown>

const config = ref<Record<string, unknown> | null>(null)
const entry = ref<Entry | null>(null)
const original = ref('')
const loading = ref(false)
const loadError = ref<string | null>(null)
const saving = ref(false)
const showAdvanced = ref(false)
try {
  showAdvanced.value = localStorage.getItem('sendspin-ui:settings-advanced') === '1'
} catch {
  /* default off */
}

const mac = computed(() => props.device.bluetooth.mac?.toUpperCase() ?? '')
const dirty = computed(() => entry.value !== null && JSON.stringify(entry.value) !== original.value)
const adapterChoices = computed(() => bridge.adapters.map((a) => ({ value: a.id, label: a.name ? `${a.id} — ${a.name}` : a.id })))
// Timing is applied live from its own tab.
const groups = computed(() => DEVICE_GROUPS.filter((g) => g.id !== 'sync'))

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const doc = (await getConfig()) as Record<string, unknown>
    const found = ((doc.BLUETOOTH_DEVICES as Entry[] | undefined) ?? []).find(
      (e) => String(e.mac ?? '').toUpperCase() === mac.value,
    )
    config.value = doc
    entry.value = found ? { ...found } : null
    original.value = JSON.stringify(entry.value)
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
watch(() => props.device.id, load, { immediate: true })

const read = (key: string) => entry.value?.[key]
function write(key: string, value: unknown) {
  if (!entry.value) return
  if (value === null || value === '') delete entry.value[key]
  else entry.value[key] = value
}

function visible(f: (typeof DEVICE_GROUPS)[number]['fields'][number]) {
  return (showAdvanced.value || !f.advanced) && (!f.visible || f.visible(entry.value ?? {}))
}

/** Writes this speaker's entry back into the full document and saves it. */
async function save() {
  if (!config.value || !entry.value) return
  saving.value = true
  try {
    const devices = ((config.value.BLUETOOTH_DEVICES as Entry[] | undefined) ?? []).map((e) =>
      String(e.mac ?? '').toUpperCase() === mac.value ? entry.value! : e,
    )
    const result = await saveConfig({ ...config.value, BLUETOOTH_DEVICES: devices } as never)
    const restart = ((result?.reconfig ?? {}) as { restart_required?: unknown[] }).restart_required?.length
    notifications.success(t(restart ? 'settings.restartNeeded' : 'drawer.config.saved'))
    await load()
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('drawer.config.saveFailed'))
  } finally {
    saving.value = false
  }
}

const removing = ref(false)

/** The same removal as the speaker menu, at the end of its settings (as on a Home Assistant device page). */
async function removeSpeaker() {
  const name = speakerName(props.device.name, props.device.bluetooth.mac ?? props.device.id)
  const ok = await confirmDialog({
    title: t('speaker.removeTitle', { name }),
    message: t('speaker.removeMessage'),
    confirmLabel: t('speaker.remove'),
    danger: true,
  })
  if (!ok) return
  removing.value = true
  try {
    await deviceStore.remove(props.device.id)
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  } finally {
    removing.value = false
  }
}

async function toggleEnabled(enabled: boolean) {
  try {
    await deviceStore.setEnabled(props.device.id, enabled)
  } catch {
    notifications.error(t('device.actions.enableFailed'))
  }
}
</script>

<template>
  <div class="py-2">
    <div v-if="loading && !entry" class="flex justify-center py-10"><SbSpinner /></div>
    <div v-else-if="loadError" class="space-y-2 py-6 text-sm" role="alert">
      <p class="text-text-primary">{{ t('settings.loadFailed') }} <span class="text-text-secondary">{{ loadError }}</span></p>
      <SbButton variant="outline" size="sm" @click="load">{{ t('settings.retry') }}</SbButton>
    </div>
    <p v-else-if="!entry" class="py-6 text-sm text-text-secondary">{{ t('drawer.config.notConfigured') }}</p>
    <template v-else>
      <div class="divide-y divide-border">
        <SettingsRow :label="t('drawer.config.enabled')" :help="t('drawer.config.enabledHelp')">
          <SbToggle :model-value="device.enabled" @update:model-value="toggleEnabled" />
        </SettingsRow>
        <SettingsRow :label="t('drawer.config.mac')">
          <span class="font-mono text-sm text-text-secondary">{{ device.bluetooth.mac }}</span>
        </SettingsRow>
      </div>

      <section v-for="g in groups" :key="g.id" class="mt-4">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-text-secondary">{{ t(`settings.deviceGroups.${g.id}`) }}</h3>
        <div class="divide-y divide-border">
          <template v-for="f in g.fields" :key="f.key">
            <SettingField
              v-if="visible(f)"
              :field="f"
              i18n-base="settings.device"
              :read="read"
              :write="write"
              :dynamic-options="adapterChoices"
            />
          </template>
        </div>
      </section>

      <label class="mt-4 flex items-center gap-2 text-sm text-text-secondary">
        <SbToggle v-model="showAdvanced" size="sm" />
        {{ t('settings.showAdvanced') }}
      </label>

      <section class="mt-6 rounded-(--radius-card) border border-error/30 p-4">
        <p class="text-sm font-medium text-text-primary">{{ t('speaker.remove') }}</p>
        <p class="mt-0.5 text-[13px] text-text-secondary">{{ t('speaker.removeMessage') }}</p>
        <SbButton class="mt-3" variant="danger" size="sm" :loading="removing" @click="removeSpeaker">{{ t('speaker.remove') }}…</SbButton>
      </section>

      <div class="sticky bottom-0 -mx-1 mt-4 flex justify-end gap-2 bg-surface-card px-1 py-3">
        <SbButton variant="ghost" size="sm" :disabled="!dirty" @click="load">{{ t('settings.discard') }}</SbButton>
        <SbButton size="sm" :disabled="!dirty" :loading="saving" @click="save">{{ t('settings.save') }}</SbButton>
      </div>
    </template>
  </div>
</template>
