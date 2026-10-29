<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { TriangleAlert } from 'lucide-vue-next'
import { SbButton } from '@/kit'
import type { SyncMember } from '@/api/types'
import { speakerName } from '@/sync/names'

/**
 * What would stop a measurement, said before the music is stopped: a speaker that cannot be muted
 * (then no other speaker can be heard alone), one Music Assistant reports unavailable, one at 0 %,
 * one muted by someone. Each offers a way on: skip that speaker when measuring, or tune by ear.
 */
const props = defineProps<{
  members: SyncMember[]
  /** Speakers the operator chose to skip when measuring. */
  skipped?: string[]
}>()
const emit = defineEmits<{ skip: [member: string, skip: boolean]; manual: [] }>()

const { t } = useI18n()
const nameOf = (m: SyncMember) => speakerName(m.name, m.ma_player_id)

type Issue = { member: SyncMember; kind: 'unmutable' | 'unavailable' | 'silent' | 'muted' }
const issues = computed<Issue[]>(() => {
  const out: Issue[] = []
  const several = props.members.length > 1
  for (const m of props.members) {
    if (m.available === false) out.push({ member: m, kind: 'unavailable' })
    else if (m.can_mute === false && several) out.push({ member: m, kind: 'unmutable' })
    else if (m.adjustable && m.muted === true) out.push({ member: m, kind: 'muted' })
    else if (m.adjustable && m.volume === 0) out.push({ member: m, kind: 'silent' })
  }
  return out
})
const isSkipped = (id: string) => (props.skipped ?? []).includes(id)
</script>

<template>
  <div
    v-if="issues.length"
    data-readiness
    role="note"
    class="flex flex-col gap-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
  >
    <p class="flex items-center gap-2 font-medium">
      <TriangleAlert class="size-4 shrink-0 text-warning-text" aria-hidden="true" />{{ t('sync.ready.title') }}
    </p>
    <ul class="flex flex-col gap-2">
      <li v-for="i in issues" :key="i.member.ma_player_id" :data-issue="i.kind" :data-issue-member="i.member.ma_player_id" class="flex flex-col gap-1">
        <span>{{ t(`sync.ready.${i.kind}`, { name: nameOf(i.member) }) }}</span>
        <span class="flex flex-wrap gap-2">
          <SbButton
            v-if="i.kind !== 'unmutable' && i.member.adjustable"
            type="button"
            size="sm"
            variant="outline"
            :data-skip-member="i.member.ma_player_id"
            :aria-pressed="isSkipped(i.member.ma_player_id)"
            @click="emit('skip', i.member.ma_player_id, !isSkipped(i.member.ma_player_id))"
          >
            {{ isSkipped(i.member.ma_player_id) ? t('sync.ready.skipped', { name: nameOf(i.member) }) : t('sync.ready.skip', { name: nameOf(i.member) }) }}
          </SbButton>
          <SbButton v-if="i.kind === 'unmutable'" type="button" size="sm" variant="outline" data-ready-manual @click="emit('manual')">
            {{ t('sync.actions.manual') }}
          </SbButton>
        </span>
      </li>
    </ul>
  </div>
</template>
