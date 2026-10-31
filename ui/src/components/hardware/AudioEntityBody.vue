<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbSpinner } from '@/kit'
import AudioCardPanel from './AudioCardPanel.vue'
import PortList from './PortList.vue'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import { ApiError } from '@/api/client'
import { getHardwareAudioCard, getHardwareAudioSink, getHardwareAudioSource } from '@/api/hardware'
import { activePortAvailability, availabilityTone, endpointStateTone } from '@/utils/audioEndpoint'
import type { AudioCardDetail, AudioEndpointDetail, AudioEndpointOwner } from '@/api/types'

/**
 * The drawer body for one audio entity. The aggregate carries counts; this is
 * the one place that reads the detail route (profiles, ports, identifiers), so
 * opening a drawer never costs the page a fan-out.
 */
const props = defineProps<{
  kind: 'audio-card' | 'audio-sink' | 'audio-source'
  name: string
}>()

const { t } = useI18n()

const card = ref<AudioCardDetail | null>(null)
const endpoint = ref<AudioEndpointDetail | null>(null)
const loading = ref(false)
const error = ref('')
let request = 0

const isCard = computed(() => props.kind === 'audio-card')

const stateLabel = computed(() => {
  const keys: Record<string, string> = {
    running: 'hardware.audio.stateRunning',
    idle: 'hardware.audio.stateIdle',
    suspended: 'hardware.audio.stateSuspended',
    unavailable: 'hardware.audio.stateUnavailable',
  }
  return t(keys[endpoint.value?.state ?? ''] ?? 'hardware.audio.stateUnknown')
})

const availability = computed(() => activePortAvailability(endpoint.value?.active_port_available))
const availabilityLabel = computed(() => {
  const keys: Record<string, string> = {
    available: 'hardware.audio.portAvailable',
    unavailable: 'hardware.audio.portUnavailable',
    unknown: 'hardware.audio.portUnknown',
  }
  return availability.value ? t(keys[availability.value]) : ''
})

function ownerLabel(owner: AudioEndpointOwner | null | undefined) {
  if (!owner) return t('hardware.audio.ownerUnknown')
  if (owner.card) return t('hardware.audio.ownerCard', { card: owner.card })
  if (owner.bluetooth) return t('hardware.audio.ownerBluetooth', { mac: owner.bluetooth })
  return t('hardware.audio.ownerInternal')
}

const identifiers = computed(() => {
  const raw = endpoint.value?.identifiers ?? {}
  const known: [string, string][] = [
    ['serial', 'hardware.audio.identifierSerial'],
    ['bus_path', 'hardware.audio.identifierBusPath'],
    ['vendor_id', 'hardware.audio.identifierVendorId'],
    ['product_id', 'hardware.audio.identifierProductId'],
    ['alsa_long_card_name', 'hardware.audio.identifierAlsaLongCardName'],
  ]
  return known.filter(([key]) => raw[key]).map(([key, label]) => ({ key, label, value: raw[key] }))
})

async function load() {
  const token = ++request
  loading.value = true
  error.value = ''
  card.value = null
  endpoint.value = null
  try {
    const detail =
      props.kind === 'audio-card'
        ? await getHardwareAudioCard(props.name)
        : props.kind === 'audio-sink'
          ? await getHardwareAudioSink(props.name)
          : await getHardwareAudioSource(props.name)
    if (token !== request) return
    if (props.kind === 'audio-card') card.value = detail as AudioCardDetail
    else endpoint.value = detail as AudioEndpointDetail
  } catch (e) {
    if (token !== request) return
    error.value = e instanceof ApiError ? `${e.code}: ${e.message}` : e instanceof Error ? e.message : String(e)
  } finally {
    if (token === request) loading.value = false
  }
}

watch([() => props.kind, () => props.name], load, { immediate: true })
</script>

<template>
  <div data-audio-detail :data-detail-kind="kind" class="space-y-6 py-2">
    <div v-if="loading" class="flex items-center gap-2 text-sm text-text-secondary" aria-busy="true">
      <SbSpinner :label="t('common.loading')" />
      {{ t('common.loading') }}
    </div>

    <div
      v-else-if="error"
      data-audio-detail-error
      role="alert"
      class="rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
    >
      {{ error }}
    </div>

    <template v-else-if="isCard && card">
      <AudioCardPanel :card="card" :interactive="false" />
      <section v-if="technicalDetails" data-drawer-technical class="border-t border-border pt-4">
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.drawer.technical') }}
        </h3>
        <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt class="text-text-secondary">{{ t('hardware.drawer.id') }}</dt>
          <dd class="font-mono text-xs break-all text-text-primary">{{ card.name }}</dd>
          <dt class="text-text-secondary">{{ t('hardware.audio.driver') }}</dt>
          <dd class="font-mono text-xs text-text-primary">{{ card.driver || '—' }}</dd>
        </dl>
      </section>
    </template>

    <template v-else-if="endpoint">
      <section data-audio-overview class="space-y-3">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.drawer.overview') }}
        </h3>
        <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt class="text-text-secondary">{{ t('hardware.audio.direction') }}</dt>
          <dd class="text-text-primary">
            {{ endpoint.kind === 'sink' ? t('hardware.audio.output') : t('hardware.audio.input') }}
          </dd>
          <dt class="text-text-secondary">{{ t('hardware.audio.state') }}</dt>
          <dd>
            <SbBadge :tone="endpointStateTone(endpoint.state)" size="sm" dot>{{ stateLabel }}</SbBadge>
          </dd>
          <template v-if="endpoint.active_port">
            <dt class="text-text-secondary">{{ t('hardware.audio.activePort') }}</dt>
            <dd class="flex flex-wrap items-center gap-1.5">
              <span class="font-mono text-xs text-text-primary">{{ endpoint.active_port }}</span>
              <SbBadge
                v-if="availability"
                :data-port-state="availability"
                :tone="availabilityTone(availability)"
                size="sm"
                dot
              >
                {{ availabilityLabel }}
              </SbBadge>
            </dd>
          </template>
          <template v-if="endpoint.kind === 'sink'">
            <dt class="text-text-secondary">{{ t('hardware.audio.volume') }}</dt>
            <dd class="tabular-nums text-text-primary">{{ endpoint.volume ?? 0 }}%</dd>
            <dt class="text-text-secondary">{{ t('hardware.audio.muted') }}</dt>
            <dd class="text-text-primary">{{ endpoint.muted ? t('hardware.yes') : t('hardware.no') }}</dd>
            <template v-if="endpoint.in_use_by">
              <dt class="text-text-secondary">{{ t('hardware.audio.inUse') }}</dt>
              <dd>
                <RouterLink to="/" data-endpoint-player class="text-primary-text underline-offset-2 hover:underline">
                  {{ endpoint.in_use_by }}
                </RouterLink>
              </dd>
            </template>
          </template>
          <template v-if="endpoint.monitor_of">
            <dt class="text-text-secondary">{{ t('hardware.audio.monitorOf') }}</dt>
            <dd class="font-mono text-xs text-text-primary">{{ endpoint.monitor_of }}</dd>
          </template>
          <dt class="text-text-secondary">{{ t('hardware.audio.owner') }}</dt>
          <dd class="text-text-primary">{{ ownerLabel(endpoint.owner) }}</dd>
        </dl>
      </section>

      <section v-if="endpoint.ports.length" data-endpoint-ports class="border-t border-border pt-4">
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.audio.ports') }}
        </h3>
        <PortList :ports="endpoint.ports" />
      </section>

      <section v-if="technicalDetails" data-drawer-technical class="border-t border-border pt-4">
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.drawer.technical') }}
        </h3>
        <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt class="text-text-secondary">{{ t('hardware.drawer.id') }}</dt>
          <dd class="font-mono text-xs break-all text-text-primary">{{ endpoint.name }}</dd>
          <dt class="text-text-secondary">{{ t('hardware.audio.sampleSpec') }}</dt>
          <dd class="font-mono text-xs text-text-primary">{{ endpoint.sample_spec || '—' }}</dd>
          <template v-for="entry in identifiers" :key="entry.key">
            <dt class="text-text-secondary">{{ t(entry.label) }}</dt>
            <dd class="font-mono text-xs break-all text-text-primary">{{ entry.value }}</dd>
          </template>
        </dl>
      </section>
    </template>
  </div>
</template>
