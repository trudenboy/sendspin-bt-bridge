<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbBadge } from '@/kit'
import { availabilityTone, portAvailability, type PortAvailability } from '@/utils/audioEndpoint'
import type { AudioCardPort } from '@/api/types'

/** A card's ports, read-only: active, and available / unavailable / unknown kept apart. */
defineProps<{
  ports: AudioCardPort[]
}>()

const { t } = useI18n()

const labels: Record<PortAvailability, string> = {
  available: 'hardware.audio.portAvailable',
  unavailable: 'hardware.audio.portUnavailable',
  unknown: 'hardware.audio.portUnknown',
}

function label(port: AudioCardPort) {
  return t(labels[portAvailability(port.available)])
}
</script>

<template>
  <ul data-port-list class="divide-y divide-border rounded-(--radius-card) border border-border">
    <li
      v-for="port in ports"
      :key="port.name"
      data-port-row
      class="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2"
    >
      <div class="min-w-0 flex-1">
        <p class="truncate text-sm text-text-primary">
          {{ port.description || port.name }}
        </p>
        <p class="truncate font-mono text-xs text-text-secondary">{{ port.name }}</p>
      </div>
      <SbBadge v-if="port.active" data-port-active tone="info" size="sm">
        {{ t('hardware.audio.portActive') }}
      </SbBadge>
      <SbBadge
        :data-port-state="portAvailability(port.available)"
        :tone="availabilityTone(portAvailability(port.available))"
        size="sm"
        dot
      >
        {{ label(port) }}
      </SbBadge>
    </li>
  </ul>
</template>
