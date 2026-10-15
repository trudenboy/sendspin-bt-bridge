<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbButton, SbDialog } from '@/kit'
import type { SyncMember, SyncProfile } from '@/api/types'

const props = defineProps<{ profile: SyncProfile | null; members: SyncMember[]; busy?: boolean }>()
const emit = defineEmits<{ apply: [] }>()
const open = defineModel<boolean>({ default: false })

const { t } = useI18n()

/** What applying the profile changes: one row per group member the profile names. */
const rows = computed(() => {
  const delays = props.profile?.delays ?? {}
  return props.members
    .filter((m) => delays[m.ma_player_id] !== undefined)
    .map((m) => ({
      id: m.ma_player_id,
      name: m.name,
      adjustable: m.adjustable,
      before: m.delay_ms == null ? '—' : String(m.delay_ms),
      after: String(delays[m.ma_player_id]),
      changed: m.delay_ms !== delays[m.ma_player_id],
    }))
})

/** Players the profile names that are no longer in the group: skipped on apply. */
const notInGroup = computed(() => {
  const present = new Set(props.members.map((m) => m.ma_player_id))
  return Object.keys(props.profile?.delays ?? {}).filter((id) => !present.has(id))
})
</script>

<template>
  <SbDialog v-model="open" :title="profile ? t('sync.profiles.applyTitle', { name: profile.name }) : ''" size="lg">
    <div v-if="profile" data-profile-diff>
      <p v-if="rows.length === 0" class="text-sm text-text-secondary">{{ t('sync.profiles.noChanges') }}</p>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="text-left text-xs text-text-secondary">
            <th scope="col" class="py-1 pr-3 font-medium">{{ t('sync.profiles.member') }}</th>
            <th scope="col" class="py-1 text-right font-medium">{{ t('sync.profiles.change') }}</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          <tr v-for="row in rows" :key="row.id" :data-diff-row="row.id">
            <th scope="row" class="py-2 pr-3 text-left font-normal text-text-primary">{{ row.name }}</th>
            <td class="py-2 text-right tabular-nums">
              <SbBadge v-if="!row.adjustable" tone="warning" size="sm">{{ t('sync.origin.other') }}</SbBadge>
              <span v-else :class="row.changed ? 'text-text-primary' : 'text-text-secondary'">{{
                `${row.before} → ${row.after}`
              }}</span>
              <span v-if="row.adjustable && !row.changed" class="ml-2 text-xs text-text-secondary">{{
                t('sync.profiles.unchanged')
              }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="notInGroup.length" data-not-in-group class="mt-3 text-sm text-text-secondary">
        {{ t('sync.profiles.notInGroup', { names: notInGroup.map((id) => t('sync.profiles.playerId', { id })).join(', ') }) }}
      </p>
      <div class="mt-4 flex justify-end gap-3">
        <SbButton variant="outline" data-cancel @click="open = false">{{ t('sync.profiles.cancel') }}</SbButton>
        <SbButton data-confirm :loading="busy" @click="emit('apply')">{{ t('sync.profiles.confirmApply') }}</SbButton>
      </div>
    </div>
  </SbDialog>
</template>
