<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bluetooth, Plus, Radio, RotateCw } from 'lucide-vue-next'
import { SbBadge, SbButton, SbInput } from '@/kit'
import { useBridgeStore } from '@/stores/bridge'
import { useSourceStore } from '@/stores/source'
import { addLocalSource, getSourceInputs } from '@/api/source'
import type { SourceInputStatus } from '@/api/types'

/** Pair a Bluetooth source or save a local audio input; neither needs MA. */
const emit = defineEmits<{ close: []; added: [] }>()

const { t } = useI18n()
const bridge = useBridgeStore()
const source = useSourceStore()

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
/**
 * The bonds the dialog knew before this window opened; growth above it is the
 * phone that just paired. ``null`` means not measured yet (no trusted list read
 * yet, the list is still loading, or the dialog mounted mid-window).
 */
const knownBluetooth = ref<number | null>(null)
/** At least one list read has succeeded; a failed read must never seed a baseline. */
let listLoaded = false
/** Pair was clicked before a baseline could be measured: the next trusted read measures it. */
let baselinePending = false
/** When the operator asked for the window; a list request begun before it predates the window. */
let windowRequestedAt: number | null = null
/** The phone the source status already knew the first time this window was seen. */
let phoneMacAtWindowSeen: string | null = null
/** The phone this window already reported, so a later status cannot report it twice. */
let reportedPhoneMac: string | null = null
let pairPoll: ReturnType<typeof setInterval> | undefined

const feed = computed(() => source.status)
const bluetoothInputs = computed(() => allInputs.value.filter((input) => input.kind === 'bluetooth'))
const localCandidates = computed(() => candidates.value)
/** The window belongs to the source store: it survives this dialog closing. */
const pairing = computed(() => source.pairingSecondsLeft > 0)
const timer = computed(() => source.pairingSecondsLeft)

function clock(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function bluetoothOnly(inputs: SourceInputStatus[] = allInputs.value) {
  return inputs.filter((input) => input.kind === 'bluetooth')
}

function bluetoothCount(inputs: SourceInputStatus[] = allInputs.value) {
  return bluetoothOnly(inputs).length
}

/**
 * Every bond already listed when the window opens is old; only growth is the
 * phone. A failed read leaves ``allInputs`` empty, so it must never seed: that
 * would turn the first recovered read into a phantom phone.
 */
function seedBaseline(inputs: SourceInputStatus[] = allInputs.value) {
  if (!listLoaded) {
    baselinePending = true
    return
  }
  knownBluetooth.value = bluetoothCount(inputs)
  baselinePending = false
}

/** The phone the server names in its status, if the list carries it and it post-dates the window. */
function namedPhone(inputs: SourceInputStatus[]): SourceInputStatus | undefined {
  const mac = source.status?.paired_phone_mac?.toLowerCase()
  if (!mac || mac === phoneMacAtWindowSeen?.toLowerCase() || mac === reportedPhoneMac?.toLowerCase()) return undefined
  return inputs.find((input) => input.kind === 'bluetooth' && (input.mac ?? '').toLowerCase() === mac)
}

function reportPhone(fresh: SourceInputStatus, inputs: SourceInputStatus[]) {
  knownBluetooth.value = bluetoothCount(inputs)
  baselinePending = false
  reportedPhoneMac = (fresh.mac ?? '').toLowerCase() || null
  stopPolling()
  added.value = fresh.name ?? null
  if (!inputs.some((entry) => entry.active)) {
    // The first source added by pairing becomes active, without enabling
    // the source itself; it starts when the operator flips the switch.
    void source.selectInput(fresh.id).then(load).catch(() => {})
  } else {
    void load()
  }
  // The bond ended the server-side window even before its clock ran out; the
  // fresh status read closes the local countdown at once.
  void bridge.refresh()
}

/** The window is open: decide from a trusted list whether one of its bonds is the phone. */
function observeInputs(inputs: SourceInputStatus[]) {
  if (!pairing.value) return
  const named = namedPhone(inputs)
  if (named) {
    reportPhone(named, inputs)
    return
  }
  if (knownBluetooth.value === null) {
    // First trusted read of this window: everything listed predates it (a
    // dialog mounting mid-window sees the existing bonds for the first time).
    seedBaseline(inputs)
    return
  }
  const bonds = bluetoothOnly(inputs)
  if (bonds.length <= knownBluetooth.value) return
  const fresh = bonds[bonds.length - 1]
  if (fresh) reportPhone(fresh, inputs)
}

async function load() {
  const requestedAt = Date.now()
  loading.value = true
  error.value = ''
  try {
    const answer = await getSourceInputs(showMonitors.value)
    const inputs = answer.inputs ?? []
    listLoaded = true
    // A dialog mounting during an open window sees the existing bonds for the
    // first time; without a baseline every one of them would look newly paired.
    // The same holds for a read that was already loading when Pair was clicked:
    // it predates the window even though it answers before the open does.
    if (baselinePending && windowRequestedAt !== null && requestedAt <= windowRequestedAt) seedBaseline(inputs)
    allInputs.value = inputs
    candidates.value = answer.local_candidates ?? []
    captureAvailable.value = answer.capture_available !== false
    captureKind.value = answer.capture_kind ?? ''
    observeInputs(inputs)
  } catch {
    error.value = t('addSpeaker.source.localFailed')
  } finally {
    loading.value = false
  }
}

async function refreshInputs() {
  try {
    const answer = await getSourceInputs(showMonitors.value)
    const inputs = answer.inputs ?? []
    listLoaded = true
    allInputs.value = inputs
    candidates.value = answer.local_candidates ?? []
    observeInputs(inputs)
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
  windowRequestedAt = Date.now()
  if (loading.value) baselinePending = true
  else seedBaseline()
  try {
    await source.openPairingWindow()
  } catch {
    error.value = t('addSpeaker.source.pairFailed')
    return
  }
  if (source.error) {
    error.value = t('addSpeaker.source.pairFailed')
    return
  }
  // The watcher below starts the input poll as soon as the window is counted.
}

async function stopPairing() {
  stopPolling()
  try {
    await source.closePairingWindow()
  } catch {
    error.value = t('common.error')
  }
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
  // Leaving the dialog stops this view's polling only. The server-side window
  // stays open until the phone pairs, the operator cancels, or it expires.
  stopPolling()
})

watch(showMonitors, () => void load())
watch(
  pairing,
  (open) => {
    if (open) {
      // A new window: remember which phone the status knew before it, so a
      // later change in the reported MAC identifies the phone that bonded.
      phoneMacAtWindowSeen = source.status?.paired_phone_mac ?? null
      reportedPhoneMac = null
      stopPolling()
      pairPoll = setInterval(() => void refreshInputs(), 2000)
      return
    }
    stopPolling()
  },
  { immediate: true },
)
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

    <p v-if="feed && !feed.ma_paired" data-source-ma-hint class="rounded-(--radius-card) border border-info/30 bg-info/10 px-3 py-2 text-sm text-text-secondary">
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
        <SbButton size="sm" variant="outline" data-source-pair :loading="source.busy" @click="pair">
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
