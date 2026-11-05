<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bluetooth, Plus, Radio, RotateCw } from 'lucide-vue-next'
import { SbBadge, SbButton, SbInput } from '@/kit'
import { useBridgeStore } from '@/stores/bridge'
import { useHardwareStore } from '@/stores/hardware'
import { addLocalSource, getSourceInputs, selectSource } from '@/api/source'
import type { SourceInputStatus } from '@/api/types'

/** Pair a Bluetooth source or save a local audio input; neither needs MA. */
const emit = defineEmits<{ close: []; added: [] }>()

const { t } = useI18n()
const bridge = useBridgeStore()
const hardware = useHardwareStore()

const mode = ref<'bluetooth' | 'local'>('bluetooth')
const allInputs = ref<SourceInputStatus[]>([])
const candidates = ref<SourceInputStatus[]>([])
const captureAvailable = ref(true)
const captureKind = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const showMonitors = ref(false)
const name = ref('')
const manualNode = ref('')
const added = ref<string | null>(null)
const pairing = ref(false)
const knownBluetooth = ref(0)
let pairPoll: ReturnType<typeof setInterval> | undefined

const source = computed(() => bridge.bridge?.sendspin_source ?? null)
const bluetoothInputs = computed(() => allInputs.value.filter((input) => input.kind === 'bluetooth'))
const localCandidates = computed(() => candidates.value)
const timer = computed(() => hardware.pairingSecondsLeft)

function clock(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const answer = await getSourceInputs(showMonitors.value)
    allInputs.value = answer.inputs ?? []
    candidates.value = answer.local_candidates ?? []
    captureAvailable.value = answer.capture_available !== false
    captureKind.value = answer.capture_kind ?? ''
  } catch {
    error.value = t('addSpeaker.source.localFailed')
  } finally {
    loading.value = false
  }
}

async function refreshInputs() {
  try {
    const answer = await getSourceInputs(showMonitors.value)
    allInputs.value = answer.inputs ?? []
    candidates.value = answer.local_candidates ?? []
  } catch {
    /* the poll retries; the visible list stays as it was */
  }
}

function stopPolling() {
  if (pairPoll !== undefined) clearInterval(pairPoll)
  pairPoll = undefined
}

async function pair() {
  error.value = ''
  added.value = null
  knownBluetooth.value = bluetoothInputs.value.length
  pairing.value = true
  await hardware.startPairingWindow()
  if (hardware.sourceError) {
    pairing.value = false
    error.value = t('addSpeaker.source.pairFailed')
    return
  }
  stopPolling()
  pairPoll = setInterval(() => void refreshInputs(), 2000)
}

async function stopPairing() {
  pairing.value = false
  stopPolling()
  await hardware.stopPairingWindow()
  await bridge.refresh()
  await refreshInputs()
}

async function add() {
  const node = manualNode.value.trim()
  if (!node) return
  busy.value = true
  error.value = ''
  added.value = null
  try {
    await addLocalSource(node, name.value.trim() || null, showMonitors.value)
    added.value = name.value.trim() || node
    name.value = ''
    manualNode.value = ''
    await bridge.refresh()
    await load()
    emit('added')
  } catch {
    error.value = t('addSpeaker.source.addFailed')
  } finally {
    busy.value = false
  }
}

function choose(candidate: SourceInputStatus) {
  mode.value = 'local'
  manualNode.value = candidate.node ?? ''
  name.value = candidate.name
}

onMounted(load)
onBeforeUnmount(() => {
  stopPolling()
  if (pairing.value) void hardware.stopPairingWindow()
})

watch(showMonitors, () => void load())
watch(
  () => bluetoothInputs.value.length,
  (count) => {
    if (!pairing.value || count <= knownBluetooth.value) return
    pairing.value = false
    stopPolling()
    const fresh = bluetoothInputs.value[bluetoothInputs.value.length - 1]
    added.value = fresh?.name ?? null
    if (fresh && !allInputs.value.some((entry) => entry.active)) {
      // The first source added by pairing becomes active, without enabling
      // the source itself; it starts when the operator flips the switch.
      void selectSource(fresh.id).then(() => bridge.refresh()).then(load).catch(() => {})
    } else {
      void load()
    }
  },
)
watch(timer, (value) => {
  if (pairing.value && value === 0) {
    pairing.value = false
    stopPolling()
  }
})
</script>

<template>
  <div data-source-picker class="space-y-4">
    <div class="flex flex-wrap items-center gap-2">
      <p class="text-sm text-text-secondary">{{ t('addSpeaker.source.help') }}</p>
      <SbBadge tone="warning" size="sm">{{ t('common.experimental') }}</SbBadge>
    </div>

    <div class="flex flex-wrap gap-2">
      <SbButton :variant="mode === 'bluetooth' ? 'primary' : 'outline'" size="sm" data-source-mode-bluetooth @click="mode = 'bluetooth'">
        <template #icon-left><Bluetooth class="size-4" aria-hidden="true" /></template>
        {{ t('addSpeaker.source.bluetooth') }}
      </SbButton>
      <SbButton :variant="mode === 'local' ? 'primary' : 'outline'" size="sm" data-source-mode-local @click="mode = 'local'">
        <template #icon-left><Radio class="size-4" aria-hidden="true" /></template>
        {{ t('addSpeaker.source.local') }}
      </SbButton>
    </div>

    <p v-if="source && !source.ma_paired" data-source-ma-hint class="rounded-(--radius-card) border border-info/30 bg-info/10 px-3 py-2 text-sm text-text-secondary">
      {{ t('addSpeaker.source.maHint') }}
      <a href="#settings-musicAssistant" class="text-primary-text underline-offset-2 hover:underline">{{ t('nav.settings') }}</a>
    </p>

    <template v-if="mode === 'bluetooth'">
      <div v-if="pairing" data-source-pairing class="rounded-(--radius-card) border border-info/40 bg-info/10 px-3 py-2">
        <p class="text-sm text-text-primary">{{ t('addSpeaker.source.pairing', { time: clock(timer) }) }}</p>
        <p class="mt-1 text-xs text-text-secondary">{{ t('addSpeaker.source.pairingHint') }}</p>
        <SbButton class="mt-2" size="sm" variant="outline" data-source-pairing-stop @click="stopPairing">
          {{ t('addSpeaker.source.stopPairing') }}
        </SbButton>
      </div>
      <div v-else class="flex flex-wrap items-center gap-2">
        <SbButton size="sm" variant="outline" data-source-pair :loading="hardware.sourceBusy" @click="pair">
          <template #icon-left><Bluetooth class="size-4" aria-hidden="true" /></template>
          {{ t('addSpeaker.source.pair') }}
        </SbButton>
        <span class="text-xs text-text-secondary">{{ t('addSpeaker.source.pairHint') }}</span>
      </div>

      <ul v-if="bluetoothInputs.length" data-source-bluetooth-list class="divide-y divide-border rounded-(--radius-card) border border-border">
        <li v-for="input in bluetoothInputs" :key="input.id" class="flex flex-wrap items-center gap-2 px-3 py-2">
          <Bluetooth class="size-4 shrink-0 text-text-secondary" aria-hidden="true" />
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-medium text-text-primary">{{ input.name || input.mac }}</p>
            <p class="font-mono text-xs text-text-secondary">{{ input.mac }}</p>
          </div>
          <SbBadge v-if="input.active" size="sm" tone="success">{{ t('devices.sources.active') }}</SbBadge>
          <SbBadge v-else-if="input.connected" size="sm">{{ t('devices.sources.connected') }}</SbBadge>
          <SbBadge v-else size="sm" tone="neutral">{{ t('devices.sources.paired') }}</SbBadge>
        </li>
      </ul>
      <p v-else class="text-sm text-text-secondary">{{ t('addSpeaker.source.noBluetooth') }}</p>
    </template>

    <template v-else>
      <div class="flex flex-wrap items-center justify-between gap-2">
        <label class="flex items-center gap-2 text-sm text-text-secondary">
          <input v-model="showMonitors" type="checkbox" class="accent-primary" data-source-show-monitors />
          {{ t('addSpeaker.source.showMonitors') }}
        </label>
        <SbButton size="sm" variant="ghost" :loading="loading" data-source-local-refresh @click="load">
          <template #icon-left><RotateCw class="size-4" aria-hidden="true" /></template>
          {{ t('addSpeaker.source.refresh') }}
        </SbButton>
      </div>

      <p v-if="!captureAvailable" data-source-capture-unavailable class="rounded-(--radius-card) border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-text-secondary">
        {{ t('addSpeaker.source.captureUnavailable', { kind: captureKind || 'audio' }) }}
      </p>

      <ul v-if="localCandidates.length" data-source-local-list class="divide-y divide-border rounded-(--radius-card) border border-border">
        <li v-for="candidate in localCandidates" :key="candidate.id" class="flex flex-wrap items-center gap-2 px-3 py-2">
          <Radio class="size-4 shrink-0 text-text-secondary" aria-hidden="true" />
          <div class="min-w-0 flex-1">
            <p class="truncate text-sm font-medium text-text-primary">{{ candidate.name || candidate.node }}</p>
            <p class="truncate font-mono text-xs text-text-secondary">{{ candidate.node }}</p>
          </div>
          <SbBadge v-if="candidate.monitor" size="sm" tone="warning">{{ t('devices.sources.monitor') }}</SbBadge>
          <SbButton size="sm" variant="outline" :disabled="!captureAvailable" @click="choose(candidate)">
            {{ t('addSpeaker.source.choose') }}
          </SbButton>
        </li>
      </ul>
      <p v-else class="text-sm text-text-secondary">{{ t('addSpeaker.source.noLocal') }}</p>

      <div class="rounded-(--radius-card) border border-border p-3">
        <SbInput v-model="manualNode" data-source-node :label="t('addSpeaker.source.node')" :hint="t('addSpeaker.source.nodeHint')" />
        <div class="mt-3 flex flex-wrap items-end gap-2">
          <SbInput v-model="name" data-source-name class="min-w-0 flex-1" :label="t('addSpeaker.source.name')" />
          <SbButton data-source-local-add :loading="busy" :disabled="!manualNode.trim() || !captureAvailable" @click="add">
            <template #icon-left><Plus class="size-4" aria-hidden="true" /></template>
            {{ t('addSpeaker.source.add') }}
          </SbButton>
        </div>
        <p class="mt-2 text-xs text-text-secondary">{{ t('addSpeaker.source.manualHint') }}</p>
      </div>
    </template>

    <p v-if="added" data-source-added class="text-sm text-success">
      {{ t('addSpeaker.source.added', { name: added }) }}
    </p>
    <p v-if="error" data-source-error role="alert" class="text-sm text-error">{{ error }}</p>
  </div>
</template>
