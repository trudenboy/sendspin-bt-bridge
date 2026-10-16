<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbBadge } from '@/kit'
import { useNow } from '@/composables/useNow'
import type { SyncMember } from '@/api/types'
import { speakerName } from '@/sync/names'

defineProps<{ members: SyncMember[] }>()

const { t, locale } = useI18n()

/** What would get in the way of measuring this member (shown before anything is touched). */
function badges(m: SyncMember): string[] {
  const out: string[] = []
  if (m.available === false) out.push('unavailable')
  if (m.can_mute === false) out.push('unmutable')
  if (m.muted === true) out.push('muted')
  if (m.volume === 0) out.push('silent')
  return out
}
const now = useNow()

const STEPS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['day', 86_400],
  ['hour', 3_600],
  ['minute', 60],
]

/** "5 minutes ago", in the UI's language; refreshed by ``useNow``. */
function age(iso: string): string {
  const at = Date.parse(iso)
  if (Number.isNaN(at)) return ''
  const seconds = Math.max(0, Math.round((now.value - at) / 1000))
  const fmt = new Intl.RelativeTimeFormat(locale.value, { numeric: 'auto' })
  for (const [unit, size] of STEPS) {
    if (seconds >= size) return fmt.format(-Math.floor(seconds / size), unit)
  }
  return fmt.format(-seconds, 'second')
}
</script>

<template>
  <p v-if="members.length === 0" class="text-sm text-text-secondary">{{ t('sync.noMembers') }}</p>
  <template v-else>
  <p data-sign-hint class="mb-1 text-xs text-text-secondary">{{ t('sync.delayHeading') }}</p>
  <ul class="divide-y divide-border">
    <li
      v-for="m in members"
      :key="m.ma_player_id"
      data-member
      class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2"
    >
      <span :title="m.name" class="min-w-0 flex-1 truncate text-text-primary">{{ speakerName(m.name, m.ma_player_id) }}</span>
      <SbBadge v-if="!m.adjustable" tone="warning" size="sm">{{ t('sync.origin.other') }}</SbBadge>
      <SbBadge v-for="b in badges(m)" :key="b" :data-badge="b" tone="warning" size="sm">{{ t(`sync.badge.${b}`) }}</SbBadge>
      <span class="w-16 text-right text-sm tabular-nums text-text-secondary">
        <template v-if="m.adjustable && m.delay_ms != null">{{ t('sync.delay', { ms: m.delay_ms }) }}</template>
        <template v-else><span aria-hidden="true">—</span><span class="sr-only">{{ t('sync.noDelay') }}</span></template>
      </span>
      <span v-if="m.last_measured_at" class="w-full text-xs text-text-secondary sm:w-auto">
        {{ t('sync.measuredAgo', { age: age(m.last_measured_at) }) }}
      </span>
    </li>
  </ul>
  </template>
</template>
