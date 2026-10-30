<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { closeSourcePairing, getStatus, openSourcePairing, setSourceEnabled, setSourcePlayer } from '@/api/status'
import { useConfigStore } from '@/stores/config'
import { SbBadge, SbButton } from '@/kit'
import SettingsRow from './SettingsRow.vue'

const { t } = useI18n()
const configStore = useConfigStore()

interface SourceState {
  enabled: boolean
  port: number | null
  phone: string | null
  streaming: boolean
  error: string | null
  pairing_seconds_left: number
  attached_devices: string[]
}

interface Speaker {
  id: string
  name: string
}

const source = ref<SourceState | null>(null)
const speakers = ref<Speaker[]>([])
const chosen = ref('')
const busy = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

async function refresh() {
  try {
    const status = await getStatus()
    source.value = (status.bridge.sendspin_source ?? null) as SourceState | null
    speakers.value = status.devices.map((d) => ({ id: d.id, name: d.name ?? d.id }))
    if (!chosen.value && speakers.value.length) chosen.value = speakers.value[0]!.id
  } catch {
    source.value = null
  }
}

onMounted(() => {
  void refresh()
  timer = setInterval(() => void refresh(), 5000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})

async function openPairing() {
  busy.value = true
  try {
    await openSourcePairing(120)
    await refresh()
  } catch {
    /* the row keeps showing the previous state */
  } finally {
    busy.value = false
  }
}

async function startSource() {
  busy.value = true
  try {
    await setSourceEnabled(true)
    await refresh()
  } catch {
    /* the row keeps showing the previous state */
  } finally {
    busy.value = false
  }
}

async function stopSource() {
  busy.value = true
  try {
    await setSourceEnabled(false)
    await refresh()
  } catch {
    /* ignore */
  } finally {
    busy.value = false
  }
}

async function cancelPairing() {
  busy.value = true
  try {
    await closeSourcePairing()
    await refresh()
  } catch {
    /* ignore */
  } finally {
    busy.value = false
  }
}

const attached = computed(() => source.value?.attached_devices ?? [])
const chosenAttached = computed(() => attached.value.includes(chosen.value))

async function toggleSpeaker() {
  if (!chosen.value) return
  busy.value = true
  try {
    await setSourcePlayer(chosen.value, !chosenAttached.value)
    await refresh()
  } catch {
    /* ignore */
  } finally {
    busy.value = false
  }
}

const state = computed(() => {
  // Configured but not running yet (settings saved, bridge not restarted): still off.
  if (!source.value || !source.value.enabled) return 'off'
  if (source.value.error) return 'error'
  if (source.value.streaming) return 'streaming'
  return 'waiting'
})
const tone = computed(() => (state.value === 'streaming' ? 'success' : state.value === 'off' ? 'neutral' : 'warning'))
const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
const detail = computed(() => {
  const s = source.value
  if (!s) return t('settings.parts.sourceStatus.off')
  if (s.error) return t('settings.parts.sourceStatus.error')
  if (s.pairing_seconds_left > 0) return t('settings.parts.sourceStatus.pairing', { time: clock(s.pairing_seconds_left) })
  if (s.streaming && s.phone) return t('settings.parts.sourceStatus.streaming', { phone: s.phone, port: s.port })
  return t('settings.parts.sourceStatus.waiting', { port: s.port })
})
</script>

<template>
  <SettingsRow :label="t('settings.parts.sourceStatus.label')" :help="detail">
    <div class="flex items-center gap-2">
      <SbBadge :tone="tone" size="sm">{{ t(`settings.parts.sourceStatus.${state}`) }}</SbBadge>
      <template v-if="source?.enabled">
        <SbButton v-if="source.pairing_seconds_left > 0" variant="outline" size="sm" :loading="busy" @click="cancelPairing">
          {{ t('settings.parts.sourceStatus.cancelPairing') }}
        </SbButton>
        <SbButton v-else variant="outline" size="sm" :loading="busy" @click="openPairing">
          {{ t('settings.parts.sourceStatus.pair') }}
        </SbButton>
        <SbButton variant="ghost" size="sm" :loading="busy" @click="stopSource">
          {{ t('settings.parts.sourceStatus.stop') }}
        </SbButton>
      </template>
      <SbButton
        v-else-if="configStore.config?.SENDSPIN_SOURCE"
        variant="outline"
        size="sm"
        :loading="busy"
        @click="startSource"
      >
        {{ t('settings.parts.sourceStatus.startNow') }}
      </SbButton>
    </div>
    <div v-if="source?.enabled && speakers.length" class="mt-2 flex items-center justify-end gap-2">
      <select v-model="chosen" class="h-8 rounded-md border border-border bg-surface px-2 text-sm">
        <option v-for="s in speakers" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
      <SbButton variant="outline" size="sm" :loading="busy" @click="toggleSpeaker">
        {{ chosenAttached ? t('settings.parts.sourceStatus.detach') : t('settings.parts.sourceStatus.attach') }}
      </SbButton>
    </div>
  </SettingsRow>
</template>
