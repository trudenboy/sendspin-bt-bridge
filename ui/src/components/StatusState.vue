<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge } from '@/kit'
import { technicalDetails } from '@/composables/useTechnicalDetails'
import { describeStatus, type StatusKind } from '@/composables/useStatusState'
import type { LinkStatus } from '@/api/types'

/**
 * One of the three named states (Audio link, Music Assistant account,
 * Bluetooth connection) drawn the same way wherever it appears. ``chip`` is a
 * compact badge for cards and rows (the name is in the tooltip and for screen
 * readers); ``row`` and ``full`` write the name out for the drawer, the runtime
 * row and Diagnostics.
 */
const props = withDefaults(
  defineProps<{
    kind: StatusKind
    status: Pick<LinkStatus, 'state' | 'tone' | 'reason'> | null | undefined
    variant?: 'chip' | 'full'
    /** On cards and rows: show only when not OK, unless technical details are on. */
    onlyIfNotOk?: boolean
  }>(),
  { variant: 'chip', onlyIfNotOk: false },
)

const { t, te } = useI18n()
const look = computed(() => (props.status ? describeStatus(props.kind, props.status, { t: (k, p) => t(k, p ?? {}), te }) : null))
</script>

<template>
  <span
    v-if="look && (!onlyIfNotOk || technicalDetails || look.tone !== 'success')"
    class="inline-flex max-w-full"
    :data-status-state="kind"
    :data-state="look.state"
    :title="look.tooltip"
  >
    <SbBadge :tone="look.tone" size="sm" class="max-w-full">
      <component :is="look.icon" class="size-3 shrink-0" aria-hidden="true" />
      <span v-if="variant === 'full'" class="min-w-0 truncate">{{ look.full }}</span>
      <template v-else>
        <span class="sr-only">{{ look.name }}: </span>
        <span class="min-w-0 truncate">{{ look.label }}</span>
      </template>
    </SbBadge>
  </span>
</template>
