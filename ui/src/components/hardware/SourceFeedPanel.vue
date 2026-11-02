<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bluetooth, Play, Radio } from 'lucide-vue-next'
import { SbBadge, SbButton } from '@/kit'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import type { SourceFailure } from '@/stores/hardware'
import type { SourceCaptureKind, SourceFeed } from '@/api/types'
import { describeSourceCode } from '@/utils/sourceErrors'

/**
 * The source strip at the top of the Hardware page. Two pairings, two blocks:
 * the bridge pairs with Music Assistant with an eight-digit code, and the phone
 * pairs with the bridge over Bluetooth. Music Assistant owns playback and
 * routing, so the strip never offers to play the phone on a speaker itself.
 */
const props = withDefaults(
  defineProps<{
    source: SourceFeed | null
    /** Seconds left in the phone pairing window; 0 while it is closed. */
    pairingSecondsLeft?: number
    /** The streaming phone's friendly name, when a paired device names it. */
    phoneName?: string | null
    /** The last command's refusal, with the server's stable code. */
    failure?: SourceFailure | null
    busy?: boolean
  }>(),
  { pairingSecondsLeft: 0, phoneName: null, failure: null, busy: false },
)

const emit = defineEmits<{
  start: []
  stop: []
  pair: []
  stopPairing: []
}>()

const { t } = useI18n()

const state = computed(() => props.source?.state ?? 'off')
const presence = computed(() => (props.source ? 'configured' : 'absent'))
const stateLabel = computed(() => {
  const keys = {
    off: 'hardware.source.stateOff',
    pairing: 'hardware.source.statePairing',
    waiting: 'hardware.source.stateWaiting',
    streaming: 'hardware.source.stateStreaming',
    error: 'hardware.source.stateError',
  } as const
  return t(keys[state.value])
})
const tone = computed(() => {
  if (state.value === 'streaming') return 'success'
  if (state.value === 'pairing' || state.value === 'waiting') return 'warning'
  if (state.value === 'error') return 'error'
  return 'neutral'
})

const ma = computed(() => props.source?.ma ?? null)
const settingsPairingHref = '/config#settings-musicAssistant'
const maStatus = computed(() => {
  if (!props.source?.enabled) return t('hardware.source.maOff')
  if (ma.value?.error) return t('hardware.source.maError')
  if (ma.value?.paired && ma.value?.connected) return t('hardware.source.maConnected')
  if (ma.value?.paired) return t('hardware.source.maReconnecting')
  return t('hardware.source.maNotPairedWhere')
})
/** The Music Assistant web UI lives on the same host; the Sendspin port is not it. */
const maLink = computed(() => {
  const server = ma.value?.server
  if (!server) return null
  try {
    const host = new URL(server.replace(/^ws/, 'http')).hostname
    return host ? `http://${host}:8095` : null
  } catch {
    return null
  }
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
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

/** A failure the bridge reports in the feed: the same words Settings uses. */
const errorInfo = computed(() => describeSourceCode(props.source?.error || ma.value?.error, t))

const failureNextStep = computed(() => t('hardware.source.failureNextStep'))
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
        <p class="mt-1 text-sm text-text-secondary">{{ t('hardware.source.subtitle') }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <SbBadge data-source-state-badge :tone="tone" size="sm" dot>{{ stateLabel }}</SbBadge>
        <SbButton
          v-if="source?.enabled"
          variant="ghost"
          size="sm"
          data-source-stop
          :disabled="busy"
          @click="emit('stop')"
        >
          {{ t('hardware.source.stop') }}
        </SbButton>
      </div>
    </header>

    <!-- The feature is off: explain it and let the operator enable it right here. -->
    <template v-if="!source || !source.enabled">
      <p data-source-off class="mt-3 max-w-2xl text-sm text-text-secondary">
        {{ t('hardware.source.offBody') }}
      </p>
      <div class="mt-3">
        <SbButton size="sm" data-source-start :loading="busy" @click="emit('start')">
          <template #icon-left><Play class="h-3.5 w-3.5" aria-hidden="true" /></template>
          {{ t('hardware.source.start') }}
        </SbButton>
      </div>
    </template>

    <template v-else>
      <div class="mt-4 grid gap-4 lg:grid-cols-2">
        <!-- Music Assistant: connection state only; pairing is a Settings concern. -->
        <div data-source-ma class="rounded-(--radius-card) border border-border bg-surface p-3">
          <div class="flex flex-wrap items-center gap-2">
            <Radio class="h-4 w-4 text-text-secondary" aria-hidden="true" />
            <h3 class="text-sm font-semibold text-text-primary">{{ t('hardware.source.maTitle') }}</h3>
            <SbBadge :tone="ma?.connected ? 'success' : ma?.paired ? 'warning' : 'neutral'" size="sm">
              {{ ma?.connected ? t('hardware.source.maConnectedShort') : ma?.paired ? t('hardware.source.maPairedShort') : t('hardware.source.maUnpairedShort') }}
            </SbBadge>
          </div>
          <p class="mt-1 text-sm text-text-secondary">{{ maStatus }}</p>
          <p v-if="technicalDetails && ma?.server" class="mt-1 break-all font-mono text-xs text-text-secondary" data-source-ma-server>
            {{ ma.server }}
          </p>

          <p class="mt-3 text-sm text-text-secondary">
            {{ t('hardware.source.maSettingsHint') }}
            <RouterLink
              :to="settingsPairingHref"
              data-source-ma-settings
              class="text-primary-text underline underline-offset-2"
            >
              {{ t('hardware.source.maSettingsAction') }}
            </RouterLink>
          </p>

          <p class="mt-2 text-xs text-text-secondary">
            {{ t('hardware.source.maRouting') }}
            <a v-if="maLink" :href="maLink" target="_blank" rel="noopener noreferrer" class="text-primary-text underline underline-offset-2">
              {{ t('hardware.source.maOpen') }}
            </a>
          </p>
        </div>

        <!-- Phone: the Bluetooth side, which only pairs the phone with this bridge. -->
        <div data-source-phone-block class="rounded-(--radius-card) border border-border bg-surface p-3">
          <div class="flex flex-wrap items-center gap-2">
            <Bluetooth class="h-4 w-4 text-text-secondary" aria-hidden="true" />
            <h3 class="text-sm font-semibold text-text-primary">{{ t('hardware.source.phoneTitle') }}</h3>
            <SbBadge :tone="source.state === 'streaming' ? 'success' : 'neutral'" size="sm">
              {{ source.state === 'streaming' ? t('hardware.source.stateStreaming') : t('hardware.source.phoneIdle') }}
            </SbBadge>
          </div>

          <p v-if="source.phone_mac" data-source-phone class="mt-2 flex flex-wrap items-baseline gap-x-2 text-sm">
            <span class="text-text-secondary">{{ t('hardware.source.phone') }}</span>
            <span v-if="phoneLabel" class="font-medium text-text-primary">{{ phoneLabel }}</span>
            <span class="font-mono text-xs text-text-secondary">{{ source.phone_mac }}</span>
          </p>
          <p v-else-if="source.paired_phone_mac" data-source-phone-paired class="mt-2 flex flex-wrap items-baseline gap-x-2 text-sm">
            <span class="text-text-secondary">{{ t('hardware.source.phonePaired') }}</span>
            <span class="font-mono text-xs text-text-secondary">{{ source.paired_phone_mac }}</span>
          </p>
          <p v-else class="mt-2 text-sm text-text-secondary">{{ t('hardware.source.phoneNoPhone') }}</p>

          <div v-if="pairingSecondsLeft > 0" data-source-pairing class="mt-3 rounded-(--radius-card) border border-info/40 bg-info/10 px-3 py-2">
            <div class="flex flex-wrap items-center gap-2">
              <p class="text-sm text-text-primary">{{ t('hardware.source.pairing', { time: clock(pairingSecondsLeft) }) }}</p>
              <SbButton variant="outline" size="sm" data-source-pairing-stop :disabled="busy" @click="emit('stopPairing')">
                {{ t('hardware.source.stopPairing') }}
              </SbButton>
            </div>
            <p class="mt-1 text-xs text-text-secondary">{{ t('hardware.source.pairingHint') }}</p>
          </div>

          <div v-else-if="source.enabled" class="mt-3 flex flex-wrap items-center gap-2">
            <SbButton variant="outline" size="sm" data-source-pair :disabled="busy" @click="emit('pair')">
              <template #icon-left><Bluetooth class="h-3.5 w-3.5" aria-hidden="true" /></template>
              {{ t('hardware.source.pair') }}
            </SbButton>
          </div>
          <p v-if="source.paired_phone_mac && !source.phone_mac" class="mt-1 text-xs text-text-secondary">
            {{ t('hardware.source.phonePairedHint') }}
          </p>
          <p class="mt-2 text-xs text-text-secondary">{{ t('hardware.source.phoneHint') }}</p>

          <div
            v-if="source.paired_phone_mac && !ma?.paired"
            data-source-phone-needs-ma
            class="mt-3 rounded-(--radius-card) border border-info/40 bg-info/10 px-3 py-2"
          >
            <p class="text-sm text-text-primary">{{ t('hardware.source.phonePairedNeedMa') }}</p>
            <RouterLink
              :to="settingsPairingHref"
              class="mt-1 inline-block text-sm text-primary-text underline underline-offset-2"
            >
              {{ t('hardware.source.phonePairedNeedMaAction') }}
            </RouterLink>
          </div>
        </div>
      </div>

      <!-- The source itself failed: the server's reason and a next step, never a bare red badge. -->
      <div
        v-if="errorInfo"
        data-source-error
        role="alert"
        class="mt-3 rounded-(--radius-card) border border-error/50 bg-error/10 px-3 py-2"
      >
        <p class="text-sm font-medium text-text-primary">{{ t('hardware.source.errorTitle') }}</p>
        <p class="mt-1 text-sm text-text-secondary">{{ errorInfo.text }}</p>
        <p class="mt-1 text-sm text-text-secondary">{{ errorInfo.next }}</p>
        <p v-if="source.error || ma?.error" class="mt-1 font-mono text-xs text-text-secondary">
          {{ source.error || ma?.error }}
        </p>
      </div>

      <!-- The last command was refused: keep the stable code. -->
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
