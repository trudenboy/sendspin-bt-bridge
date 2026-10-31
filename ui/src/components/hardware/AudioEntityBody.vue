<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbSpinner } from '@/kit'
import AudioCardPanel from './AudioCardPanel.vue'
import PortList from './PortList.vue'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import { confirmAction } from '@/composables/useActionPolicy'
import { useHardwareStore } from '@/stores/hardware'
import { useNotificationStore } from '@/stores/notifications'
import { ApiError } from '@/api/client'
import { getHardwareAudioCard, getHardwareAudioSink, getHardwareAudioSource } from '@/api/hardware'
import {
  activePortAvailability,
  availabilityTone,
  endpointStateTone,
  portAvailability,
  type PortAvailability,
} from '@/utils/audioEndpoint'
import type {
  AudioCardDetail,
  AudioCardPort,
  AudioCardProfile,
  AudioEndpointDetail,
  AudioEndpointOwner,
} from '@/api/types'

/**
 * The drawer body for one audio entity. The aggregate carries counts; this is
 * the one place that reads the detail route (profiles, ports, identifiers), so
 * opening a drawer never costs the page a fan-out.
 *
 * The two writes live here because the detail they need is here: a profile is
 * only known from the card read, and the sink's card ports only from the
 * endpoint read. Each write answers with the updated detail, so the drawer
 * shows the new state without asking again.
 */
const props = defineProps<{
  kind: 'audio-card' | 'audio-sink' | 'audio-source'
  name: string
}>()

const { t } = useI18n()
const store = useHardwareStore()
const notifications = useNotificationStore()

const card = ref<AudioCardDetail | null>(null)
const endpoint = ref<AudioEndpointDetail | null>(null)
const loading = ref(false)
const error = ref('')
/** A refused write; the server's stable code stays in the text. */
const actionError = ref('')
const actionBusy = ref(false)
const profileSelectId = useId()
const portSelectId = useId()
let request = 0

const isCard = computed(() => props.kind === 'audio-card')

/**
 * The bridge's Bluetooth link chooses this card's profile. The flag is the
 * card's own fact: a PipeWire ``bluez_card`` has no endpoints to read it from.
 */
const managedByBridge = computed(() => card.value?.managed_by === 'bridge')

/**
 * Ports belong to an output; an input has no port choice to offer. A
 * Bluetooth-owned sink still offers its ports: only the profile is the
 * bridge's (plan E6), and the backend resolves the sink's card through the
 * read model's owner classification, so the switch is supported there.
 */
const portSwitchable = computed(() => endpoint.value?.kind === 'sink' && endpoint.value.ports.length > 0)

/** The players a profile switch may silence, named from the detail's endpoints. */
const playersInUse = computed(() => {
  const ids = (card.value?.endpoints ?? [])
    .map((entry) => entry.in_use_by)
    .filter((id): id is string => Boolean(id))
  return [...new Set(ids)]
})

/** A detail read before the last refresh: the change it follows is not in it. */
const detailStale = computed(() =>
  isCard.value ? card.value?.stale === true : endpoint.value?.stale === true,
)

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

function failure(e: unknown) {
  return e instanceof ApiError ? `${e.code}: ${e.message}` : e instanceof Error ? e.message : String(e)
}

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

const portLabels: Record<PortAvailability, string> = {
  available: 'hardware.audio.portAvailable',
  unavailable: 'hardware.audio.portUnavailable',
  unknown: 'hardware.audio.portUnknown',
}

/** An option says what the profile is, whether it is in use, and why it cannot be picked. */
function profileOption(profile: AudioCardProfile) {
  const description = profile.description && profile.description !== profile.name ? profile.description : ''
  const parts = [description ? `${description} (${profile.name})` : profile.name]
  if (profile.name === card.value?.active_profile) parts.push(t('hardware.audio.profileActive'))
  if (portAvailability(profile.available) === 'unavailable') parts.push(t('hardware.audio.profileUnavailable'))
  return parts.join(' · ')
}

/** A port option always carries its availability: an unknown port is not an unavailable one. */
function portOption(port: AudioCardPort) {
  const description = port.description && port.description !== port.name ? port.description : ''
  const parts = [description ? `${description} (${port.name})` : port.name]
  if (port.active) parts.push(t('hardware.audio.portActive'))
  parts.push(t(portLabels[portAvailability(port.available)]))
  return parts.join(' · ')
}

async function onProfileChange(event: Event) {
  const select = event.target as HTMLSelectElement
  const profile = select.value
  const current = card.value
  if (!current || !profile || profile === current.active_profile) return
  // The dialog decides. Until it does, the control keeps showing what runs now.
  select.value = current.active_profile ?? ''
  actionError.value = ''
  const players = playersInUse.value
  const consequence = players.length
    ? t('hardware.audio.profileConsequenceInUse', { card: current.name, players: players.join(', ') })
    : t('hardware.audio.profileConsequence', { card: current.name })
  const ok = await confirmAction('audio.cardProfile', {
    title: t('hardware.audio.profileConfirmTitle', { card: current.name, profile }),
    consequences: [consequence],
    confirmLabel: t('hardware.audio.profileConfirm'),
  })
  if (!ok) return
  actionBusy.value = true
  try {
    const detail = await store.chooseCardProfile(current, profile)
    if (detail) card.value = detail
    notifications.success(t('hardware.audio.profileSwitched', { card: current.name, profile }))
  } catch (e) {
    actionError.value = failure(e)
  } finally {
    actionBusy.value = false
  }
}

async function onPortChange(event: Event) {
  const select = event.target as HTMLSelectElement
  const port = select.value
  const current = endpoint.value
  if (!current || !port || port === current.active_port) return
  select.value = current.active_port ?? ''
  actionError.value = ''
  const name = current.description || current.name
  const consequence = current.in_use_by
    ? t('hardware.audio.portConsequenceInUse', { name, player: current.in_use_by })
    : t('hardware.audio.portConsequence', { name })
  const picked = current.ports.find((entry) => entry.name === port)
  // The confirm and the toast name the port the same way: its description when
  // the server has one, its id only as a fallback.
  const portLabel = picked?.description || port
  const ok = await confirmAction('audio.sinkPort', {
    title: t('hardware.audio.portConfirmTitle', { name, port: portLabel }),
    consequences: [consequence],
    confirmLabel: t('hardware.audio.portConfirm'),
  })
  if (!ok) return
  actionBusy.value = true
  try {
    const detail = await store.chooseSinkPort(current, port)
    if (detail) endpoint.value = detail
    notifications.success(t('hardware.audio.portSwitched', { name, port: portLabel }))
  } catch (e) {
    actionError.value = failure(e)
  } finally {
    actionBusy.value = false
  }
}

async function load() {
  const token = ++request
  loading.value = true
  error.value = ''
  actionError.value = ''
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
    <!-- The server said the read behind this detail is older than the request. -->
    <p
      v-if="!loading && !error && detailStale"
      data-stale-detail
      class="rounded-(--radius-card) border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      {{ t('hardware.audio.detailStale') }}
    </p>

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
      <!-- A profile is a card-wide decision: picking one can silence every output on it. -->
      <section v-if="card.profiles.length && !managedByBridge" data-profile-switch class="space-y-2">
        <h3 class="text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.audio.profileSwitch') }}
        </h3>
        <label :for="profileSelectId" class="block text-sm text-text-secondary">
          {{ t('hardware.audio.profileSelectLabel') }}
        </label>
        <select
          :id="profileSelectId"
          data-profile-select
          class="block w-full max-w-md rounded-(--radius-input) border border-border-strong bg-surface-card px-2 py-1.5 text-sm text-text-primary disabled:opacity-60"
          :value="card.active_profile ?? ''"
          :disabled="actionBusy"
          @change="onProfileChange"
        >
          <option
            v-for="profile in card.profiles"
            :key="profile.name"
            :value="profile.name"
            :disabled="profile.available === false"
          >
            {{ profileOption(profile) }}
          </option>
        </select>
        <p v-if="actionError" data-action-error role="alert" class="text-sm text-error">
          {{ actionError }}
        </p>
      </section>
      <p v-else-if="managedByBridge" data-profile-managed class="text-sm text-text-secondary">
        {{ t('hardware.audio.profileManagedByConnection') }}
      </p>

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

      <!-- An output can choose its card's port; the choice is audible, so it asks. -->
      <section v-if="portSwitchable" data-port-switch class="border-t border-border pt-4">
        <h3 class="mb-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.audio.portSwitch') }}
        </h3>
        <label :for="portSelectId" class="mb-1 block text-sm text-text-secondary">
          {{ t('hardware.audio.portSelectLabel') }}
        </label>
        <select
          :id="portSelectId"
          data-port-select
          class="block w-full max-w-md rounded-(--radius-input) border border-border-strong bg-surface-card px-2 py-1.5 text-sm text-text-primary disabled:opacity-60"
          :value="endpoint.active_port ?? ''"
          :disabled="actionBusy"
          @change="onPortChange"
        >
          <option
            v-for="port in endpoint.ports"
            :key="port.name"
            :value="port.name"
            :disabled="port.available === false"
          >
            {{ portOption(port) }}
          </option>
        </select>
        <p v-if="actionError" data-action-error role="alert" class="mt-1 text-sm text-error">
          {{ actionError }}
        </p>
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
