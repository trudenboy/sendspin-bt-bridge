<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbButton } from '@/kit'
import type { BtDeviceSummary } from '@/api/types'

const props = withDefaults(
  defineProps<{
    device: BtDeviceSummary
    adding?: boolean
    forgetting?: boolean
    connecting?: boolean
    disconnecting?: boolean
    /** A refused forget: the server's player id and its own remediation sentence. */
    refusal?: { playerId: string | null; remediation: string | null; detail: string | null } | null
  }>(),
  { adding: false, forgetting: false, connecting: false, disconnecting: false, refusal: null },
)

const emit = defineEmits<{
  open: [mac: string]
  add: [device: BtDeviceSummary]
  forget: [device: BtDeviceSummary]
  connect: [device: BtDeviceSummary]
  disconnect: [device: BtDeviceSummary]
}>()

const { t } = useI18n()
const displayName = computed(() => props.device.alias || props.device.name || props.device.mac)
/** Connect is offered only for a bond that is down; a visitor without a bond cannot connect. */
const canConnect = computed(
  () => !props.device.connected && (props.device.paired === true || props.device.bonded === true),
)
const playerHref = computed(() =>
  props.refusal?.playerId ? `/?player=${encodeURIComponent(props.refusal.playerId)}` : '/',
)
const rssi = computed(() => {
  const value = props.device.rssi?.delta_db
  if (value == null) return null
  return value > 0 ? `+${value}` : String(value)
})
</script>

<template>
  <article data-device-card class="relative flex flex-col gap-3 rounded-(--radius-card) border border-border bg-surface-card p-4 transition-colors hover:border-border-strong">
    <header class="flex items-start gap-2">
      <div class="min-w-0 flex-1">
        <h3 class="text-base font-medium leading-snug text-text-primary">
          <button
            type="button"
            data-device-details
            class="block max-w-full text-left after:absolute after:inset-0 after:rounded-(--radius-card) after:content-[''] focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-(--sb-focus-ring)"
            :aria-label="t('hardware.openDetails', { name: displayName })"
            @click="emit('open', device.mac)"
          >
            <span class="line-clamp-2 break-words">{{ displayName }}</span>
          </button>
        </h3>
        <p class="mt-0.5 font-mono text-xs text-text-secondary">{{ device.mac }}</p>
      </div>
      <SbBadge v-if="device.connected" tone="success" size="sm">{{ t('hardware.connected') }}</SbBadge>
    </header>

    <div class="flex flex-wrap items-center gap-1.5">
      <SbBadge v-if="device.paired === true" size="sm">{{ t('hardware.paired') }}</SbBadge>
      <SbBadge v-if="device.trusted === true" size="sm">{{ t('hardware.trusted') }}</SbBadge>
      <SbBadge v-if="device.battery_pct !== null" size="sm">
        {{ t('hardware.battery', { pct: device.battery_pct }) }}
      </SbBadge>
      <SbBadge v-if="rssi" size="sm">{{ t('hardware.signal', { db: rssi }) }}</SbBadge>
    </div>

    <div v-if="device.roles.length" class="flex flex-wrap items-center gap-1.5">
      <SbBadge
        v-for="role in device.roles"
        :key="`${role.role}-${role.evidence}`"
        size="sm"
        :tone="role.role === 'input' ? 'info' : 'success'"
        :data-role="role.role"
        :title="t('hardware.roleEvidence', { evidence: role.evidence })"
      >
        {{ role.role === 'input' ? t('hardware.roleInput') : t('hardware.roleOutput') }}
        <span class="font-normal">· {{ role.evidence }}</span>
      </SbBadge>
    </div>

    <p v-if="device.player_id" class="relative z-10 text-sm">
      <span class="text-text-secondary">{{ t('hardware.player') }}:</span>
      <RouterLink data-player-link to="/" class="ml-1 text-primary-text underline-offset-2 hover:underline">
        {{ device.player_id }}
      </RouterLink>
    </p>

    <div class="relative z-10 mt-auto flex flex-wrap items-center gap-2">
      <SbButton
        v-if="canConnect"
        size="sm"
        variant="outline"
        data-connect-device
        :loading="connecting"
        @click="emit('connect', device)"
      >
        {{ t('hardware.connect') }}
      </SbButton>
      <SbButton
        v-if="device.connected"
        size="sm"
        variant="outline"
        data-disconnect-device
        :loading="disconnecting"
        @click="emit('disconnect', device)"
      >
        {{ t('hardware.disconnect') }}
      </SbButton>
      <SbButton
        v-if="!device.player_id"
        size="sm"
        variant="outline"
        data-add-speaker
        :loading="adding"
        @click="emit('add', device)"
      >
        {{ t('hardware.addAsSpeaker') }}
      </SbButton>
      <SbButton size="sm" variant="outline" data-forget-bond :loading="forgetting" @click="emit('forget', device)">
        {{ t('device.actions.forget') }}
      </SbButton>
    </div>

    <!-- The server refused: its own sentence names the player, so the page repeats it verbatim. -->
    <div
      v-if="refusal"
      data-bond-refusal
      role="alert"
      class="relative z-10 rounded-(--radius-card) border border-warning/40 bg-warning/10 p-3 text-sm"
    >
      <p class="font-medium text-text-primary">{{ t('hardware.bondInUseTitle') }}</p>
      <p class="mt-1 text-text-secondary">{{ refusal.remediation || refusal.detail || t('hardware.bondInUse') }}</p>
      <RouterLink
        v-if="refusal.playerId"
        :to="playerHref"
        data-bond-player-link
        class="mt-1 inline-block font-medium text-primary-text underline-offset-2 hover:underline"
      >
        {{ t('hardware.openPlayer', { player: refusal.playerId }) }}
      </RouterLink>
    </div>
  </article>
</template>
