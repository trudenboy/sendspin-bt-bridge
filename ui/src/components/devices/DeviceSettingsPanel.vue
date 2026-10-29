<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAudioStore } from '@/stores/audio'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { getConfig, saveConfig } from '@/api/config'
import { ApiError } from '@/api/client'
import { SbButton, SbSpinner, SbToggle } from '@/kit'
import SettingField from '@/components/settings/SettingField.vue'
import SettingsRow from '@/components/settings/SettingsRow.vue'
import { DEVICE_GROUPS, deviceFieldFor, deviceFieldVisible, type DeviceField } from '@/settings/device'
import { getPath, setPath } from '@/settings/layout'
import { confirmAction } from '@/composables/useActionPolicy'
import { speakerName } from '@/utils/speakerName'
import { useReversibleActions } from '@/composables/useReversibleActions'
import type { Device } from '@/api/types'

/** What the panel needs of a player: a running Device, or a disabled player shaped like one. */
export interface SettingsTarget {
  id: string
  config_id?: string | null
  name?: string | null
  enabled: boolean
  bluetooth?: { mac?: string | null } | null
}

const props = defineProps<{ device: SettingsTarget | Device }>()

const { t } = useI18n()
const bridge = useBridgeStore()
const audio = useAudioStore()
const deviceStore = useDeviceStore()
const notifications = useNotificationStore()
const reversible = useReversibleActions()

type Entry = Record<string, unknown>

const config = ref<Record<string, unknown> | null>(null)
const entry = ref<Entry | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const showAdvanced = ref(false)
try {
  showAdvanced.value = localStorage.getItem('sendspin-ui:settings-advanced') === '1'
} catch {
  /* default off */
}

// The speaker's entry in the configuration; its runtime id may differ.
const configId = computed(() => props.device.config_id ?? props.device.id)
const adapterChoices = computed(() => bridge.adapters.map((a) => ({ value: a.id, label: a.name ? `${a.id} — ${a.name}` : a.id })))
// Outputs another player already plays to are not offered; the saved one always is.
const sinkChoices = computed(() => {
  const current = String(getPath(entry.value, 'backend.sink') ?? '')
  const list = audio.sinks
    .filter((s) => !s.in_use_by || s.in_use_by === configId.value)
    .map((s) => ({ value: s.name, label: s.description && s.description !== s.name ? `${s.description} — ${s.name}` : s.name }))
  if (current && !list.some((o) => o.value === current)) list.unshift({ value: current, label: current })
  return list
})
const choicesFor = (f: DeviceField) => (f.optionsFrom === 'sinks' ? sinkChoices.value : adapterChoices.value)
// Timing is applied live from its own tab.
const groups = computed(() => DEVICE_GROUPS.filter((g) => g.id !== 'sync'))
// The port the running speaker really listens on, when none is pinned.
const effectivePort = computed(() => entry.value?._effective_listen_port)
const fieldFor = (f: DeviceField): DeviceField => {
  const shown = deviceFieldFor(f, entry.value ?? {})
  return f.key === 'listen_port' && effectivePort.value != null ? { ...shown, placeholder: String(effectivePort.value) } : shown
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const doc = (await getConfig()) as Record<string, unknown>
    const found = ((doc.players as Entry[] | undefined) ?? []).find((p) => p.id === configId.value)
    config.value = doc
    // Deep copy: the backend is nested and edited in place.
    entry.value = found ? (JSON.parse(JSON.stringify(found)) as Entry) : null
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    loading.value = false
  }
}
watch(configId, load, { immediate: true })
watch(
  () => (entry.value ? getPath(entry.value, 'backend.type') : null),
  (type) => {
    if (type === 'pulse_sink') void audio.fetchSinks()
  },
  { immediate: true },
)

const read = (key: string) => getPath(entry.value, key)

/*
 * Auto-save (like Home Assistant's entity settings): a change is applied at
 * once, saved after a short pause, and answered with "Saved" and Undo next to
 * the field. A failed save puts the old value back and shows the server's
 * reason. What is pending is saved when the panel closes.
 */
const SAVE_DELAY_MS = 600
const SAVED_SHOWN_MS = 8000
type FieldSave = { kind: 'saved' | 'error'; message?: string; undoable?: boolean }
const fieldSave = reactive<Record<string, FieldSave>>({})
const pending = new Map<string, unknown>() // key -> the value before this change
let saveTimer: ReturnType<typeof setTimeout> | null = null
let queue: Promise<void> = Promise.resolve()
const clearTimers = new Map<string, ReturnType<typeof setTimeout>>()

function write(key: string, value: unknown) {
  if (!entry.value) return
  if (!pending.has(key)) pending.set(key, getPath(entry.value, key))
  setPath(entry.value, key, value === null || value === '' ? undefined : value)
  delete fieldSave[key]
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(flush, SAVE_DELAY_MS)
}

function flush() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = null
  if (!pending.size) return
  const batch = new Map(pending)
  pending.clear()
  queue = queue.then(() => persist(batch, true))
  return queue
}

async function persist(batch: Map<string, unknown>, undoable: boolean) {
  if (!config.value || !entry.value) return
  try {
    // ``_effective_*`` is read-only display data; it is kept for display and dropped here.
    const mine = Object.fromEntries(Object.entries(entry.value).filter(([k]) => !k.startsWith('_')))
    const players = ((config.value.players as Entry[] | undefined) ?? []).map((p) => (p.id === configId.value ? mine : p))
    const doc = { ...config.value, players }
    const result = await saveConfig(doc as never)
    config.value = doc
    const restart = ((result?.reconfig ?? {}) as { restart_required?: unknown[] }).restart_required?.length
    if (restart) notifications.warning(t('settings.restartNeeded'))
    for (const key of batch.keys()) showSaved(key, undoable ? batch.get(key) : undefined, undoable)
  } catch (e) {
    const reason = e instanceof ApiError ? e.message : t('drawer.config.saveFailed')
    if (!alive) notifications.error(reason) // the panel is gone: nobody sees the field
    // The server refused: the field goes back to what is saved.
    for (const [key, before] of batch) {
      if (entry.value) setPath(entry.value, key, before)
      fieldSave[key] = { kind: 'error', message: reason }
    }
  }
}

function showSaved(key: string, before: unknown, undoable: boolean) {
  fieldSave[key] = { kind: 'saved', undoable }
  if (undoable) undoValues.set(key, before)
  const old = clearTimers.get(key)
  if (old) clearTimeout(old)
  clearTimers.set(key, setTimeout(() => delete fieldSave[key], SAVED_SHOWN_MS))
}

const undoValues = new Map<string, unknown>()
function undo(key: string) {
  if (!entry.value || !undoValues.has(key)) return
  const before = undoValues.get(key)
  const current = getPath(entry.value, key)
  undoValues.delete(key)
  setPath(entry.value, key, before)
  delete fieldSave[key]
  queue = queue.then(() => persist(new Map([[key, current]]), false))
}

let alive = true
onBeforeUnmount(() => {
  alive = false
  clearTimers.forEach(clearTimeout)
  void flush()
})

function visible(f: DeviceField) {
  return (showAdvanced.value || !f.advanced) && deviceFieldVisible(f, entry.value ?? {})
}

const removing = ref(false)

/** The same removal as the speaker menu, at the end of its settings (as on a Home Assistant device page). */
async function removeSpeaker() {
  const name = speakerName(props.device.name, props.device.bluetooth?.mac ?? props.device.id)
  const ok = await confirmAction('speaker.remove', {
    title: t('speaker.removeTitle', { name }),
    consequences: [
      t('consequence.stopsPlayback', { names: name }),
      t('consequence.leavesMa', { names: name }),
      t('consequence.unpairs'),
    ],
    confirmLabel: t('speaker.remove'),
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

// Saved state: a speaker follows the bridge's playout sync unless it opted out.
const playoutSync = computed(() => entry.value?.playout_sync_enabled !== false)

async function togglePlayoutSync(on: boolean) {
  try {
    await deviceStore.setPlayoutSync(props.device.id, on)
    await load()
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('drawer.config.saveFailed'))
  }
}

async function toggleEnabled(enabled: boolean) {
  try {
    if (!enabled) {
      const result = (await reversible.disable(props.device.id, speakerName(props.device.name))) as { restart_required?: boolean } | undefined
      await bridge.refresh()
      if (result?.restart_required) notifications.warning(t('settings.restartNeeded'))
      return
    }
    const result = (await deviceStore.setEnabled(props.device.id, enabled)) as { restart_required?: boolean } | undefined
    await bridge.refresh()
    if (result?.restart_required) notifications.warning(t('settings.restartNeeded'))
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
        <SettingsRow :label="t('drawer.config.playoutSync')" :help="t('drawer.config.playoutSyncHelp')">
          <SbToggle id="playout-sync-toggle" :model-value="playoutSync" @update:model-value="togglePlayoutSync" />
        </SettingsRow>
        <SettingsRow v-if="device.bluetooth?.mac" :label="t('drawer.config.mac')">
          <span class="font-mono text-sm text-text-secondary">{{ device.bluetooth?.mac }}</span>
        </SettingsRow>
      </div>

      <section v-for="g in groups" :key="g.id" class="mt-4">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-text-secondary">{{ t(`settings.deviceGroups.${g.id}`) }}</h3>
        <div class="divide-y divide-border">
          <template v-for="f in g.fields" :key="f.key">
            <SettingField
              v-if="visible(f)"
              :field="fieldFor(f)"
              i18n-base="settings.device"
              :read="read"
              :write="write"
              :dynamic-options="choicesFor(f)"
              :save-state="fieldSave[f.key]"
              @undo="undo(f.key)"
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
    </template>
  </div>
</template>
