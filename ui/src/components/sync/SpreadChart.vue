<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatMs } from '@/sync/format'

/**
 * Two horizontal bars on one scale: the spread between the speakers before, and after
 * (measured in a check round, or the planner's prediction). Values are labelled directly,
 * so the colours never carry the meaning alone.
 */
const props = defineProps<{
  before: number | null
  after: number | null
  /** ``after`` is the planner's prediction, not a measurement. */
  predicted: boolean
}>()

const { t } = useI18n()

const scale = computed(() => Math.max(props.before ?? 0, props.after ?? 0, 1))
const width = (v: number | null) => (v == null ? 0 : Math.max(1, (v / scale.value) * 100))
const text = (v: number | null) => (v == null ? t('sync.proposal.noData') : t('sync.proposal.ms', { ms: formatMs(v) }))

const bars = computed(() => [
  { key: 'before', label: t('sync.proposal.spreadBefore'), value: props.before, fill: 'bg-text-secondary/60' },
  {
    key: 'after',
    label: props.predicted ? t('sync.proposal.spreadPredicted') : t('sync.proposal.spreadAfter'),
    value: props.after,
    fill: 'bg-primary',
  },
])
const label = computed(() =>
  t('sync.proposal.chartLabel', {
    before: props.before == null ? t('sync.proposal.noData') : formatMs(props.before),
    after: props.after == null ? t('sync.proposal.noData') : formatMs(props.after),
  }),
)
</script>

<template>
  <figure data-spread-chart role="img" :aria-label="label" class="flex flex-col gap-2">
    <figcaption class="text-sm font-medium text-text-secondary">{{ t('sync.proposal.chart') }}</figcaption>
    <div
      v-for="bar in bars"
      :key="bar.key"
      :data-bar="bar.key"
      class="grid grid-cols-[minmax(6rem,auto)_1fr_auto] items-center gap-3 text-sm"
      aria-hidden="true"
    >
      <span class="text-text-secondary">{{ bar.label }}</span>
      <span class="h-3 rounded-sm bg-surface-secondary">
        <span class="block h-full rounded-r-[4px] rounded-l-sm" :class="bar.fill" :style="{ width: `${width(bar.value)}%` }" />
      </span>
      <span class="tabular-nums text-text-primary">{{ text(bar.value) }}</span>
    </div>
  </figure>
</template>
