<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbBadge } from '@/kit'
import { useNow } from '@/composables/useNow'
import type { SyncMember } from '@/api/types'

defineProps<{ members: SyncMember[] }>()

const { t, locale } = useI18n()
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
  <ul v-else class="divide-y divide-border">
    <li
      v-for="m in members"
      :key="m.ma_player_id"
      data-member
      class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2"
    >
      <span class="min-w-0 flex-1 truncate text-text-primary">{{ m.name }}</span>
      <SbBadge :tone="m.adjustable ? 'neutral' : 'warning'" size="sm">
        {{ t(`sync.origin.${m.adjustable ? m.origin : 'other'}`) }}
      </SbBadge>
      <span
        class="w-16 text-right text-sm tabular-nums text-text-secondary"
        :aria-label="m.adjustable && m.delay_ms != null ? undefined : t('sync.noDelay')"
      >
        {{ m.adjustable && m.delay_ms != null ? t('sync.delay', { ms: m.delay_ms }) : '—' }}
      </span>
      <span v-if="m.last_measured_at" class="w-full text-xs text-text-secondary sm:w-auto">
        {{ t('sync.measuredAgo', { age: age(m.last_measured_at) }) }}
      </span>
    </li>
  </ul>
</template>
