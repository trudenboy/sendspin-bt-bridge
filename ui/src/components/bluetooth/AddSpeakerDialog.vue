<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useBridgeStore } from '@/stores/bridge'
import { playClick } from '@/api/calibration'
import { ApiError } from '@/api/client'
import { SbBadge, SbButton, SbDialog, SbInput, SbTabs } from '@/kit'
import AudioOutputPicker from './AudioOutputPicker.vue'
import PairedDevicesList from './PairedDevicesList.vue'
import LongOperation from '@/components/LongOperation.vue'
import { CheckCircle2, RotateCw, Speaker } from 'lucide-vue-next'
import type { ScanDevice } from '@/api/types'

/**
 * Adding a speaker as one guided flow: find it, pair, name it and its room,
 * hear it. The scan starts by itself; the compatibility switches and the
 * adapter choice stay out of the way until needed.
 */
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()

const { t } = useI18n()
const bt = useBluetoothStore()
const bridge = useBridgeStore()

type Step = 'find' | 'name' | 'test' | 'paired'
const step = ref<Step>('find')
// A speaker over Bluetooth, or an audio output of this computer.
const mode = ref<'bluetooth' | 'audio'>('bluetooth')
const audioStage = ref<'pick' | 'name' | 'done'>('pick')
const tabs = computed(() => [
  { id: 'bluetooth', label: t('addSpeaker.tabs.bluetooth') },
  { id: 'audio', label: t('addSpeaker.tabs.audio') },
])
const showTabs = computed(() => (mode.value === 'audio' ? audioStage.value === 'pick' : step.value === 'find'))
const adapter = ref('')
const audioOnly = ref(true)
const options = ref({ quiesceAdapter: false, noInputNoOutputAgent: false, allowHfpProfile: false })
const chosen = ref<ScanDevice | null>(null)
const name = ref('')
const room = ref('')
const error = ref('')
const saving = ref(false)
const testing = ref(false)

const adapters = computed(() => bridge.adapters.filter((a) => a.powered !== false))
const onBridge = computed(() => new Set(bridge.devices.map((d) => d.bluetooth?.mac?.toUpperCase())))
const added = computed(() =>
  chosen.value ? bridge.devices.find((d) => d.bluetooth?.mac?.toUpperCase() === chosen.value!.mac.toUpperCase()) : undefined,
)

function hciOf(result: ScanDevice) {
  return bridge.adapters.find((a) => a.mac === result.adapter)?.id ?? adapter.value
}

const scanned = ref(false)

async function scan() {
  if (!adapter.value) return
  error.value = ''
  scanned.value = true
  await bt.startScan(adapter.value, audioOnly.value)
  if (bt.scanError) error.value = bt.scanError
}

function reset() {
  mode.value = 'bluetooth'
  audioStage.value = 'pick'
  step.value = 'find'
  chosen.value = null
  name.value = ''
  room.value = ''
  error.value = ''
  scanned.value = false
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    reset()
    if (!adapter.value && adapters.value.length) adapter.value = adapters.value[0]!.id
  },
  { immediate: true },
)

async function pick(result: ScanDevice) {
  error.value = ''
  chosen.value = result
  const ok = await bt.pairDevice(result.mac, { adapter: hciOf(result), ...options.value })
  if (!ok) {
    chosen.value = null
    if (!bt.pairError) return // cancelled by the operator
    error.value = t('bluetooth.scan.pairFailed', { reason: bt.pairError ?? '' })
    chosen.value = null
    return
  }
  name.value = result.name && result.name !== result.mac ? result.name : ''
  step.value = 'name'
}

async function addToBridge() {
  if (!chosen.value) return
  saving.value = true
  error.value = ''
  try {
    await bt.addToBridge(chosen.value.mac, name.value.trim() || chosen.value.mac, hciOf(chosen.value), { room: room.value })
    step.value = 'test'
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

async function testSound() {
  if (!added.value) return
  testing.value = true
  error.value = ''
  try {
    await playClick(added.value.id)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    testing.value = false
  }
}

function close() {
  emit('update:open', false)
}

function signal(rssi?: number | null): 'success' | 'warning' | 'error' | 'neutral' {
  if (rssi == null) return 'neutral'
  return rssi > -60 ? 'success' : rssi > -75 ? 'warning' : 'error'
}
</script>

<template>
  <SbDialog :model-value="open" :title="t('addSpeaker.title')" size="md" @update:model-value="(v: unknown) => !v && close()">
    <SbTabs v-if="showTabs" :model-value="mode" :tabs="tabs" class="mb-4" @update:model-value="(v: unknown) => (mode = v as typeof mode)" />

    <AudioOutputPicker v-if="mode === 'audio'" @stage="(s) => (audioStage = s)" @close="close" />

    <!-- 1. Find -->
    <div v-else-if="step === 'find'" class="space-y-4">
      <p class="text-sm text-text-secondary">{{ t('addSpeaker.findHelp') }}</p>

      <div class="flex flex-wrap items-center gap-3">
        <label v-if="adapters.length > 1" class="flex items-center gap-2 text-sm text-text-secondary">
          {{ t('bluetooth.scan.adapter') }}
          <select
            v-model="adapter"
            class="h-9 rounded-(--radius-input) border border-border-strong bg-surface-card px-2 text-sm text-text-primary"
            :disabled="bt.scanning"
          >
            <option v-for="a in adapters" :key="a.id" :value="a.id">{{ a.name ? `${a.name} · ${a.id}` : a.id }}</option>
          </select>
        </label>
        <SbButton class="ml-auto" variant="outline" size="sm" :loading="bt.scanning" :disabled="!adapter" @click="scan">
          <template #icon-left><RotateCw class="size-4" /></template>
          {{ bt.scanning ? t('bluetooth.scan.scanning') : (scanned ? t('addSpeaker.scanAgain') : t('addSpeaker.scan')) }}
        </SbButton>
      </div>

      <LongOperation
        v-if="bt.pairing"
        :label="t('addSpeaker.pairing', { name: chosen?.name && chosen.name !== chosen.mac ? chosen.name : chosen?.mac })"
        :started-at="bt.pairStartedAt"
        cancellable
        :cancelling="bt.cancelling"
        @cancel="bt.cancelPairing()"
      />

      <LongOperation
        v-if="bt.scanning && !bt.scanResults.length"
        :label="t('addSpeaker.looking')"
        :started-at="bt.scanStartedAt"
        cancellable
        :cancelling="bt.cancelling"
        @cancel="bt.cancelScan()"
      />

      <ul v-else-if="bt.scanResults.length" class="divide-y divide-border rounded-(--radius-card) border border-border">
        <li v-for="r in bt.scanResults" :key="r.mac" class="flex items-center gap-3 px-3 py-2.5">
          <Speaker class="size-5 shrink-0 text-text-secondary" aria-hidden="true" />
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-medium text-text-primary">
              {{ r.name && r.name !== r.mac ? r.name : t('bluetooth.scan.unknown') }}
            </p>
            <p class="font-mono text-xs text-text-tertiary">{{ r.mac }}</p>
          </div>
          <SbBadge v-if="r.rssi_dbm != null" :tone="signal(r.rssi_dbm)" size="sm">{{ t('addSpeaker.signal.' + signal(r.rssi_dbm)) }}</SbBadge>
          <SbBadge v-if="onBridge.has(r.mac.toUpperCase())" tone="success" size="sm">{{ t('bluetooth.scan.added') }}</SbBadge>
          <SbButton
            v-else
            size="sm"
            :loading="bt.pairing && bt.pairTarget === r.mac"
            :disabled="bt.pairing"
            @click="pick(r)"
          >{{ t('addSpeaker.add') }}</SbButton>
        </li>
      </ul>

      <p v-else-if="!bt.scanning" class="py-6 text-center text-sm text-text-secondary">
        {{ scanned ? t('bluetooth.scan.noDevices') : t('addSpeaker.scanHint') }}
      </p>

      <details class="text-sm">
        <summary class="cursor-pointer text-text-secondary">{{ t('addSpeaker.trouble') }}</summary>
        <div class="mt-2 space-y-1.5 pl-1">
          <label class="flex items-start gap-2">
            <input v-model="audioOnly" type="checkbox" class="mt-0.5 accent-primary" />
            {{ t('bluetooth.audioOnly') }}
          </label>
          <label class="flex items-start gap-2">
            <input v-model="options.noInputNoOutputAgent" type="checkbox" class="mt-0.5 accent-primary" />
            {{ t('bluetooth.scan.noIo') }}
          </label>
          <label class="flex items-start gap-2">
            <input v-model="options.quiesceAdapter" type="checkbox" class="mt-0.5 accent-primary" />
            {{ t('bluetooth.scan.quiesce') }}
          </label>
          <label class="flex items-start gap-2">
            <input v-model="options.allowHfpProfile" type="checkbox" class="mt-0.5 accent-primary" />
            {{ t('bluetooth.scan.hfp') }}
          </label>
          <button type="button" class="mt-1 text-primary-text hover:underline" @click="step = 'paired'">
            {{ t('addSpeaker.alreadyPaired') }}
          </button>
        </div>
      </details>
    </div>

    <!-- 2. Name and room -->
    <form v-else-if="step === 'name'" class="space-y-4" @submit.prevent="addToBridge">
      <p class="flex items-center gap-2 text-sm text-text-primary">
        <CheckCircle2 class="size-5 text-success" aria-hidden="true" />
        {{ t('addSpeaker.paired') }}
      </p>
      <SbInput v-model="name" :label="t('settings.device.name.label')" :hint="t('settings.device.name.help')" :placeholder="chosen?.mac" />
      <SbInput v-model="room" :label="t('settings.device.room_name.label')" :hint="t('addSpeaker.roomHint')" />
    </form>

    <!-- 3. Hear it -->
    <div v-else-if="step === 'test'" class="space-y-4">
      <p class="flex items-center gap-2 text-sm text-text-primary">
        <CheckCircle2 class="size-5 text-success" aria-hidden="true" />
        {{ t('addSpeaker.addedTitle', { name: name || chosen?.mac }) }}
      </p>
      <p class="text-sm text-text-secondary">{{ added?.audio.has_sink ? t('addSpeaker.testHelp') : t('addSpeaker.waiting') }}</p>
    </div>

    <!-- Already paired speakers -->
    <div v-else class="space-y-3">
      <button type="button" class="text-sm text-primary-text hover:underline" @click="step = 'find'">← {{ t('addSpeaker.backToScan') }}</button>
      <PairedDevicesList />
    </div>

    <LongOperation
      v-if="error"
      class="mt-3"
      status="error"
      :label="error"
      :error="error"
      :next-step="step === 'find' ? t('addSpeaker.errorNext') : null"
      :retryable="step === 'find' && scanned && adapter !== ''"
      @retry="scan"
    />

    <template #footer>
      <template v-if="mode === 'audio'">
        <SbButton v-if="audioStage !== 'done'" variant="ghost" @click="close">{{ t('common.close') }}</SbButton>
      </template>
      <template v-else-if="step === 'name'">
        <SbButton variant="ghost" @click="reset">{{ t('common.cancel') }}</SbButton>
        <SbButton :loading="saving" @click="addToBridge">{{ t('addSpeaker.addToBridge') }}</SbButton>
      </template>
      <template v-else-if="step === 'test'">
        <SbButton variant="outline" :loading="testing" :disabled="!added?.audio.has_sink" @click="testSound">{{ t('addSpeaker.testSound') }}</SbButton>
        <SbButton @click="close">{{ t('addSpeaker.done') }}</SbButton>
      </template>
      <SbButton v-else variant="ghost" @click="close">{{ t('common.close') }}</SbButton>
    </template>
  </SbDialog>
</template>
