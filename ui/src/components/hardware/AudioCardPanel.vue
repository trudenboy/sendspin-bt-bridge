<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbCard } from '@/kit'
import PortList from './PortList.vue'
import { availabilityTone, portAvailability } from '@/utils/audioEndpoint'
import type { AudioCardDetail, AudioCardProfile, AudioCardSummary, AudioEndpointRef } from '@/api/types'

/**
 * One audio card. The aggregate gives the summary; the drawer passes the
 * detail read, which is where the profile and port lists live. Profiles are
 * read-only in v1 — switching one is not a safe action yet.
 */
const props = withDefaults(
  defineProps<{
    card: AudioCardSummary | AudioCardDetail
    /** False in the drawer, where the card is already the selected entity. */
    interactive?: boolean
  }>(),
  { interactive: true },
)

const emit = defineEmits<{
  open: [card: AudioCardSummary | AudioCardDetail]
  openEndpoint: [endpoint: AudioEndpointRef]
}>()

const { t } = useI18n()

const detail = computed<AudioCardDetail | null>(() =>
  'ports' in props.card ? (props.card as AudioCardDetail) : null,
)

function profileAvailable(profile: AudioCardProfile) {
  return portAvailability(profile.available)
}

function profileLabel(profile: AudioCardProfile) {
  return t(
    profileAvailable(profile) === 'available'
      ? 'hardware.audio.profileAvailable'
      : profileAvailable(profile) === 'unavailable'
        ? 'hardware.audio.profileUnavailable'
        : 'hardware.audio.profileUnknown',
  )
}
</script>

<template>
  <SbCard data-audio-card class="h-full">
    <div class="flex flex-col gap-3">
      <header class="flex items-start gap-2">
        <div class="min-w-0 flex-1">
          <h3 class="text-base font-medium leading-snug text-text-primary">
            <button
              v-if="interactive"
              type="button"
              data-audio-card-details
              class="block max-w-full break-words text-left underline-offset-2 hover:underline"
              :aria-label="t('hardware.openDetails', { name: card.name })"
              @click="emit('open', card)"
            >
              {{ card.name }}
            </button>
            <span v-else class="block break-words">{{ card.name }}</span>
          </h3>
        </div>
      </header>

      <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt class="text-text-secondary">{{ t('hardware.audio.driver') }}</dt>
        <dd class="break-words font-mono text-xs text-text-primary">{{ card.driver || t('hardware.audio.noDriver') }}</dd>
        <dt class="text-text-secondary">{{ t('hardware.audio.activeProfile') }}</dt>
        <dd class="break-words font-mono text-xs text-text-primary">{{ card.active_profile || t('hardware.audio.profileNone') }}</dd>
        <dt class="text-text-secondary">{{ t('hardware.audio.profileCount') }}</dt>
        <dd class="tabular-nums text-text-primary">{{ card.profile_count }}</dd>
        <dt class="text-text-secondary">{{ t('hardware.audio.portCount') }}</dt>
        <dd class="tabular-nums text-text-primary">{{ card.port_count }}</dd>
      </dl>

      <div v-if="card.endpoint_refs.length" class="space-y-1.5">
        <p class="text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.audio.endpoints') }}
        </p>
        <div class="flex flex-wrap gap-1.5">
          <component
            :is="interactive ? 'button' : 'span'"
            v-for="ref in card.endpoint_refs"
            :key="`${ref.kind}-${ref.name}`"
            :type="interactive ? 'button' : undefined"
            data-endpoint-ref
            :data-ref-kind="ref.kind"
            class="inline-flex max-w-full items-center gap-1 rounded-(--radius-badge) bg-surface-secondary px-2 py-0.5 text-xs text-text-primary"
            :class="interactive ? 'hover:bg-surface-card' : ''"
            @click="interactive ? emit('openEndpoint', ref) : undefined"
          >
            <span class="text-text-secondary">{{ ref.kind === 'sink' ? t('hardware.audio.output') : t('hardware.audio.input') }}</span>
            <span class="truncate font-mono">{{ ref.name }}</span>
          </component>
        </div>
      </div>
    </div>

    <template v-if="detail">
      <section v-if="detail.profiles.length" data-profile-list class="mt-4 border-t border-border pt-3">
        <p class="mb-1.5 text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.audio.profiles') }}
        </p>
        <ul class="space-y-1.5">
          <li
            v-for="profile in detail.profiles"
            :key="profile.name"
            data-profile-row
            class="flex flex-wrap items-center gap-x-2 gap-y-1"
          >
            <div class="min-w-0 flex-1">
              <p class="truncate text-sm text-text-primary">{{ profile.description || profile.name }}</p>
              <p class="truncate font-mono text-xs text-text-secondary">{{ profile.name }}</p>
            </div>
            <SbBadge v-if="profile.name === detail.active_profile" data-profile-active tone="info" size="sm">
              {{ t('hardware.audio.profileActive') }}
            </SbBadge>
            <SbBadge
              :data-profile-state="profileAvailable(profile)"
              :tone="availabilityTone(profileAvailable(profile))"
              size="sm"
              dot
            >
              {{ profileLabel(profile) }}
            </SbBadge>
          </li>
        </ul>
      </section>

      <section v-if="detail.ports.length" data-card-ports class="mt-4 border-t border-border pt-3">
        <p class="mb-1.5 text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.audio.ports') }}
        </p>
        <PortList :ports="detail.ports" />
      </section>
    </template>
  </SbCard>
</template>
