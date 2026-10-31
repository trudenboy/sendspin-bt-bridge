<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bluetooth, Play, Square } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import type { SourceFailure } from '@/stores/hardware'
import type { SourceCaptureKind, SourceFeed } from '@/api/types'

/**
 * The source strip at the top of the Hardware page: the one phone the bridge
 * accepts, how its audio is captured, which speakers relay it, and the pairing
 * window. A bridge that does not report a source gets the sentence that says
 * where the feature is enabled — never a fake set of controls.
 */
const props = withDefaults(
  defineProps<{
    source: SourceFeed | null
    /** Seconds left in the pairing window; 0 while it is closed. */
    pairingSecondsLeft?: number
    /** Every player on the bridge, so relays read as speakers and not as ids. */
    players?: { id: string; name: string }[]
    /** The streaming phone's friendly name, when a paired device names it. */
    phoneName?: string | null
    /** The last command's refusal, with the server's stable code. */
    failure?: SourceFailure | null
    busy?: boolean
  }>(),
  { pairingSecondsLeft: 0, players: () => [], phoneName: null, failure: null, busy: false },
)

const emit = defineEmits<{
  start: []
  stop: []
  pair: []
  stopPairing: []
  relay: [playerId: string, enabled: boolean]
}>()

const { t } = useI18n()

const state = computed(() => props.source?.state ?? 'off')
/** An absent feature and a configured-but-off source share "off"; this tells them apart. */
const presence = computed(() => (props.source ? 'configured' : 'absent'))
const stateLabel = computed(() => {
  const keys = {
    off: 'hardware.source.stateOff',
    waiting: 'hardware.source.stateWaiting',
    streaming: 'hardware.source.stateStreaming',
    error: 'hardware.source.stateError',
  } as const
  return t(keys[state.value])
})
const tone = computed(() => {
  if (state.value === 'streaming') return 'success'
  if (state.value === 'waiting') return 'warning'
  if (state.value === 'error') return 'error'
  return 'neutral'
})

const stateBody = computed(() => {
  const feed = props.source
  if (!feed) return ''
  if (!feed.enabled) return t('hardware.source.configuredOff')
  if (feed.state === 'streaming') return t('hardware.source.streamingBody', { port: feed.port ?? '—' })
  if (feed.state === 'waiting') return t('hardware.source.waitingBody', { port: feed.port ?? '—' })
  return ''
})

const captureKind = computed<SourceCaptureKind>(() => props.source?.capture.kind ?? 'unavailable')
const captureKindLabel = computed(() => {
  const keys = {
    pipewire_node: 'hardware.source.capturePipewire',
    pulse_source: 'hardware.source.capturePulse',
    unavailable: 'hardware.source.captureUnavailable',
  } as const
  return t(keys[captureKind.value])
})

const phoneLabel = computed(() => props.phoneName || '')

const chosen = ref('')
const relays = computed(() => props.source?.relays ?? [])
const chosenRelayed = computed(() => relays.value.includes(chosen.value))

/* A player list that arrives after the first render, or a relay that left, picks a usable default. */
watch(
  [() => props.players, relays],
  () => {
    if (!props.players.length) {
      chosen.value = ''
      return
    }
    if (props.players.some((player) => player.id === chosen.value)) return
    const free = props.players.find((player) => !relays.value.includes(player.id))
    chosen.value = free?.id ?? props.players[0]!.id
  },
  { immediate: true, deep: true },
)

function playerName(id: string | null) {
  if (!id) return ''
  return props.players.find((player) => player.id === id)?.name ?? id
}

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

function toggleRelay() {
  if (!chosen.value || props.busy) return
  emit('relay', chosen.value, !chosenRelayed.value)
}

/** An error the bridge reports in the feed: our words for the codes we know, its own text otherwise. */
const errorText = computed(() => {
  const code = props.source?.error
  if (!code) return ''
  const port = props.source?.port ?? '—'
  if (code === 'port_in_use') return t('hardware.source.errorPortInUse', { port })
  if (code === 'capture_unavailable') return t('hardware.source.errorCapture')
  if (code === 'no_adapter') return t('hardware.source.errorNoAdapter')
  if (code === 'no_agent') return t('hardware.source.errorNoAgent')
  return code
})

const errorNextStep = computed(() => {
  const code = props.source?.error
  if (code === 'port_in_use') return t('hardware.source.errorPortInUseNext')
  if (code === 'capture_unavailable') return t('hardware.source.errorCaptureNext')
  if (code === 'no_adapter' || code === 'no_agent') return t('hardware.source.errorBluetoothNext')
  return t('hardware.source.errorNextStep')
})

const failureNextStep = computed(() =>
  props.failure?.code === 'source_sink_missing'
    ? t('hardware.source.sinkMissingNext')
    : t('hardware.source.failureNextStep'),
)
</script>

<template>
  <section
    data-source-panel
    :data-source-state="state"
    :data-source-presence="presence"
    aria-labelledby="hardware-source-title"
    class="mb-8 rounded-(--radius-card) border border-border bg-surface-card p-4"
  >
    <header class="flex flex-wrap items-start gap-x-3 gap-y-1">
      <div class="min-w-0 flex-1">
        <h2 id="hardware-source-title" class="text-lg font-semibold text-text-primary">
          {{ t('hardware.source.title') }}
        </h2>
        <p v-if="stateBody" class="mt-1 text-sm text-text-secondary">{{ stateBody }}</p>
      </div>
      <SbBadge data-source-state-badge :tone="tone" size="sm" dot>{{ stateLabel }}</SbBadge>
    </header>

    <!-- The feature is off: say where it lives, offer nothing that would not work. -->
    <p v-if="!source" data-source-off class="mt-3 max-w-2xl text-sm text-text-secondary">
      {{ t('hardware.source.offBody') }}
    </p>

    <template v-else>
      <p v-if="source.phone_mac" data-source-phone class="mt-3 flex flex-wrap items-baseline gap-x-2 text-sm">
        <span class="text-text-secondary">{{ t('hardware.source.phone') }}</span>
        <span v-if="phoneLabel" class="font-medium text-text-primary">{{ phoneLabel }}</span>
        <span class="font-mono text-xs text-text-secondary">{{ source.phone_mac }}</span>
      </p>

      <div v-if="source.enabled" data-source-relays class="mt-3">
        <p class="text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.source.relays') }}
        </p>
        <ul v-if="relays.length" class="mt-1.5 flex flex-wrap gap-1.5">
          <li v-for="relay in relays" :key="relay">
            <RouterLink :to="'/'" class="text-sm text-primary-text underline-offset-2 hover:underline">
              {{ playerName(relay) }}
            </RouterLink>
          </li>
        </ul>
        <p v-else data-source-no-relays class="mt-1 text-sm text-text-secondary">
          {{ t('hardware.source.noRelays') }}
        </p>

        <div v-if="players.length" class="mt-2 flex flex-wrap items-center gap-2">
          <select
            v-model="chosen"
            data-source-player
            :aria-label="t('hardware.source.relayPlayer')"
            class="h-8 rounded-(--radius-input) border border-border bg-surface px-2 text-sm text-text-primary"
            :disabled="busy"
          >
            <option v-for="player in players" :key="player.id" :value="player.id">{{ player.name }}</option>
          </select>
          <SbButton
            variant="outline"
            size="sm"
            data-source-relay
            :disabled="busy || !chosen"
            @click="toggleRelay"
          >
            <template #icon-left><Bluetooth class="h-3.5 w-3.5" aria-hidden="true" /></template>
            {{ chosenRelayed ? t('hardware.source.detach') : t('hardware.source.attach') }}
          </SbButton>
        </div>
      </div>

      <!-- The window is open: the countdown is the server's remaining time, not a copy of the default. -->
      <div
        v-if="source.enabled && pairingSecondsLeft > 0"
        data-source-pairing
        class="mt-3 rounded-(--radius-card) border border-info/40 bg-info/10 px-3 py-2"
      >
        <div class="flex flex-wrap items-center gap-2">
          <p class="text-sm text-text-primary">{{ t('hardware.source.pairing', { time: clock(pairingSecondsLeft) }) }}</p>
          <SbButton variant="outline" size="sm" data-source-pairing-stop :disabled="busy" @click="emit('stopPairing')">
            {{ t('hardware.source.stopPairing') }}
          </SbButton>
        </div>
        <p class="mt-1 text-xs text-text-secondary">{{ t('hardware.source.pairingHint') }}</p>
      </div>

      <div class="mt-3 flex flex-wrap items-center gap-2">
        <SbButton v-if="!source.enabled" size="sm" data-source-start :loading="busy" @click="emit('start')">
          <template #icon-left><Play class="h-3.5 w-3.5" aria-hidden="true" /></template>
          {{ t('hardware.source.start') }}
        </SbButton>
        <template v-else>
          <SbButton
            v-if="pairingSecondsLeft <= 0"
            variant="outline"
            size="sm"
            data-source-pair
            :disabled="busy"
            @click="emit('pair')"
          >
            <template #icon-left><Bluetooth class="h-3.5 w-3.5" aria-hidden="true" /></template>
            {{ t('hardware.source.pair') }}
          </SbButton>
          <SbButton variant="ghost" size="sm" data-source-stop :disabled="busy" @click="emit('stop')">
            <template #icon-left><Square class="h-3.5 w-3.5" aria-hidden="true" /></template>
            {{ t('hardware.source.stop') }}
          </SbButton>
        </template>
      </div>

      <!-- The source itself failed: the server's reason and a next step, never a bare red badge. -->
      <div
        v-if="source.state === 'error'"
        data-source-error
        role="alert"
        class="mt-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-3 py-2"
      >
        <p class="text-sm font-medium text-text-primary">{{ t('hardware.source.errorTitle') }}</p>
        <p class="mt-1 text-sm text-text-secondary">{{ errorText }}</p>
        <p class="mt-1 text-sm text-text-secondary">{{ errorNextStep }}</p>
        <p v-if="source.error" class="mt-1 font-mono text-xs text-text-secondary">{{ source.error }}</p>
      </div>

      <!-- The last command was refused: keep the code and, for the sink refusal, the speaker. -->
      <div
        v-if="failure"
        data-source-failure
        role="alert"
        class="mt-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-3 py-2"
      >
        <p class="text-sm font-medium text-text-primary">{{ t('hardware.source.failureTitle') }}</p>
        <p class="mt-1 font-mono text-xs text-text-secondary">
          {{ failure.code }}<template v-if="failure.detail">: {{ failure.detail }}</template>
        </p>
        <p v-if="failure.code === 'source_sink_missing'" class="mt-1 text-sm text-text-secondary">
          {{ t('hardware.source.sinkMissing', { player: playerName(failure.player) }) }}
        </p>
        <p class="mt-1 text-sm text-text-secondary">{{ failureNextStep }}</p>
      </div>

      <dl
        v-if="technicalDetails"
        data-source-technical
        class="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 border-t border-border pt-3 text-sm"
      >
        <dt class="text-text-secondary">{{ t('hardware.source.capture') }}</dt>
        <dd class="text-text-primary">{{ captureKindLabel }}</dd>
        <dt class="text-text-secondary">{{ t('hardware.source.node') }}</dt>
        <dd class="break-all font-mono text-xs text-text-primary">
          {{ source.capture.node || t('hardware.source.noNode') }}
        </dd>
      </dl>
    </template>
  </section>
</template>
